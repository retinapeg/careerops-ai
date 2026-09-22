"""Loopback-only web application. No email or ATS submission endpoints exist."""
from __future__ import annotations

import copy
import json
import mimetypes
import math
import os
import secrets
import threading
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from careerops.store import Store, canonical_url, digest, encode, now


class Application:
    def __init__(self, path):
        self.store = Store(path)
        self.token = secrets.token_urlsafe(32)
        self.cancel_events = {}
        self.running_ids = set()
        self.search_lock = threading.Lock()
        self.import_previews = {}
        self.import_lock = threading.Lock()
        from careerops.preparation import Preparation
        self.preparation = Preparation(self.store)
        from careerops.cv_execution import CVExecution
        self.cv_execution = CVExecution(self.store)

    @staticmethod
    def summary(job):
        from careerops.inventory import presentation
        value = {k: v for k, v in job.items() if k not in {'description', 'evidence', 'requirements', 'sources', 'history', 'materials', 'review'}}
        value['presentation'] = presentation(job)
        value['evaluation'] = {k: v for k, v in job.get('evaluation', {}).items() if k in {'admitted','fit','value','priority','confidence','eligibility','match','stretch','lanes','why','gaps','blockers','next_action','filtered_reasons','candidacy'}}
        return value

    def today(self):
        from careerops.inventory import classify_job, query_inventory
        jobs, settings = self.store.jobs(), self.store.settings()
        strategy = settings.get('strategy', {})
        limit = max(1, min(int(strategy.get('today_limit', strategy.get('shortlist_size', 10))), 20))
        stretch_limit = max(0, min(int(strategy.get('stretch_size', 2)), 5))
        per_company = max(1, min(int(strategy.get('per_company', 3)), 20))
        result = query_inventory(jobs, settings, {'region': 'all', 'view': 'recommended', 'verification':'verified_open', 'per_page': 50})
        indexed = {j['id']: j for j in jobs}
        ordered = [indexed[i] for i in result.get('all_matching_ids', []) if i in indexed]
        def stage(j):
            return j.get('application', {}).get('stage', 'not_started')
        def band(j):
            return j.get('evaluation', {}).get('candidacy', {}).get('band', 'not_suitable')
        def region(j):
            from careerops.inventory import classify_job
            return classify_job(j, settings)['region']
        candidates = [j for j in ordered if stage(j) == 'not_started' and j.get('status') not in {'applied','interview','offer','dismissed','closed'} and j.get('last_verified')]
        suitable = [j for j in candidates if band(j) in {'strong', 'plausible'}]
        # Separate streams have bounded display, but no minimum count or invented
        # replacements. A small London list never suppresses a strong overseas match.
        def bounded(stream, cap):
            chosen, companies = [], {}
            for job in stream:
                if len(chosen) >= cap:
                    break
                company = str(job.get('company') or '').strip().casefold()
                if companies.get(company, 0) >= per_company:
                    continue
                chosen.append(job)
                companies[company] = companies.get(company, 0) + 1
            return chosen
        london = bounded([j for j in suitable if region(j) == 'london'], limit)
        mediterranean = bounded([j for j in suitable if region(j) == 'overseas'], max(3, limit // 2))
        stretch = bounded([j for j in candidates if band(j) == 'stretch'], stretch_limit)
        active = [j for j in jobs if stage(j) in {'in_progress','applied','screening','interview','offer'}]
        active.sort(key=lambda j: (j.get('application', {}).get('follow_up_date') or '9999', -(j['id'])))
        today = date.today().isoformat()
        due = [j for j in jobs if (j.get('application', {}).get('follow_up_date') or j.get('follow_up_date') or '9999') <= today and stage(j) not in {'rejected','withdrawn'}]
        due.sort(key=lambda j: j.get('application', {}).get('follow_up_date') or j.get('follow_up_date'))
        saved = [j for j in jobs if j.get('bookmarked')]
        return {**{k: [self.summary(dict(j, inventory=classify_job(j, settings))) for j in v] for k, v in {'london':london,'mediterranean':mediterranean,'stretch':stretch,'in_progress':active,'follow_ups':due,'saved':saved}.items()},
                'limit': limit, 'counts': {'suitable':len(suitable),'london':sum(region(j)=='london' for j in suitable),'mediterranean':sum(region(j)=='overseas' for j in suitable),'in_progress':len(active),'follow_ups':len(due),'saved':len(saved)}}

    def inventory(self, filters):
        from careerops.inventory import query_inventory
        result = query_inventory(self.store.jobs(), self.store.settings(), filters)
        result['jobs'] = [self.summary(j) for j in result['jobs']]
        return result

    def funnel(self):
        from careerops.funnel import funnel
        return funnel(self.store.jobs(), self.store.settings(), self.store.shortlist(), self.store.runs())

    def diagnostics(self):
        from careerops.inventory import inventory_diagnostics
        settings = self.store.settings()
        settings['search']['registry'] = self.store.boards()
        return inventory_diagnostics(self.store.jobs(), settings, self.store.runs())

    def cv(self, job_id, data=None):
        from careerops import cv_review
        if data is None:
            return cv_review.workspace(self.store, job_id)
        action = data.get('action')
        if action == 'generate':
            return cv_review.generate_cv(self.store, job_id)
        if action == 'start_review':
            return cv_review.start_review(self.store, int(data['material_id']), explicit=data.get('explicit') is True, job_id=job_id)
        if action == 'submit_review':
            return cv_review.submit_review(self.store, data['review_id'], data['team'], data['response'], job_id=job_id)
        if action == 'revise':
            return cv_review.revise(self.store, data['review_id'], data.get('accepted_ids', []), job_id=job_id)
        raise ValueError('Choose generate, start_review, submit_review, or revise.')

    def cv_workflow(self, job_id, data=None):
        from careerops import cv_review
        if data is not None:
            action = data.get('action', 'start')
            if action in {'start', 'revise', 'compare', 'review_again'}:
                self.cv_execution.start(job_id, data)
            elif action in {'cancel', 'retry'}:
                run_id = data.get('run_id')
                run = self.cv_execution.get(run_id)
                if run['job_id'] != job_id:
                    raise ValueError('This CV run belongs to another vacancy.')
                if action == 'cancel':
                    self.cv_execution.cancel(run_id)
                else:
                    self.cv_execution.retry(run_id, explicit_uncertain=data.get('explicit_uncertain') is True)
            elif action == 'select':
                self.cv_execution.select(job_id, data.get('material_id'))
            else:
                raise ValueError('Choose create, revise, compare, select, cancel or retry.')
        result = self.cv_execution.workspace(job_id)
        documents = cv_review.workspace(self.store, job_id)
        result.update({key: documents[key] for key in ('versions', 'confirmed_qualifications')})
        return result

    def state(self):
        from careerops.providers import provider_status
        from careerops.materials import approved_evidence
        jobs = self.store.jobs()
        settings = self.store.settings()
        profile = self.store.profile()
        from careerops.inventory import query_inventory
        counts = {region: query_inventory(jobs, settings, {'region': region, 'include_excluded': True})['counts'] for region in ('overseas','london','all')}
        summaries = [self.summary(j) for j in jobs]
        return {"jobs": summaries, "shortlist": self.store.shortlist(), "pipeline": [j for j in summaries if j["status"] != "new"],
                "settings": settings, "profile": profile, "evidence_ready": bool(approved_evidence(profile)), "companies": self.store.companies(),
                "runs": self.store.runs(), "providers": provider_status(settings), "token": self.token,
                "inventory_counts": counts, "preparation_batches": self.store.preparation_batches(), "boards": self.store.boards(),
                "stats": {"inventory": len(jobs), "admitted": sum(bool(j["evaluation"].get("admitted")) for j in jobs), "applied": sum(j["status"] == "applied" for j in jobs)}}

    def _existing_url(self, url):
        return self.store.find_job_by_url(url) if url else None

    @staticmethod
    def _source_revision(job):
        return digest({k:job.get(k) for k in ('id','content_fingerprint','last_verified','vacancy_status',
            'verification_attempt','salary_min','salary_max','location')}) if job else None

    @staticmethod
    def _reviewed_import_fields(data):
        fields = ('url','title','company','description','location','country','salary_min','salary_max',
                  'salary_currency','salary_period','salary_type','work_pattern','office_days','sponsorship')
        result = {k:data[k] for k in fields if k in data}
        for key in ('salary_min','salary_max','office_days'):
            value = result.get(key)
            if value in (None, ''):
                if key in result:
                    result[key] = None
                continue
            if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f'{key} must be a non-negative number or unknown.')
            if key == 'office_days' and (value > 7 or not float(value).is_integer()):
                raise ValueError('Office days must be an integer between zero and seven, or unknown.')
        if result.get('salary_min') is not None and result.get('salary_max') is not None and result['salary_min'] > result['salary_max']:
            raise ValueError('Salary maximum must be at least the minimum.')
        for key,value in result.items():
            if key in {'salary_min','salary_max','office_days'}:
                continue
            if value is not None and (not isinstance(value,str) or len(value) > (200000 if key=='description' else 4000)):
                raise ValueError(f'{key} must be ordinary text within its length limit.')
        return result

    def import_preview(self, data):
        from careerops.discovery import import_url, FetchError
        url = str(data.get('url', '')).strip()
        canonical_url(url)
        if not url:
            raise ValueError('Paste a vacancy URL to fetch a preview, or use manual entry.')
        existing = self._existing_url(url)
        warnings = []
        failed = False
        try:
            job = import_url(url)
        except (FetchError, ValueError):
            failed = True
            job = {'url':url, 'title':'', 'company':'', 'description':'', 'location':'', 'last_verified':None}
            if existing:
                job = copy.deepcopy(existing)
            warnings.append('This page could not be verified automatically. Enter or correct its details and paste the advert. Salary and eligibility remain unknown unless supplied by the advert.')
        token = secrets.token_urlsafe(24)
        with self.import_lock:
            self.import_previews = {k:v for k,v in self.import_previews.items() if v['expires'] > time.monotonic()}
            if len(self.import_previews) >= 100:
                self.import_previews.pop(next(iter(self.import_previews)))
            self.import_previews[token] = {'job':copy.deepcopy(job), 'expires':time.monotonic()+1800,
                'source_revision':self._source_revision(existing), 'failed':failed, 'url':url}
        if existing:
            warnings.append('This URL is already in your workspace. Its bookmark, application stage and history will be preserved.')
        return {'job':job, 'duplicate':bool(existing), 'existing_id':existing['id'] if existing else None,
                'existing_bookmarked':bool(existing and existing.get('bookmarked')), 'warnings':warnings, 'preview_token':token}

    def import_job(self, data):
        # Serialize preview revision validation and persistence with discovery and
        # manual updates in the single authoritative application process.
        with self.store.lock:
            return self._commit_import(data)

    def _commit_import(self, data):
        from careerops.discovery import import_url
        data = copy.deepcopy(data)
        url = str(data.get("url", "")).strip()
        text = data.get("description") or data.get("text") or ""
        token = data.get('preview_token')
        existing = self._existing_url(url)
        if token:
            with self.import_lock:
                preview = self.import_previews.get(token)
            if not preview or preview['expires'] <= time.monotonic():
                raise ValueError('This import preview expired. Fetch the preview again or use manual entry.')
            if canonical_url(url) != canonical_url(preview['url']):
                raise ValueError('The URL changed. Fetch a new preview or use manual entry.')
            if preview.get('committed_id'):
                if preview.get('request_digest') != digest(data):
                    raise ValueError('This preview was already saved. Fetch a new preview to make further source changes.')
                return {'job':self.store.get_job(preview['committed_id']), 'duplicate':True}
            if self._source_revision(existing) != preview['source_revision']:
                raise ValueError('This job source changed after the preview. Fetch it again to avoid overwriting newer evidence.')
            request_digest = digest(data)
            source = preview['job']
            reviewed = self._reviewed_import_fields({**source, **data})
            def normal(value):
                return None if value in (None, '', 'unknown', 'Unknown') else value
            unchanged = all(normal(value) == normal(source.get(k)) for k,value in reviewed.items())
            if preview['failed'] and unchanged and existing:
                return {'job':self.store.get_job(existing['id']), 'duplicate':True}
            data = copy.deepcopy(source) if unchanged else {**reviewed, 'source':'manual_review', 'last_verified':None}
            if not str(data.get('title') or '').strip() or not str(data.get('company') or '').strip():
                raise ValueError('Enter the job title and company before saving the reviewed opportunity.')
        elif existing and not text and not any(data.get(k) for k in ('title','company','location')):
            return {'job':self.store.get_job(existing['id']), 'duplicate':True}
        elif url and not text and data.get("retrieve", True):
            fetched = import_url(url)
            data = fetched
        elif text or (data.get('title') and data.get('company')):
            data = self._reviewed_import_fields(data)
            data.update(description=text, source='manual_paste' if text else 'manual_entry', last_verified=None)
        else:
            raise ValueError("Enter a job URL and title/company, or paste the job description.")
        if existing and not data.get('description'):
            # A bookmark-only import must never erase an existing source advert.
            return {'job':self.store.get_job(existing['id']), 'duplicate':True}
        result = self.store.upsert_job(data)
        if token:
            with self.import_lock:
                preview['committed_id'] = result['job']['id']
                preview['request_digest'] = request_digest
        return result

    def review(self, job, settings, run=None):
        from careerops.providers import review_job, review_cache_key
        profile = self.store.profile()
        key = review_cache_key(job, profile, settings)
        cached = self.store.cached_review(key)
        if cached:
            with self.store.lock, self.store.connect() as db:
                current = self.store.get_job(job["id"])
                current["review"] = cached
                self.store._save_job(db, current)
            return dict(cached, cached=True)
        configured = copy.deepcopy(settings)
        if run:
            configured["providers"]["spent_usd"] = run.get("review_spend_usd", 0)
            def reserve(usage):
                amount = float(usage.get("reserved_usd", 0)) if isinstance(usage, dict) else float(usage)
                run["review_spend_usd"] = run.get("review_spend_usd", 0) + amount
                self.store.update_run(run["id"], {"review_spend_usd": run["review_spend_usd"]})
            configured["_provider_usage_callback"] = reserve
        result = review_job(job, profile, configured)
        self.store.save_review(key, result)
        with self.store.lock, self.store.connect() as db:
            current = self.store.get_job(job["id"])
            current["review"] = result
            self.store._save_job(db, current)
        return result

    def start_search(self, mode, prior_id=None, scope='london'):
        if mode not in {"normal", "deep", "bootstrap"}:
            raise ValueError("Choose normal, deep or bootstrap search.")
        if scope not in {'overseas', 'london'}:
            raise ValueError('Choose overseas or London coverage.')
        with self.search_lock:
            if self.running_ids:
                raise ValueError("A search is already running. Cancel or wait for it before starting another.")
            checkpoint = None
            prior = None
            if prior_id:
                prior = next((r for r in self.store.runs() if r["id"] == prior_id), None)
                if not prior:
                    raise KeyError("Search run not found")
                if prior["status"] in {"completed", "running", "cancelling"}:
                    raise ValueError("Only an interrupted, cancelled or failed search can resume.")
                checkpoint = prior.get("checkpoint")
            run = self.store.create_run(mode, checkpoint, prior_id)
            run = self.store.update_run(run['id'], {'scope': prior.get('scope', scope) if prior else scope})
            event = threading.Event()
            self.cancel_events[run["id"]] = event
            self.running_ids.add(run["id"])
            threading.Thread(target=self._search_worker, args=(run, event), daemon=True, name=f"careerops-search-{run['id']}").start()
            return run

    def _search_worker(self, run, cancellation):
        from careerops.discovery import discover
        settings = self.store.settings()
        settings.setdefault('search', {})['scope'] = run.get('scope', 'overseas')
        settings['search']['registry'] = self.store.boards()
        settings['search']['known_urls'] = [j.get('url') for j in self.store.jobs() if j.get('url')]
        from careerops.inventory import classify_job
        settings['search']['historical_sources'] = [{'url': j['url'], 'company': j.get('company')} for j in self.store.jobs() if j.get('legacy') and j.get('url') and j['status'] not in {'applied','interview','offer','dismissed','closed'} and classify_job(j, settings)['region'] == run.get('scope', 'overseas')]
        if run.get("checkpoint"):
            settings.setdefault("search", {})["checkpoint"] = run["checkpoint"]
        review_count = run.get("review_count", 0)
        query_leads = run.setdefault('query_leads', {})
        settings["_review_turn_count"] = lambda: review_count
        def emit(event):
            nonlocal review_count
            kind = event.get("kind", "progress")
            if kind == "job":
                incoming = dict(event["job"], discovery_run_id=run['id'])
                observed = query_leads.get(canonical_url(incoming.get('url', '')), [])
                incoming['source_queries'] = list(dict.fromkeys([*incoming.get('source_queries', []), *observed]))
                result = self.store.upsert_job(incoming, refresh=False)
                run["found"] += 1
                run["duplicates"] += int(result["duplicate"])
                run["checked"] += 1
                run['new_unique'] = run.get('new_unique', 0) + int(not result['duplicate'])
                if run['checked'] % 25 == 0:
                    run['shortlisted'] = len(self.store.refresh_shortlist(replace=False))
                job = result["job"]
                provider = settings.get("providers", {})
                max_turns = event.get('review_limit', settings.get("search", {}).get(run["mode"], {}).get("max_turns", 6))
                if not cancellation.is_set() and job["evaluation"].get("admitted") and provider.get("active", "none") != "none" and provider.get("budget_usd", 0) > 0 and review_count < max_turns:
                    from careerops.discovery import RunStopped
                    if event.get("remaining_seconds", 30) <= .05:
                        raise RunStopped("time_limit")
                    if not event.get('coverage_run') and event.get("discovery_turns", 0) + review_count >= max_turns:
                        raise RunStopped("limit_reached")
                    review_count += 1
                    run["review_count"] = review_count
                    self.store.update_run(run["id"], {"review_count": review_count})
                    review_settings = {k: v for k, v in settings.items() if k != "_review_turn_count"}
                    review_settings = copy.deepcopy(review_settings)
                    review_settings["providers"]["timeout_seconds"] = min(float(provider.get("timeout_seconds", 30)), event.get("remaining_seconds", 30))
                    try:
                        self.review(job, review_settings, run)
                    except Exception as exc:
                        # Discovery intentionally recovers from malformed source
                        # ValueErrors. A billed review failure must stop the run.
                        raise RuntimeError("Bounded review stopped") from exc
            elif kind == 'board':
                self.store.save_board(event.get('board', event.get('data', {})))
            elif kind == 'source_query_seen':
                try:
                    url = canonical_url(event.get('url', ''))
                except ValueError:
                    url = ''
                query = event.get('query')
                if url and isinstance(query, str) and query:
                    if event.get('is_board'):
                        from careerops.registry import board_identity
                        identity = board_identity(url)
                        if identity:
                            self.store.save_board({**identity, 'provenance': [{'url': url, 'query': query, 'kind': 'search_result', 'provider': event.get('provider'), 'retrieved_at': event.get('at')}]})
                    else:
                        query_leads[url] = list(dict.fromkeys([*query_leads.get(url, []), query]))
                        self.store.record_query_source(url, query, event.get('provider'), run['id'])
            elif kind == 'verification_failed':
                url = event.get('url')
                for candidate in self.store.jobs():
                    if candidate.get('url') == url:
                        with self.store.lock, self.store.connect() as db:
                            # Discovery's earlier snapshot can precede a manual
                            # edit. Only amend verification on the latest row.
                            db.execute('BEGIN IMMEDIATE')
                            candidate = self.store.get_job(candidate['id'])
                            candidate['verification_attempt'] = {'status': 'UNKNOWN', 'at': now(), 'reason': event.get('reason', 'Could not verify')}
                            self.store._save_job(db, candidate)
            elif kind == "checkpoint":
                run["checkpoint"] = event.get("checkpoint", event.get("data", {}))
            elif kind == "usage":
                run["usage_usd"] = event.get("spent_usd", event.get("cost_usd", run.get("usage_usd", 0)))
            else:
                run["events"] = (run.get("events", []) + [event])[-100:]
            self.store.update_run(run["id"], {k: v for k, v in run.items() if k != "id"})
        try:
            outcome = discover(settings, run["mode"], emit, cancellation.is_set)
            outcome = dict(outcome or {})
            run["source_duplicates"] = outcome.pop("duplicates", 0)
            if 'new_unique' in outcome:
                run['discovery_new_unique'] = outcome.pop('new_unique')
            run.update(outcome)
            run["status"] = "cancelled" if cancellation.is_set() else run.get("status", "completed")
            if run["status"] == "running":
                run["status"] = "completed"
        except Exception as exc:
            # Avoid surfacing arbitrary provider/client exceptions containing secrets.
            run["status"] = "failed"
            run["message"] = "Search stopped safely. Partial jobs and checkpoints were retained. Check provider readiness, limits and source configuration."
            run["error_type"] = type(exc).__name__
        finally:
            run['shortlisted'] = len(self.store.refresh_shortlist(replace=False))
            run["finished_at"] = now()
            self.store.update_run(run["id"], {k: v for k, v in run.items() if k != "id"})
            cancellation.set()
            with self.search_lock:
                self.running_ids.discard(run["id"])

    def cancel_search(self, run_id):
        event = self.cancel_events.get(run_id)
        if not event or event.is_set():
            raise ValueError("This search has no active worker.")
        event.set()
        return self.store.update_run(run_id, {"status": "cancelling", "message": "Stopping at the next bounded request boundary."})


def make_server(app, port=8765):
    static = Path(__file__).parent / "static"
    class Handler(BaseHTTPRequestHandler):
        server_version = "CareerOps"

        def log_message(self, format, *args):
            pass  # URLs and descriptions can contain private data; no request logging.

        def _trusted_host(self):
            return self.headers.get("Host", "") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        def respond(self, status, data, content_type="application/json", download=None):
            payload = encode(data).encode() if content_type == "application/json" else data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            try:
                if not self._trusted_host():
                    return self.respond(403, {"error": "Loopback host required."})
                path = urlsplit(self.path).path
                parts = path.strip("/").split("/")
                if path == "/api/state":
                    return self.respond(200, app.state())
                if path == "/api/health":
                    with app.store.connect() as db:
                        schema_version = db.execute('PRAGMA user_version').fetchone()[0]
                    return self.respond(200, {"status": "ok", "app": "CareerOps AI", "schema_version": schema_version})
                if path == '/api/today':
                    return self.respond(200, app.today())
                if path == '/api/inventory':
                    return self.respond(200, app.inventory({k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}))
                if path == '/api/funnel':
                    return self.respond(200, app.funnel())
                if path == '/api/diagnostics':
                    return self.respond(200, app.diagnostics())
                if path == '/api/base-cv':
                    from careerops import base_cv
                    return self.respond(200, base_cv.workspace(app.store))
                if path == '/api/model-connections':
                    from careerops import model_connections
                    return self.respond(200, model_connections.status(app.store))
                if len(parts) == 6 and parts[:2] == ['api', 'cv-runs'] and parts[3] == 'application-pack':
                    from careerops.application_pack import export_application_pack
                    file = export_application_pack(app.store, int(parts[2]), int(parts[4]), parts[5])
                    return self.respond(200, file.read_bytes(), mimetypes.guess_type(file)[0] or 'application/octet-stream', file.name)
                if len(parts) == 4 and parts[:2] == ['api', 'jobs'] and parts[3] == 'cv-workflow':
                    return self.respond(200, app.cv_workflow(int(parts[2])))
                if len(parts) == 4 and parts[:2] == ['api', 'jobs'] and parts[3] == 'cv':
                    return self.respond(200, app.cv(int(parts[2])))
                if len(parts) == 5 and parts[:2] == ['api', 'jobs'] and parts[3:] == ['cv', 'packet']:
                    from careerops.cv_review import review_packet
                    query = {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}
                    packet = review_packet(app.store, query['review_id'], query.get('team', 'red'), job_id=int(parts[2]))
                    return self.respond(200, packet, download='careerops-review-packet.json')
                if path == '/api/boards':
                    return self.respond(200, {'boards': app.store.boards()})
                if path == "/api/export":
                    return self.respond(200, app.store.export(), download="careerops-private-export.json")
                if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                    from careerops.inventory import classify_job, presentation
                    job = app.store.get_job(int(parts[2]))
                    job['inventory'] = classify_job(job, app.store.settings())
                    job['presentation'] = presentation(job)
                    return self.respond(200, job)
                if len(parts) == 3 and parts[:2] == ['api','preparation']:
                    return self.respond(200, {'batch': app.preparation.get(int(parts[2]))})
                if len(parts) == 4 and parts[:2] == ["api", "materials"]:
                    from careerops.materials import export_material
                    file = export_material(app.store, int(parts[2]), parts[3])
                    return self.respond(200, file.read_bytes(), mimetypes.guess_type(file)[0] or "application/octet-stream", file.name)
                if path in {"/", "/index.html", "/app.js", "/style.css"}:
                    file = static / ("index.html" if path == "/" else path[1:])
                    return self.respond(200, file.read_bytes(), (mimetypes.guess_type(file)[0] or "text/plain") + "; charset=utf-8")
                return self.respond(404, {"error": "Not found"})
            except (KeyError, FileNotFoundError):
                self.respond(404, {"error": "Not found"})
            except (ValueError, TypeError) as exc:
                self.respond(400, {"error": str(exc)})
            except Exception:
                self.respond(500, {"error": "Local operation failed. Data is preserved; check the requested action and server configuration."})

        def do_POST(self):
            try:
                origin = self.headers.get("Origin")
                allowed_origins = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
                if not self._trusted_host() or (origin and origin not in allowed_origins) or not secrets.compare_digest(self.headers.get("X-CareerOps-Token", ""), app.token):
                    return self.respond(403, {"error": "Same-origin application token required. Reload the app."})
                if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                    return self.respond(415, {"error": "JSON request required."})
                length = int(self.headers.get("Content-Length", "0"))
                path = urlsplit(self.path).path
                limit = 12 * 1024 * 1024 if path == '/api/base-cv' else 1000000
                if not 0 < length <= limit:
                    return self.respond(413, {"error": "Request exceeds the upload limit."})
                data = json.loads(self.rfile.read(length), parse_constant=lambda x: (_ for _ in ()).throw(ValueError("Finite JSON values required")))
                if not isinstance(data, dict):
                    raise ValueError("JSON object required.")
                path = urlsplit(self.path).path
                parts = path.strip("/").split("/")
                if path == "/api/import":
                    return self.respond(200, app.import_job(data))
                if path == '/api/base-cv':
                    from careerops import base_cv
                    return self.respond(200, base_cv.upload(app.store, data))
                if path == '/api/base-cv/select':
                    from careerops import base_cv
                    return self.respond(200, base_cv.select(app.store, data.get('version_id')))
                if path == '/api/model-connections':
                    from careerops import model_connections
                    return self.respond(200, model_connections.save(app.store, data))
                if len(parts) == 4 and parts[:2] == ['api', 'jobs'] and parts[3] == 'cv-workflow':
                    return self.respond(200, app.cv_workflow(int(parts[2]), data))
                if len(parts) == 4 and parts[:2] == ['api', 'jobs'] and parts[3] == 'cv':
                    return self.respond(200, app.cv(int(parts[2]), data))
                if path == '/api/import/preview':
                    return self.respond(200, app.import_preview(data))
                if path == "/api/shortlist/refresh":
                    app.store.refresh_shortlist()
                    return self.respond(200, app.state())
                if path == "/api/settings":
                    return self.respond(200, {"settings": app.store.update_settings(data["settings"])})
                if path == "/api/profile":
                    return self.respond(200, {"profile": app.store.update_profile(data["profile"])})
                if path == "/api/companies":
                    return self.respond(200, {"company": app.store.save_company(data)})

                if path == "/api/search":
                    return self.respond(202, {"run": app.start_search(data.get("mode", "normal"), scope=data.get('scope', 'london'))})
                if path == '/api/boards':
                    return self.respond(200, {'boards': [app.store.save_board(board) for board in data.get('boards', [])]})
                if path == '/api/preparation/preview':
                    return self.respond(200, app.preparation.preview(data))
                if path == '/api/preparation/start':
                    return self.respond(202, {'batch': app.preparation.start(data.get('preview_id'))})
                if len(parts) == 4 and parts[:2] == ['api', 'preparation']:
                    if parts[3] == 'cancel':
                        return self.respond(200, {'batch': app.preparation.cancel(int(parts[2]))})
                    if parts[3] == 'resume':
                        return self.respond(202, {'batch': app.preparation.resume(int(parts[2]))})
                if len(parts) == 4 and parts[:2] == ["api", "runs"]:
                    run_id = int(parts[2])
                    if parts[3] == "cancel":
                        return self.respond(200, {"run": app.cancel_search(run_id)})
                    if parts[3] == "resume":
                        prior = next((r for r in app.store.runs() if r["id"] == run_id), None)
                        if not prior:
                            raise KeyError("Run not found")
                        return self.respond(202, {"run": app.start_search(prior["mode"], run_id)})
                if len(parts) == 3 and parts[:2] == ["api", "materials"]:
                    from careerops.materials import edit_draft
                    return self.respond(200, {"material": edit_draft(app.store, int(parts[2]), data.get("text"))})
                if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "action":
                    job_id, action = int(parts[2]), data.get("action")
                    if action == "prepare":
                        from careerops.materials import prepare
                        prepare(app.store, job_id)
                        return self.respond(200, app.store.get_job(job_id))
                    if action == "reverify":
                        from careerops.discovery import import_url
                        old = app.store.get_job(job_id)
                        return self.respond(200, app.store.upsert_job(import_url(old["url"]))["job"])
                    if action == "review":
                        raise ValueError("AI review runs within a bounded search. Configure a provider and explicit budget in Settings first.")
                    return self.respond(200, app.store.action(job_id, action, data))
                return self.respond(404, {"error": "Not found"})
            except (ValueError, TypeError) as exc:
                if hasattr(exc, 'current_application'):
                    return self.respond(409, {'error':str(exc)[:1000], 'current_application':exc.current_application})
                self.respond(400, {"error": str(exc)[:1000]})
            except KeyError:
                self.respond(400, {"error": "Required field or record is missing."})
            except Exception:
                self.respond(500, {"error": "Operation failed safely. Existing data and drafts remain available."})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
