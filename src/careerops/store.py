"""Local durable state. No imports of legacy workers or external side effects."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from careerops.policy import POLICY_VERSION, default_profile, default_settings, evaluate_job, extract_job, select_shortlist
from careerops.tracker import ConflictError, application_write_lock, ensure_tracker, update_application


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def merge(base, patch):
    result = copy.deepcopy(base)
    for key, value in patch.items():
        result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else copy.deepcopy(value)
    return result


def canonical_url(value):
    if not value:
        return ""
    u = urlsplit(value)
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        raise ValueError("Use an ordinary HTTP or HTTPS application URL without credentials.")
    query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"gh_src", "lever-source", "trackingid"}]
    return urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path.rstrip("/") or "/", urlencode(sorted(query)), ""))


def validate_settings(settings):
    cv_review = settings.get("cv_review", {})
    if not isinstance(cv_review, dict):
        raise ValueError("CV review settings must be an object.")
    rounds = cv_review.get("max_rounds", 2)
    if isinstance(rounds, bool) or not isinstance(rounds, int) or not 1 <= rounds <= 5:
        raise ValueError("Automatic CV review rounds must be an integer between 1 and 5.")
    strategy = settings.get("strategy", {})
    for key in ("shortlist_size", "stretch_size", "per_company"):
        value = strategy.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 50:
            raise ValueError(f"Strategy {key} must be an integer between 0 and 50.")
    if "weights" in strategy:
        weights = strategy["weights"]
        if not isinstance(weights, dict) or not weights or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in weights.values()) or abs(sum(weights.values()) - 1) > .001:
            raise ValueError("Strategy weights must be finite non-negative fractions that sum to 1.")
    if settings.get("schedules_enabled"):
        raise ValueError("Scheduling is not enabled in this local release. Run searches explicitly.")
    for name, n in settings.get("queue", {}).items():
        if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= 20:
            raise ValueError(f"Queue limit {name} must be an integer between 0 and 20.")
    for name, amount in settings.get("london", {}).items():
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or not 0 <= amount <= 10000000:
            raise ValueError(f"London setting {name} must be a non-negative number.")
    exceptional = settings.get("exceptional", {})
    if "base_gbp" in exceptional:
        # Scoring compares every known salary with this trigger, so a blank
        # value would break rescoring on every later start.
        amount = exceptional["base_gbp"]
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or not 0 <= amount <= 10000000:
            raise ValueError("Verified annual base trigger must be a non-negative number.")
    for section in settings.get("weights", {}).values():
        if not isinstance(section, dict) or any(not isinstance(x, (int, float)) or x < 0 or x > 1 for x in section.values()) or abs(sum(section.values()) - 1) > .001:
            raise ValueError("Each scoring weight group must contain fractions that sum to 1.")
    for lane in settings.get("thresholds", {}).values():
        if any(not isinstance(x, (int, float)) or not 0 <= x <= 100 for x in lane.values()):
            raise ValueError("Fit and priority thresholds must be between 0 and 100.")
    for mode in ("normal", "deep", "bootstrap"):
        limits = settings.get("search", {}).get(mode, {})
        for name, value in limits.items():
            if name in {"max_pages", "max_jobs", "max_queries", "max_turns", "concurrency", "timeout_seconds", "max_retries"}:
                upper = 8 if name == "concurrency" else 3 if name == "max_retries" else 600 if name == "timeout_seconds" else 1000
                if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= upper:
                    raise ValueError(f"{mode} {name} must be an integer between 0 and {upper}.")
    for config in (settings.get("providers", {}), settings.get("search", {}).get("web", {})):
        amount = config.get("budget_usd", 0)
        if not isinstance(amount, (int, float)) or not 0 <= amount <= 100:
            raise ValueError("Per-run budget must be an explicit amount between $0 and $100.")
    def validate_coverage(value):
        if not isinstance(value, dict):
            raise ValueError('Coverage settings must be named budgets.')
        for key, amount in value.items():
            if isinstance(amount, dict):
                validate_coverage(amount)
            elif key.endswith('_requests') or key in {'query_objective','board_objective','per_host_limit','timeout_seconds','max_ai_reviews','max_jobs'}:
                upper = 1800 if key == 'timeout_seconds' else 5000 if key == 'max_jobs' else 1000
                if isinstance(amount, bool) or not isinstance(amount, int) or not 0 <= amount <= upper:
                    raise ValueError(f'Coverage {key} must be an integer between 0 and {upper}.')
    validate_coverage(settings.get('search', {}).get('coverage', {}))
    validate_coverage(settings.get('search', {}).get('scope_budgets', {}))
    def check_keys(d):
        if isinstance(d, dict):
            for key, value in d.items():
                if any(token in key.lower() for token in ("api_key", "password", "secret", "access_token")):
                    raise ValueError("Keep credentials in server environment variables, outside saved settings.")
                check_keys(value)
        elif isinstance(d, list):
            for value in d:
                check_keys(value)
    check_keys(settings)
    return settings


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        if self.path.exists():
            with sqlite3.connect(self.path) as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version > 3:
                    raise ValueError("Database is newer than this application; do not downgrade it.")
                has_cv_runs = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cv_runs'").fetchone()
                if version < 3 or not has_cv_runs:
                    self.backup()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, identity TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, identity TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, job_id INTEGER, kind TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS shortlist (job_id INTEGER PRIMARY KEY, position INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS companies (id INTEGER PRIMARY KEY, identity TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS materials (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, cache_key TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews (cache_key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS employer_boards (identity TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS preparation_previews (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS preparation_batches (id INTEGER PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cv_bases (cache_key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS base_documents (id INTEGER PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cv_runs (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, idempotency_key TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_canonical_url ON jobs(json_extract(data, '$.canonical_url'));
                PRAGMA user_version=3;
            """)
            for key, value in (("settings", default_settings()), ("profile", default_profile())):
                db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, encode(value)))
        self.path.chmod(0o600)
        # A restart cannot leave an apparently running search with no owner.
        for run in self.runs():
            if run["status"] in {"running", "cancelling"}:
                self.update_run(run["id"], {"status": "interrupted", "message": "App restarted; partial results are preserved. Resume explicitly."})
        for batch in self.preparation_batches():
            if batch["status"] in {"running", "cancelling"}:
                batch["status"] = "interrupted"
                for item in batch["items"]:
                    if item["status"] == "running":
                        item["status"] = "pending"
                self.update_preparation_batch(batch["id"], batch)
        self.migrate_source_metadata()
        self.migrate_tracker()
        from careerops import policy
        upgrade = getattr(policy, "upgrade_professional_settings", None)
        if upgrade:
            current = self.meta("settings", {})
            updated = upgrade(current)
            if updated != current:
                self.put_meta("settings", updated, version=True)
        self.rescore()
        if upgrade and updated != current:
            self.refresh_shortlist(replace=True)
        if self.meta('inventory_policy_version') != 'overseas-volume-v1':
            self.put_meta('inventory_policy_version', 'overseas-volume-v1', version=True)
        if self.meta('evaluation_policy_version') != POLICY_VERSION:
            self.put_meta('evaluation_policy_version', POLICY_VERSION, version=True)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def backup(self):
        destination = self.path.parent / "backups" / f"careerops-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}.sqlite3"
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.lock, self.connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)
        destination.chmod(0o600)
        return destination

    def meta(self, key, fallback=None):
        with self.connect() as db:
            row = db.execute("SELECT data FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else fallback

    def put_meta(self, key, value, version=False):
        with self.lock, self.connect() as db:
            db.execute("INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data", (key, encode(value)))
            if version:
                db.execute("INSERT INTO versions(kind,data,created_at) VALUES (?,?,?)", (key, encode(value), now()))

    def settings(self):
        settings = merge(default_settings(), self.meta("settings", {}))
        settings['policy_version'] = POLICY_VERSION
        settings.setdefault('cv_review', {'max_rounds': 2})
        return settings

    def profile(self):
        return self.meta("profile")

    def update_settings(self, patch):
        settings = validate_settings(merge(self.settings(), patch))
        self.put_meta("settings", settings, version=True)
        self.rescore()
        return settings

    def update_profile(self, profile):
        safe = {k: v for k, v in profile.items() if k in {
            "name", "location", "drives", "availability", "qualifications", "skills", "domains",
            "evidence", "languages", "work_authorisation", "citizenships", "employment", "projects",
            "education", "research", "contact", "unresolved", "version", "source_hashes", "qualifications_not_held"}}
        if "name" in safe and not isinstance(safe["name"], str):
            raise ValueError("Profile name must be text.")
        for key in ("skills", "domains", "evidence", "employment", "projects", "education", "research"):
            if key in safe and not isinstance(safe[key], list):
                raise ValueError(f"Profile {key} must be a list.")
        updated = merge(self.profile(), safe)
        updated["version"] = digest({k: v for k, v in updated.items() if k != "version"})[:16]
        self.put_meta("profile", updated, version=True)
        self.rescore()
        return updated

    def jobs(self):
        with self.connect() as db:
            sources = {}
            for row in db.execute('SELECT job_id,data FROM sources'):
                sources.setdefault(row['job_id'], []).append(json.loads(row['data']))
            return [self.universe_fields(dict(json.loads(r["data"]), id=r["id"]), sources.get(r['id'], [])) for r in db.execute("SELECT id,data FROM jobs ORDER BY id DESC")]

    @staticmethod
    def universe_fields(job, sources=None):
        """Additive defaults; missing historical discovery facts remain unknown."""
        job.setdefault("discovered_at", job.get("first_seen"))
        job["canonical_url"] = canonical_url(job.get("url", ""))
        job.setdefault("hidden", job.get("status") == "dismissed")
        job.setdefault("hidden_at", None)
        job.setdefault("source_queries", [job["source_query"]] if job.get("source_query") else [])
        job.setdefault("source_types", [job["source"]] if job.get("source") else [])
        job.setdefault("source_urls", [job["url"]] if job.get("url") else [])
        for source in sources if sources is not None else job.get('sources', []):
            for plural, singular in (('source_types', 'type'), ('source_urls', 'url'), ('source_queries', 'source_query')):
                value = source.get(singular)
                if isinstance(value, str) and value and value not in job[plural]:
                    job[plural].append(value)
        return job

    def get_job(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError("Job not found")
            job = self.universe_fields(dict(json.loads(row[0]), id=int(job_id)))
            job["sources"] = [json.loads(r[0]) for r in db.execute("SELECT data FROM sources WHERE job_id=? ORDER BY id", (job_id,))]
            job["history"] = [dict(kind=r["kind"], data=json.loads(r["data"]), created_at=r["created_at"]) for r in db.execute("SELECT * FROM events WHERE job_id=? ORDER BY id DESC", (job_id,))]
            job["materials"] = [dict(json.loads(r["data"]), id=r["id"]) for r in db.execute("SELECT id,data FROM materials WHERE job_id=? ORDER BY id DESC", (job_id,))]
        return self.universe_fields(job)

    def _save_job(self, db, job):
        job = self.universe_fields(job)
        data = {k: v for k, v in job.items() if k not in {"history", "materials", "sources"}}
        db.execute("UPDATE jobs SET data=? WHERE id=?", (encode(data), job["id"]))

    @staticmethod
    def _url_job_row(db, url):
        """Match actual retained URLs, including pre-migration identity aliases."""
        if not url:
            return None
        row = db.execute("SELECT * FROM jobs WHERE json_extract(data, '$.canonical_url')=?", (url,)).fetchone()
        if row:
            return row
        row = db.execute('SELECT jobs.* FROM jobs JOIN sources ON sources.job_id=jobs.id WHERE sources.identity=?', ('url:' + url,)).fetchone()
        if row:
            return row
        # Only unmigrated historical aliases need normalisation in Python.
        # Current canonical URLs and source aliases use SQLite indexes.
        for row in db.execute("SELECT id,data FROM jobs WHERE json_extract(data, '$.canonical_url') IS NULL"):
            data = json.loads(row["data"])
            if data.get("url") and canonical_url(data["url"]) == url:
                return db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
        for row in db.execute("SELECT job_id,data FROM sources WHERE identity NOT LIKE 'url:%'"):
            data = json.loads(row["data"])
            if data.get("url") and canonical_url(data["url"]) == url:
                return db.execute("SELECT * FROM jobs WHERE id=?", (row["job_id"],)).fetchone()
        return None

    def find_job_by_url(self, url):
        with self.connect() as db:
            row = self._url_job_row(db, canonical_url(url))
        return self.get_job(row["id"]) if row else None

    def record_query_source(self, url, query, provider, run_id):
        """Attach observed query provenance without refreshing or replacing facts."""
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._url_job_row(db, canonical_url(url))
            if not row:
                return False
            job = self.universe_fields(dict(json.loads(row['data']), id=row['id']))
            if query not in job['source_queries']:
                job['source_queries'].append(query)
                self._save_job(db, job)
                db.execute('INSERT INTO events(job_id,kind,data,created_at) VALUES (?,?,?,?)',
                    (job['id'], 'source_query_seen', encode({'query': query, 'provider': provider, 'run_id': run_id, 'url': url}), now()))
            return True

    def migrate_tracker(self):
        if self.meta("tracker_migration_version", 0) >= 1:
            return
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT * FROM jobs").fetchall():
                job = ensure_tracker(dict(json.loads(row["data"]), id=row["id"]))
                self._save_job(db, job)
                if row["identity"].startswith("url:") and job.get("url"):
                    new_identity = "url:" + canonical_url(job["url"])
                    conflict = db.execute("SELECT id FROM jobs WHERE identity=? AND id<>?", (new_identity, row["id"])).fetchone()
                    if conflict:
                        new_identity = f"legacy-url:{row['id']}"
                    db.execute("UPDATE jobs SET identity=? WHERE id=?", (new_identity, row["id"]))
            for row in db.execute("SELECT * FROM sources").fetchall():
                source = json.loads(row["data"])
                if row["identity"].startswith("url:") and source.get("url"):
                    new_identity = "url:" + canonical_url(source["url"])
                    conflict = db.execute("SELECT id FROM sources WHERE identity=? AND id<>?", (new_identity, row["id"])).fetchone()
                    if conflict:
                        new_identity = f"legacy-source:{row['id']}"
                    db.execute("UPDATE sources SET identity=? WHERE id=?", (new_identity, row["id"]))
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('tracker_migration_version', '1')")

    def upsert_job(self, supplied, *, trusted_history=False, refresh=True):
        with self.lock:
            return self._upsert_job(supplied, trusted_history=trusted_history, refresh=refresh)

    def _upsert_job(self, supplied, trusted_history=False, refresh=True):
        supplied = copy.deepcopy(supplied)
        text = supplied.get("description") or supplied.pop("text", "")
        manual_metadata = not text.strip() and supplied.get("title") and supplied.get("company") and (supplied.get("url") or supplied.get("source") in {"manual_entry", "manual_review"})
        if (not text.strip() and not manual_metadata) or len(text) > 200000:
            raise ValueError("Import a job description, or supply a manual job URL, title and company.")
        if manual_metadata:
            supplied["last_verified"] = None
        supplied.pop("evaluation", None)
        supplied.pop("id", None)
        supplied.pop("materials", None)
        for local_field in ("application", "bookmarked", "bookmarked_at", "manually_saved", "hidden", "hidden_at"):
            supplied.pop(local_field, None)
        if supplied.get("status") == "closed" and not trusted_history:
            supplied["vacancy_status"] = "closed"
        job = extract_job(text, supplied)
        job["description"] = text
        job["title"] = job.get("title") or "Role title needs confirmation"
        job["company"] = job.get("company") or "Employer needs confirmation"
        url = canonical_url(job.get("url", ""))
        company = re.sub(r"\W+", "", job["company"].lower())
        fingerprint = digest([company, job["title"].casefold().strip(), str(job.get("location", "")).casefold().strip(), re.sub(r"\s+", " ", text.strip())])
        source_fingerprint = fingerprint
        requisition = job.get("requisition_id")
        identity = f"req:{company}:{requisition}" if requisition else f"url:{url}" if url else f"content:{fingerprint}"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE identity=?", (identity,)).fetchone()
            if row and identity.startswith("url:") and canonical_url(json.loads(row["data"]).get("url", "")) != url:
                row = None
            if not row and url:
                row = self._url_job_row(db, url)
            if not row and not trusted_history and text.strip():
                # Boilerplate descriptions are not proof that distinct URLs are
                # one vacancy. Cross-source merging requires a shared requisition.
                candidate = db.execute("SELECT * FROM jobs WHERE fingerprint=?", (fingerprint,)).fetchone()
                if candidate:
                    old = json.loads(candidate["data"])
                    old_req = old.get("requisition_id")
                    old_url, new_url = urlsplit(canonical_url(old.get("url", ""))), urlsplit(url)
                    distinct_urls = bool(old_url.netloc and new_url.netloc and old_url != new_url)
                    if not distinct_urls and not (old_req and requisition and str(old_req) != str(requisition)):
                        row = candidate
            stamp = now()
            prior = json.loads(row["data"]) if row else {}
            if prior and manual_metadata:
                job = copy.deepcopy(prior)
                fingerprint = row["fingerprint"]
            if trusted_history and prior.get("last_verified"):
                # History contributes sources and outcomes, never stale listing
                # excerpts in place of a newer authoritative retrieval.
                job = dict(prior, legacy=supplied.get("legacy") or prior.get("legacy"))
                fingerprint = row["fingerprint"]
            job["first_seen"] = prior.get("first_seen", supplied.get("first_seen", stamp) if trusted_history else stamp)
            job["last_seen"] = stamp
            job["content_fingerprint"] = fingerprint
            job["status"] = prior.get("status", supplied.get("status", "new") if trusted_history else "new")
            locally_acted = row and db.execute("SELECT 1 FROM events WHERE job_id=? AND kind IN ('save','skip','restore','mark_applied','interview','offer','close','materials_ready','bookmark','application','hidden','interested') LIMIT 1", (row["id"],)).fetchone()
            if trusted_history and not locally_acted and supplied.get("status") in {"applied", "interview", "offer", "closed", "dismissed"}:
                job["status"] = supplied["status"]
            for key in ("notes", "follow_up_date", "applied_at", "feedback", "manually_saved", "bookmarked", "bookmarked_at", "application", "preparation_status", "application_opened_at", "reviewed_material_id", "hidden", "hidden_at", "discovered_at"):
                if key in prior:
                    job[key] = prior[key]
            for plural, singular in (("source_queries", "source_query"), ("source_types", "source"), ("source_urls", "url")):
                values = [*prior.get(plural, []), *supplied.get(plural, [])]
                values += [prior.get(singular), supplied.get(singular)]
                if plural == "source_types":
                    values += [s.get("type") for s in supplied.get("sources", []) if isinstance(s, dict)]
                job[plural] = list(dict.fromkeys(v for v in values if isinstance(v, str) and v))
            if prior.get("legacy") and not job.get("legacy"):
                job["legacy"] = prior["legacy"]
            job = ensure_tracker(job)
            if trusted_history and not locally_acted and prior.get("application", {}).get("stage") == "not_started" and job["status"] in {"applied", "interview", "offer"}:
                job["application"].update(stage=job["status"], source="legacy_import", version=job["application"]["version"] + 1, updated_at=stamp,
                                          application_date=supplied.get("applied_at") or job["application"].get("application_date"))
            if row and row["fingerprint"] == fingerprint and prior.get("review"):
                job["review"] = prior["review"]
            job["evaluation"] = evaluate_job(job, self.profile(), self.settings())
            if row:
                job["id"] = row["id"]
                self._save_job(db, job)
                db.execute("UPDATE jobs SET fingerprint=? WHERE id=?", (fingerprint, job["id"]))
            else:
                cur = db.execute("INSERT INTO jobs(identity,fingerprint,data) VALUES (?,?,?)", (identity, fingerprint, encode(job)))
                job["id"] = cur.lastrowid
                self._save_job(db, job)
            source_items = supplied.get("sources", []) or [{"url": job.get("url", ""), "type": supplied.get("source", "manual"), "requisition_id": requisition}]
            for source in source_items:
                source = dict(source, first_seen=stamp, job_text=text, content_fingerprint=source_fingerprint)
                source_url = canonical_url(source.get("url", ""))
                source_id = f"url:{source_url}" if source_url else "content:" + digest([job["id"], {k: v for k, v in source.items() if k != "first_seen"}])
                db.execute("INSERT OR IGNORE INTO sources(job_id,identity,data) VALUES (?,?,?)", (job["id"], source_id, encode(source)))
            db.execute("INSERT INTO events(job_id,kind,data,created_at) VALUES (?,?,?,?)", (job["id"], "rediscovered" if row else "imported", encode({"source": supplied.get("source", "manual"), "source_query": supplied.get("source_query"), "run_id": supplied.get("discovery_run_id"), "content_changed": bool(row and row["fingerprint"] != fingerprint)}), stamp))
        if refresh and not getattr(self, "defer_selection", False):
            self.refresh_shortlist(replace=False)
        return {"job": self.get_job(job["id"]), "duplicate": bool(row)}

    def rescore(self):
        from careerops.policy import reextract_job
        with self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            profile, settings = self.profile(), self.settings()
            for job in self.jobs():
                job = reextract_job(job)
                job["evaluation"] = evaluate_job(job, profile, settings)
                self._save_job(db, job)
        self.refresh_shortlist(replace=False)

    def migrate_source_metadata(self):
        """Correct an earlier ATS interpretation without deleting history or facts."""
        revision = 'ats-advertised-locations-v3'
        if self.meta('source_metadata_revision') == revision:
            return
        jobs = self.jobs()
        if jobs:
            self.backup()
        with self.lock, self.connect() as db:
            types = {}
            for row in db.execute('SELECT job_id,data FROM sources'):
                types.setdefault(row['job_id'], set()).add(json.loads(row['data']).get('type'))
            counts = {'greenhouse_locations': 0, 'talent_pools': 0, 'ashby_office_frequency': 0}
            for job in jobs:
                changed = False
                if 'greenhouse' in types.get(job['id'], set()) and not job.get('location_extraction_version'):
                    job['ats_offices'] = job.get('available_locations', [])
                    job['available_locations'] = []
                    job['location_extraction_version'] = 2
                    counts['greenhouse_locations'] += 1
                    changed = True
                pattern = re.sub(r'[^a-z]', '', str(job.get('work_pattern', '')).lower())
                if 'ashby' in types.get(job['id'], set()) and pattern in {'hybrid','onsite'} and job.get('office_days') == 0:
                    job['office_days'] = None
                    job['evidence'] = [e for e in job.get('evidence', []) if not (e.get('field') == 'office_days' and e.get('quote') == '0' and e.get('status') == 'supplied')]
                    counts['ashby_office_frequency'] += 1
                    changed = True
                if not job.get('legacy') and job.get('opportunity_type') != 'talent_pool' and re.search(r'^(?:open|general|spontaneous|speculative|unsolicited) application\b|^(?:join (?:our|the) )?talent (?:pool|community|network)\b|^expressions? of interest\b', str(job.get('title', '')), re.I):
                    job['opportunity_type'] = 'talent_pool'
                    job['verification_status'] = 'pending'
                    counts['talent_pools'] += 1
                    changed = True
                if changed:
                    self._save_job(db, job)
                    db.execute('INSERT INTO events(job_id,kind,data,created_at) VALUES (?,?,?,?)',
                               (job['id'], 'source_metadata_corrected', encode({'revision': revision}), now()))
            # Registry country summaries are descriptive only. Clear inaccurate
            # Greenhouse summaries until a new receipt derives them from adverts.
            for row in db.execute('SELECT identity,data FROM employer_boards').fetchall():
                board = json.loads(row['data'])
                if board.get('type') == 'greenhouse':
                    board['previous_country_summary'] = board.get('countries', [])
                    board['countries'] = []
                    board['country_summary_status'] = 'refresh_required'
                    db.execute('UPDATE employer_boards SET data=? WHERE identity=?', (encode(board), row['identity']))
        self.put_meta('source_metadata_revision', revision, version=True)
        previous = self.meta('source_metadata_corrections', {})
        self.put_meta('source_metadata_corrections', {key: previous.get(key, 0) + value for key, value in counts.items()}, version=True)

    def refresh_shortlist(self, replace=True):
        with self.lock, self.connect() as db:
            current_ids = [r[0] for r in db.execute("SELECT job_id FROM shortlist ORDER BY position")]
            jobs = self.jobs()
            # A historical queue is an inventory, not evidence that a vacancy
            # is still open. Explicit save/reverification can return it to Today.
            jobs = [j for j in jobs if not j.get("legacy") or j.get("last_verified") or j.get("manually_saved")]
            settings = self.settings()
            if settings.get("strategy", {}).get("mode") == "professional_london_first":
                selected = select_shortlist(jobs, settings)
                db.execute("DELETE FROM shortlist")
                db.executemany("INSERT INTO shortlist VALUES (?,?)", [(j["id"], p) for p, j in enumerate(selected)])
                return selected
            by_id = {j["id"]: j for j in jobs}
            current = [by_id[i] for i in current_ids if i in by_id and by_id[i]["status"] in {"new", "saved", "materials_ready"} and by_id[i]["evaluation"].get("admitted")]
            if replace:
                # Unfinished saved/prepared applications still occupy the user's limited queue.
                pinned = [j for j in current if j["status"] in {"saved", "materials_ready"}]
            else:
                pinned = current
            # Selector may rank inputs: preserve existing membership by choosing additions only
            # after each pinned member is admitted under the current caps.
            selected = []
            for candidate in pinned:
                if len(select_shortlist(selected + [candidate], self.settings())) == len(selected) + 1:
                    selected.append(candidate)
            candidates = select_shortlist(jobs, self.settings())
            if len(candidates) < len(jobs):
                # Include lower-ranked candidates where an existing company/region consumes a cap.
                candidates += sorted([j for j in jobs if j not in candidates], key=lambda j: j["evaluation"].get("priority", 0), reverse=True)
            for job in candidates:
                if any(j["id"] == job["id"] for j in selected):
                    continue
                if len(select_shortlist(selected + [job], self.settings())) == len(selected) + 1:
                    selected.append(job)
            db.execute("DELETE FROM shortlist")
            db.executemany("INSERT INTO shortlist VALUES (?,?)", [(j["id"], p) for p, j in enumerate(selected)])
        return selected

    def shortlist(self):
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT job_id FROM shortlist ORDER BY position")]
        jobs = {j["id"]: j for j in self.jobs()}
        current = [jobs[i] for i in ids if i in jobs]
        return {"recommended": [j for j in current if not j["evaluation"].get("stretch")], "stretch": [j for j in current if j["evaluation"].get("stretch")]}

    def action(self, job_id, action, data=None, *, refresh=True):
        data = data or {}
        allowed = {"save", "skip", "mark_applied", "interview", "offer", "close", "restore", "materials_ready", "open", "notes", "feedback", "mark_reviewed", "bookmark", "application", "hidden", "interested"}
        if action not in allowed:
            raise ValueError("Unknown job action")
        with application_write_lock(self.path), self.lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError("Job not found")
            job = self.universe_fields(ensure_tracker(dict(json.loads(row[0]), id=int(job_id))))
            before = copy.deepcopy(job)
            event = {"actor": "user"}
            if action == "hidden":
                value = data.get("hidden")
                if not isinstance(value, bool):
                    raise ValueError("Supply hidden as true or false.")
                job["hidden"] = value
                job["hidden_at"] = now() if value else None
                if not value and job["status"] == "dismissed":
                    job["status"] = "saved" if job["bookmarked"] else "new"
            elif action == "interested":
                if job["application"]["stage"] == "not_started":
                    job, event = update_application(job, {"stage": "in_progress"}, expected_version=job["application"]["version"])
            elif action in {"application", "mark_applied", "interview", "offer"}:
                patch = data.get("application") if action == "application" else {"stage": {"mark_applied": "applied"}.get(action, action)}
                if isinstance(patch, dict) and patch.get("material_id") is not None:
                    material = db.execute("SELECT id FROM materials WHERE id=? AND job_id=?", (patch["material_id"], job_id)).fetchone()
                    if not material:
                        raise ValueError("The material reference must belong to this job.")
                expected = data.get("expected_version") if action == "application" else job["application"]["version"]
                job, event = update_application(job, patch, expected_version=expected, confirmed_applied=action == "mark_applied" or data.get("confirmed_applied") is True)
            elif action in {"bookmark", "save", "restore"}:
                value = data.get("bookmarked") if action == "bookmark" else True
                if not isinstance(value, bool):
                    raise ValueError("Supply bookmarked as true or false.")
                if value != job["bookmarked"]:
                    job["bookmarked_at"] = now() if value else None
                job["bookmarked"] = job["manually_saved"] = value
                if action == "restore":
                    job["hidden"], job["hidden_at"] = False, None
                if job["application"]["stage"] == "not_started" and (job["status"] in {"new", "saved"} or action == "restore"):
                    job["status"] = "saved" if value else "new"
            elif action in {"skip", "close"}:
                job["status"] = "dismissed" if action == "skip" else "closed"
                if action == "skip":
                    job["hidden"], job["hidden_at"] = True, now()
            elif action == "open":
                job["application_opened_at"] = now()
            elif action == "materials_ready":
                if job["application"]["stage"] == "not_started" and job["status"] not in {"closed", "dismissed"}:
                    job["status"] = "materials_ready"
                job["preparation_status"] = "draft_generated"
                job.pop("reviewed_material_id", None)
            elif action == "mark_reviewed":
                material = db.execute("SELECT id FROM materials WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
                if not material:
                    raise ValueError("Prepare a draft before marking it reviewed.")
                job["preparation_status"] = "reviewed_ready"
                job["reviewed_material_id"] = material["id"]
            if action != "application":
                patch = {key: data[key] for key in ("notes", "follow_up_date") if key in data}
                if patch:
                    job, note_event = update_application(job, patch, expected_version=job["application"]["version"])
                    event["application_edit"] = note_event
            if "feedback" in data:
                job["feedback"] = None if data["feedback"] is None else str(data["feedback"])[:20000]
            if "before" not in event:
                changed = {key for key in job if job.get(key) != before.get(key)}
                event.update(before={key: before.get(key) for key in changed}, after={key: job.get(key) for key in changed})
            self._save_job(db, job)
            db.execute("INSERT INTO events(job_id,kind,data,created_at) VALUES (?,?,?,?)", (job_id, action, encode({**data, **event}), now()))
        if refresh:
            self.refresh_shortlist(replace=False)
        return self.get_job(job_id)

    def companies(self):
        with self.connect() as db:
            return [dict(json.loads(r["data"]), id=r["id"]) for r in db.execute("SELECT * FROM companies ORDER BY id DESC")]

    def save_company(self, data):
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("Company name is required.")
        value = {"name": name, "url": canonical_url(data.get("url", "")), "notes": str(data.get("notes", "")), "status": "speculative", "created_at": now()}
        with self.lock, self.connect() as db:
            db.execute("INSERT INTO companies(identity,data) VALUES (?,?) ON CONFLICT(identity) DO UPDATE SET data=excluded.data", (name.casefold(), encode(value)))
        return value

    def runs(self):
        with self.connect() as db:
            return [dict(json.loads(r["data"]), id=r["id"]) for r in db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 50")]

    def create_run(self, mode, checkpoint=None, prior_id=None):
        value = {"mode": mode, "status": "running", "created_at": now(), "found": 0, "duplicates": 0, "checked": 0, "shortlisted": 0, "events": [], "checkpoint": checkpoint or {}, "usage_usd": 0}
        with self.lock, self.connect() as db:
            if prior_id:
                row = db.execute("SELECT data FROM runs WHERE id=?", (prior_id,)).fetchone()
                if not row:
                    raise KeyError("Run not found")
                prior = json.loads(row[0])
                if prior.get("resumed_by"):
                    raise ValueError("This checkpoint has already been resumed. Resume its latest continuation instead.")
                if prior["status"] in {"completed", "running", "cancelling"}:
                    raise ValueError("Only an interrupted, cancelled or failed search can resume.")
                value.update(checkpoint=prior.get("checkpoint", {}), resumed_from=prior_id,
                             review_spend_usd=prior.get("review_spend_usd", 0),
                             review_count=prior.get("review_count", 0), query_leads=prior.get('query_leads', {}))
                # An explicit continuation gets another bounded time window;
                # money, request caps and completed work remain cumulative.
                value['checkpoint'] = copy.deepcopy(value['checkpoint'])
                value['checkpoint']['elapsed_seconds'] = 0
            value["id"] = db.execute("INSERT INTO runs(data) VALUES (?)", (encode(value),)).lastrowid
            if prior_id:
                prior["resumed_by"] = value["id"]
                db.execute("UPDATE runs SET data=? WHERE id=?", (encode(prior), prior_id))
        return value

    def update_run(self, run_id, patch):
        with self.lock, self.connect() as db:
            row = db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError("Run not found")
            value = {**json.loads(row[0]), **patch, "id": int(run_id), "updated_at": now()}
            db.execute("UPDATE runs SET data=? WHERE id=?", (encode(value), run_id))
        return value

    def cached_review(self, key):
        with self.connect() as db:
            row = db.execute("SELECT data FROM reviews WHERE cache_key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def boards(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM employer_boards ORDER BY identity")]

    def save_board(self, data):
        url = canonical_url(data.get("url") or data.get("board_url") or "")
        if not url:
            raise ValueError("A public board URL is required")
        with self.lock, self.connect() as db:
            row = db.execute("SELECT data FROM employer_boards WHERE identity=?", (url,)).fetchone()
            prior = json.loads(row[0]) if row else {}
            value = {**prior, **data, "url": url, "first_seen": prior.get("first_seen", now()), "last_seen": now()}
            value["provenance"] = list({encode(p): p for p in prior.get("provenance", []) + data.get("provenance", [])}.values())
            db.execute("INSERT INTO employer_boards VALUES (?,?) ON CONFLICT(identity) DO UPDATE SET data=excluded.data", (url, encode(value)))
        return value

    def preparation_batches(self):
        with self.connect() as db:
            return [dict(json.loads(r["data"]), id=r["id"]) for r in db.execute("SELECT * FROM preparation_batches ORDER BY id DESC")]

    def update_preparation_batch(self, batch_id, value):
        with self.lock, self.connect() as db:
            db.execute("UPDATE preparation_batches SET data=? WHERE id=?", (encode(dict(value, updated_at=now())), batch_id))
        return value

    def save_review(self, key, data):
        with self.lock, self.connect() as db:
            db.execute("INSERT OR REPLACE INTO reviews VALUES (?,?)", (key, encode(data)))

    def material(self, material_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
            if not row:
                raise KeyError("Material not found")
            return dict(json.loads(row["data"]), id=row["id"], job_id=row["job_id"])

    def save_material(self, job_id, key, data):
        with self.lock, self.connect() as db:
            # Allocate across every CV creation path, including branches from an
            # older document. Serialize separate Store instances as well.
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT id FROM materials WHERE cache_key=?", (key,)).fetchone()
            if prior:
                return self.material(prior[0])
            versions = [json.loads(row["data"]).get("version") for row in
                        db.execute("SELECT data FROM materials WHERE job_id=?", (job_id,))]
            # The row count also covers legacy documents without a version;
            # existing saved documents and idempotent replays stay unchanged.
            latest = max([len(versions)] + [v for v in versions if type(v) is int and v > 0])
            value = dict(data, version=latest + 1)
            material_id = db.execute("INSERT INTO materials(job_id,cache_key,data) VALUES (?,?,?)", (job_id, key, encode(value))).lastrowid
        return self.material(material_id)

    def export(self):
        with self.connect() as db:
            versions = [dict(r) for r in db.execute("SELECT * FROM versions ORDER BY id")]
            reviews = [{"cache_key": r["cache_key"], "data": json.loads(r["data"])} for r in db.execute("SELECT * FROM reviews ORDER BY cache_key")]
            cv_runs = [json.loads(r[0]) for r in db.execute('SELECT data FROM cv_runs ORDER BY id')]
            base_documents = [json.loads(r[0]) for r in db.execute('SELECT data FROM base_documents ORDER BY id')]
        return {"schema_version": 3, "exported_at": now(), "profile": self.profile(), "settings": self.settings(), "jobs": [self.get_job(j["id"]) for j in self.jobs()], "shortlist": self.shortlist(), "companies": self.companies(), "boards": self.boards(), "preparation_batches": self.preparation_batches(), "runs": self.runs(), "versions": versions, "reviews": reviews, "legacy": self.meta("legacy", {}), "cv_runs": cv_runs, "base_documents": base_documents, "selected_base_cv_id": self.meta('selected_base_cv_id')}
