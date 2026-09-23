"""Bounded public-source discovery. Importing this module never makes a request.

All requests resolve public addresses once, connect to that pinned address with
normal TLS hostname validation, and revalidate every redirect. Source text is
untrusted data; discovery never interprets it as instructions or executes tools.
"""
from __future__ import annotations

import hashlib
import html
import http.client
import ipaddress
import json
import os
import queue
import re
import socket
import ssl
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import certifi
from .registry import board_identity

MAX_BYTES = 2_000_000
BOARD_MAX_BYTES = 32_000_000  # Whole public ATS feeds include hundreds of full adverts.
MAX_URL = 4096


class FetchError(ValueError):
    """A readable error which never includes credentials or raw response text."""


class ImportNeedsText(FetchError):
    pass


class RunStopped(Exception):
    pass


@dataclass
class Page:
    url: str
    status: int
    headers: dict
    body: bytes

    @property
    def text(self):
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise FetchError("Source returned invalid JSON.") from exc


def _url_parts(url):
    if not isinstance(url, str) or len(url) > MAX_URL or re.search(r"[\x00-\x20\x7f\\]", url):
        raise FetchError("Use a valid public HTTP or HTTPS URL.")
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise FetchError("Invalid URL or port.") from exc
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username is not None or parts.password is not None:
        raise FetchError("Only public HTTP(S) URLs without embedded credentials are supported.")
    if port not in {80, 443} or "%" in parts.hostname:
        raise FetchError("Only standard public web ports are supported.")
    return parts, port


def _public_addresses(host, port, timeout):
    # getaddrinfo has no portable timeout. A daemon query gives the caller a hard
    # bound; it does not hold application shutdown hostage on a broken resolver.
    result = queue.Queue(maxsize=1)

    def resolve():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError:
            result.put(None)

    threading.Thread(target=resolve, daemon=True).start()
    try:
        addresses = result.get(timeout=max(0.01, timeout))
    except queue.Empty as exc:
        raise FetchError("DNS lookup timed out.") from exc
    if not addresses:
        raise FetchError("Could not resolve the source host.")
    for family, _, _, _, sockaddr in addresses:
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except ValueError as exc:
            raise FetchError("Invalid source address.") from exc
        mapped = getattr(address, "ipv4_mapped", None)
        six_to_four = getattr(address, "sixtofour", None)
        teredo = getattr(address, "teredo", None)
        if family not in {socket.AF_INET, socket.AF_INET6} or not address.is_global or address.is_multicast or (mapped and not mapped.is_global) or (six_to_four and not six_to_four.is_global) or (teredo and any(not ip.is_global for ip in teredo)):
            raise FetchError("Private, local and reserved network addresses are blocked.")
    return addresses


class _PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host, port, address, secure, timeout):
        super().__init__(host, port, timeout=timeout)
        self.address = address
        self.secure = secure

    def connect(self):
        family, socktype, proto, _, sockaddr = self.address
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(self.timeout)
            sock.connect(sockaddr)  # Never resolve the hostname a second time.
            if self.secure:
                context = ssl.create_default_context()
                # Some macOS Python installations have no OpenSSL CA file. Add
                # the project's existing maintained CA bundle while retaining
                # default/system trust and normal hostname verification.
                context.load_verify_locations(cafile=certifi.where())
                sock = context.wrap_socket(sock, server_hostname=self.host)
            self.sock = sock
        except Exception:
            sock.close()
            raise


def safe_fetch(url, *, timeout=15, max_bytes=MAX_BYTES, headers=None, method="GET", body=None, max_redirects=4, cancelled=lambda: False, request_hook=None):
    """Fetch a bounded public URL; authenticated callers must use redirects=0."""
    deadline = time.monotonic() + min(60, max(0.05, float(timeout)))
    original = urlsplit(url)
    original_origin = (original.scheme, original.netloc)
    for hop in range(max_redirects + 1):
        if cancelled():
            raise RunStopped("cancelled")
        parts, port = _url_parts(url)
        if request_hook:
            request_hook(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchError("Source request timed out.")
        addresses = _public_addresses(parts.hostname, port, remaining)
        conn = _PinnedConnection(parts.hostname, port, addresses[0], parts.scheme == "https", max(0.01, deadline - time.monotonic()))
        request_headers = {"User-Agent": "CareerOps/1.0 (manual public vacancy retrieval)", "Accept": "text/html,application/json,text/plain", "Accept-Encoding": "identity"}
        if headers and (parts.scheme, parts.netloc) == original_origin:
            request_headers.update(headers)
        def expire():
            if conn.sock:
                try:
                    conn.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        watchdog = threading.Timer(max(0.01, deadline - time.monotonic()), expire)
        watchdog.daemon = True
        watchdog.start()
        try:
            conn.request(method, urlunsplit(("", "", parts.path or "/", parts.query, "")), body=body, headers=request_headers)
            response = conn.getresponse()
            response_headers = {k.lower(): v for k, v in response.getheaders()}
            if response.status in {301, 302, 303, 307, 308}:
                target = response_headers.get("location")
                if not target or hop >= max_redirects:
                    raise FetchError("Source redirect limit reached.")
                url = urljoin(url, target)
                continue
            if response.status >= 400:
                raise FetchError(f"Source returned HTTP {response.status}; this does not establish that the vacancy is closed.")
            if not 200 <= response.status < 300:
                raise FetchError("Source returned an unsupported HTTP status.")
            encoding = response_headers.get("content-encoding", "identity").lower()
            if encoding not in {"identity", ""}:
                raise FetchError("Source ignored the safe uncompressed response request.")
            content_type = response_headers.get("content-type", "").lower()
            if content_type and not any(t in content_type for t in ("text/", "json", "xhtml")):
                raise FetchError("This source is not a supported HTML, text or JSON page; paste its job description.")
            length = response_headers.get("content-length", "")
            if length.isdigit() and int(length) > max_bytes:
                raise FetchError("Source exceeds the download size limit.")
            chunks, count = [], 0
            while True:
                if cancelled():
                    raise RunStopped("cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FetchError("Source request timed out.")
                if conn.sock:
                    conn.sock.settimeout(remaining)
                data = response.read1(min(65536, max_bytes - count + 1))
                if not data:
                    break
                count += len(data)
                if count > max_bytes:
                    raise FetchError("Source exceeds the download size limit.")
                chunks.append(data)
            return Page(url, response.status, response_headers, b"".join(chunks))
        except (OSError, http.client.HTTPException, UnicodeError, ValueError) as exc:
            if isinstance(exc, FetchError):
                raise
            raise FetchError("Could not retrieve the source within safe network limits; paste the job text or try the employer's direct listing.") from exc
        finally:
            watchdog.cancel()
            conn.close()
    raise FetchError("Source redirect limit reached.")


def plain_text(value):
    soup = BeautifulSoup(html.unescape(str(value or "")), "html.parser")
    for node in soup(["script", "style", "noscript", "iframe", "svg", "template", "form"]):
        node.decompose()
    return re.sub(r"[ \t]+", " ", soup.get_text("\n", strip=True)).strip()[:180_000]


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    except (ValueError, TypeError):
        return None


def _finish(job, source_url, kind, *, allow_partial=False):
    stamp = _stamp()
    job["title"] = plain_text(job.get("title"))[:300]
    job["description"] = plain_text(job.get("description"))
    job["company"] = plain_text(job.get("company"))[:300]
    job["location"] = plain_text(job.get("location"))[:500]
    if not job["title"] or not job["description"] and not allow_partial:
        raise ImportNeedsText("The source did not provide a complete job title and description; paste the advert.")
    job["url"] = job.get("url") or source_url
    _url_parts(job["url"])
    job.setdefault("posted_at", None)
    job.update(first_seen=stamp, last_seen=stamp, last_verified=stamp, sample=False)
    job["content_fingerprint"] = hashlib.sha256((job["title"] + "\n" + job["company"] + "\n" + job["description"]).encode()).hexdigest()
    job.setdefault("evidence", [])
    for field in ("title", "company", "location"):
        if job.get(field):
            job["evidence"].append({"field": field, "quote": job[field], "source": source_url, "status": "PASS"})
    # Source identity belongs to the individual application page. A shared ATS
    # board URL would collide in the store's source index for every board job.
    job["sources"] = [{"url": job["url"], "retrieved_from": source_url, "type": kind, "retrieved_at": stamp, "content_fingerprint": job["content_fingerprint"], "verification": "retrieved_public_listing"}]
    if not job["description"]:
        job.update(description_missing=True, verification_status="pending", last_verified=None)
        job["sources"][0]["verification"] = "partial_public_listing"
    return job


def _jsonld_jobs(soup):
    found = []
    def visit(node, depth=0):
        if depth > 20:
            return
        if isinstance(node, list):
            for value in node:
                visit(value, depth + 1)
        elif isinstance(node, dict):
            kinds = node.get("@type", [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if isinstance(kinds, list) and "JobPosting" in kinds:
                found.append(node)
            for key in ("@graph", "mainEntity", "itemListElement", "item"):
                if key in node:
                    visit(node[key], depth + 1)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            visit(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError, RecursionError):
            pass
    return found


def _number(value):
    try:
        number = float(value)
        return number if 0 <= number < 1_000_000_000 else None
    except (TypeError, ValueError):
        return None


def _from_jsonld(raw, url):
    organisation = raw.get("hiringOrganization", {})
    loc = raw.get("jobLocation") or {}
    if isinstance(loc, list):
        loc = next((item for item in loc if isinstance(item, dict)), {})
    address = loc.get("address", {}) if isinstance(loc, dict) else {}
    if not isinstance(address, dict):
        address = {"streetAddress": str(address)}
    country = address.get("addressCountry", "")
    if isinstance(country, dict):
        country = country.get("name", "")
    location = ", ".join(str(address[k]) for k in ("addressLocality", "addressRegion") if address.get(k))
    if country:
        location += (", " if location else "") + str(country)
    ident = raw.get("identifier", {})
    job = {"title": raw.get("title", ""), "company": organisation.get("name", "") if isinstance(organisation, dict) else organisation, "description": raw.get("description", ""), "location": location, "city": address.get("addressLocality", ""), "country": country, "url": url, "requisition_id": str(ident.get("value", "")) if isinstance(ident, dict) else str(ident), "posted_at": _date(raw.get("datePosted")), "evidence": []}
    all_locations = raw.get("jobLocation", [])
    all_locations = all_locations if isinstance(all_locations, list) else [all_locations]
    job["available_locations"] = []
    for alternative in all_locations:
        addr = alternative.get("address", {}) if isinstance(alternative, dict) else {}
        if not isinstance(addr, dict):
            continue
        country_value = addr.get("addressCountry", "")
        if isinstance(country_value, dict):
            country_value = country_value.get("name", "")
        label = ", ".join(str(x) for x in (addr.get("addressLocality"), addr.get("addressRegion"), country_value) if x)
        if label:
            job["available_locations"].append({"location": label, "city": addr.get("addressLocality", ""), "country": country_value, "source": url})
    salary = raw.get("baseSalary")
    if isinstance(salary, dict):
        value = salary.get("value", {})
        value = value if isinstance(value, dict) else {"value": value}
        job.update(salary_min=_number(value.get("minValue", value.get("value"))), salary_max=_number(value.get("maxValue", value.get("value"))), salary_currency=salary.get("currency"), salary_type="base", salary_period={"YEAR": "annual", "MONTH": "month", "HOUR": "hour", "DAY": "day"}.get(str(value.get("unitText", "")).upper(), "unknown"))
        job["evidence"].append({"field": "salary", "quote": json.dumps(salary, ensure_ascii=False), "source": url, "status": "PASS"})
    allowed = raw.get("applicantLocationRequirements", [])
    allowed = allowed if isinstance(allowed, list) else [allowed]
    job["remote_countries"] = [str(a.get("name")) for a in allowed if isinstance(a, dict) and a.get("name")]
    if str(raw.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        job["office_days"] = 0
    valid = _date(raw.get("validThrough"))
    if valid:
        expiry = datetime.fromisoformat(valid)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry < datetime.now(timezone.utc):
            job["status"] = "closed"
            job["evidence"].append({"field": "closed", "quote": str(raw["validThrough"]), "source": url, "status": "PASS"})
    return _finish(job, url, "jsonld")


def _ats_location(url):
    parts, _ = _url_parts(url)
    host = parts.hostname.lower()
    segments = [s for s in parts.path.split("/") if s]
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"} and segments:
        board = segments[0]
        identifier = segments[2] if len(segments) >= 3 and segments[1] == "jobs" else parse_qs(parts.query).get("gh_jid", [None])[0]
        return "greenhouse", board, identifier, "https://boards-api.greenhouse.io/v1/boards/"
    if host in {"jobs.lever.co", "jobs.eu.lever.co"} and segments:
        return "lever", segments[0], segments[1] if len(segments) >= 2 else None, "https://api.eu.lever.co/v0/postings/" if host == "jobs.eu.lever.co" else "https://api.lever.co/v0/postings/"
    return None


def _greenhouse(raw, board, url, *, allow_partial=False):
    # Greenhouse offices belong to the underlying hiring organisation/job setup.
    # They can disagree with the location of this particular public posting
    # (for example a Dublin posting attached to a Barcelona office). They do not
    # establish an applicant's choice of work location. Preserve them separately.
    offices = [{"name": plain_text(o.get("name")), "location": plain_text(o.get("location")), "id": o.get("id")} for o in raw.get("offices", []) if isinstance(o, dict)]
    return _finish({"title": raw.get("title"), "company": raw.get("company_name") or board, "description": raw.get("content"), "location": (raw.get("location") or {}).get("name", ""), "available_locations": [], "ats_offices": offices, "location_extraction_version": 2, "requisition_id": str(raw.get("id", "")), "url": raw.get("absolute_url") or url, "posted_at": _date(raw.get("first_published"))}, url, "greenhouse", allow_partial=allow_partial)


def _lever(raw, board, url, *, allow_partial=False):
    description = raw.get("descriptionPlain") or raw.get("description") or ""
    for part in raw.get("lists", []):
        description += "\n" + str(part.get("text", "")) + "\n" + str(part.get("content", ""))
    description += "\n" + str(raw.get("additionalPlain") or raw.get("additional") or "")
    job = {"title": raw.get("text"), "company": board, "description": description, "location": (raw.get("categories") or {}).get("location", ""), "available_locations": (raw.get("categories") or {}).get("allLocations", []), "work_pattern": raw.get("workplaceType"), "requisition_id": str(raw.get("id", "")), "url": raw.get("hostedUrl") or url, "posted_at": None}
    if str(raw.get("workplaceType", "")).lower() == "remote":
        job["office_days"] = 0
    return _finish(job, url, "lever", allow_partial=allow_partial)


def _ashby(raw, board, url, *, allow_partial=False):
    primary = raw.get("address") or {}
    primary = primary.get("postalAddress") or primary
    locations = [{"location": raw.get("location", ""), "country": primary.get("addressCountry", ""), "city": primary.get("addressLocality", ""), "source": url}]
    for alternative in raw.get("secondaryLocations", []):
        address = alternative.get("address") or {}
        locations.append({"location": alternative.get("location", ""), "country": address.get("addressCountry", ""), "city": address.get("addressLocality", ""), "source": url})
    job = {"title": raw.get("title"), "company": board, "description": raw.get("descriptionPlain") or raw.get("descriptionHtml"), "location": raw.get("location", ""), "available_locations": locations, "country": primary.get("addressCountry", ""), "city": primary.get("addressLocality", ""), "url": raw.get("jobUrl") or url, "requisition_id": str(raw.get("id", "")), "posted_at": _date(raw.get("publishedAt")), "work_pattern": raw.get("workplaceType"), "employment_type": raw.get("employmentType")}
    workplace = str(raw.get("workplaceType") or "").casefold()
    if workplace == "remote" or (raw.get("isRemote") is True and workplace not in {"hybrid", "onsite", "on-site", "on_site"}):
        job["office_days"] = 0
    compensation = raw.get("compensation") or {}
    salary_components = [component for component in compensation.get("summaryComponents", [])
                         if isinstance(component, dict) and str(component.get("compensationType", "")).lower() == "salary"]
    if compensation:
        job["compensation_details"] = {"source": job["url"], "provider": "ashby", "data": deepcopy(compensation)}
    countries = set(_job_countries(job))
    # A board-wide summary may describe only one regional tier. Preserve the
    # original tiers, but never attach its first US range to a London card.
    salary_unresolved = bool(salary_components) and (len(countries) != 1 or "WORLDWIDE" in countries or len(salary_components) != 1)
    if salary_unresolved:
        job.update(salary_geography_unresolved=True, salary_min=None, salary_max=None,
                   salary_currency=None, salary_type="unknown", salary_period="unknown")
    else:
        for component in salary_components:
            job.update(salary_min=_number(component.get("minValue")), salary_max=_number(component.get("maxValue")), salary_currency=component.get("currencyCode"), salary_type="base", salary_period={"1 YEAR": "annual", "1 MONTH": "month", "1 HOUR": "hour"}.get(component.get("interval"), "unknown"))
            break
    return _finish(job, url, "ashby", allow_partial=allow_partial)


def _import(url, fetch):
    parts, _ = _url_parts(url)
    if parts.hostname == "linkedin.com" or parts.hostname.endswith(".linkedin.com"):
        raise ImportNeedsText("LinkedIn import uses pasted job text or the direct employer listing. Paste the description with this URL; no LinkedIn access controls are bypassed.")
    ats = _ats_location(url)
    identity = board_identity(url)
    if identity and identity["type"] == "ashby" and len(parts.path.strip("/").split("/")) >= 2:
        rows, _ = _board_rows(identity, fetch)
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            if urlsplit(str(raw.get("jobUrl", ""))).path.rstrip("/") == parts.path.rstrip("/"):
                return _ashby(raw, identity["board"], url)
        raise ImportNeedsText("The direct vacancy was not found in the current public Ashby feed.")
    if ats and ats[2]:
        kind, board, identifier, base = ats
        if not re.fullmatch(r"[A-Za-z0-9_-]+", board) or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise FetchError("Invalid ATS identifier.")
        api = base + board + ("/jobs/" if kind == "greenhouse" else "/") + identifier + ("?content=true" if kind == "greenhouse" else "?mode=json")
        raw = fetch(api).json()
        return _greenhouse(raw, board, url) if kind == "greenhouse" else _lever(raw, board, url)
    page = fetch(url)
    soup = BeautifulSoup(page.text, "html.parser")
    postings = _jsonld_jobs(soup)
    if len(postings) == 1:
        return _from_jsonld(postings[0], page.url)
    # Generic page guesses are not promoted to verified vacancies. Manual pasted
    # text is explicit user input and remains supported by the caller.
    raise ImportNeedsText("No single structured vacancy was found. Use its direct Greenhouse/Lever listing, or paste the full job description with this URL.")


def import_url(url: str) -> dict:
    try:
        return _import(url, safe_fetch)
    except FetchError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        raise ImportNeedsText("The source returned an unsupported job structure; paste the description.") from exc


def _money(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else Decimal(0)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _queries(settings, maximum):
    search = settings.get("search", {})
    explicit = search.get("web", {}).get("queries") or []
    if explicit:
        return [str(q)[:350] for q in explicit[:maximum] if q]
    roles = search.get("role_families") or ["software engineer", "data analyst", "solutions engineer", "technical support", "QA analyst"]
    locations = []
    for country, config in settings.get("locations", {}).items():
        if isinstance(config, dict) and config.get("enabled", True):
            locations.extend(config.get("cities") or [country])
    if settings.get("lanes", {}).get("london", True):
        locations.append("London")
    # Rotate location first so bounded runs do not use their whole query budget
    # on the first market; local-language query variants may be supplied above.
    return [f'{role} jobs {location}' for role in roles for location in locations][:maximum]


def _discover_legacy(settings: dict, mode: str, emit, cancelled) -> dict:
    """Sequential (concurrency=1), resumable source graph with strict run caps.

    `settings.search.checkpoint` resumes exactly the remaining work. Limits and
    reserved spend are cumulative across resumes so resume cannot evade a cap.
    Each request is a turn; redirects consume the request's bounded hop allowance.
    """
    if mode not in {"normal", "deep"}:
        raise ValueError("Search mode must be normal or deep.")
    search = settings.get("search", {})
    supplied = search.get(mode, {})
    defaults = {"max_pages": 12 if mode == "normal" else 36, "max_jobs": 80 if mode == "normal" else 240, "max_queries": 6 if mode == "normal" else 18, "max_turns": 6 if mode == "normal" else 16, "timeout_seconds": 90 if mode == "normal" else 240, "max_retries": 1}
    limits = {k: max(0, min(int(supplied.get(k, v)), 1000 if k != "timeout_seconds" else 1800)) for k, v in defaults.items()}
    resume = search.get("checkpoint") or {}
    counts = {key: int(resume.get("counts", {}).get(key, 0)) for key in ("pages", "turns", "queries", "jobs", "duplicates", "warnings")}
    spent = _money(resume.get("spent_usd", 0))
    elapsed_before = float(resume.get("elapsed_seconds", 0))
    start = time.monotonic()
    seen = set(resume.get("seen", []))
    visited = set(resume.get("visited", []))
    web = search.get("web", {})
    if "pending" in resume:
        pending = list(resume["pending"])
    else:
        pending = []
        for source in search.get("sources", []):
            if isinstance(source, str):
                source = {"url": source}
            if not isinstance(source, dict) or not source.get("enabled", True):
                continue
            source = dict(source)
            if not source.get("url") and source.get("board") and source.get("type") in {"lever", "greenhouse"}:
                source["url"] = ("https://jobs.lever.co/" if source["type"] == "lever" else "https://boards.greenhouse.io/") + str(source["board"])
            if source.get("url"):
                pending.append({"kind": "page", "url": source["url"], "depth": 0, "company": source.get("company", "")})
        if web.get("enabled") and web.get("provider") in {"brave", "adzuna"}:
            pending += [{"kind": "query", "query": q} for q in _queries(settings, limits["max_queries"])]
    status = "completed"

    def stop_check():
        if cancelled():
            raise RunStopped("cancelled")
        if time.monotonic() - start + elapsed_before >= limits["timeout_seconds"]:
            raise RunStopped("time_limit")
        if counts["pages"] >= limits["max_pages"] or counts["turns"] + settings.get("_review_turn_count", lambda: 0)() >= limits["max_turns"]:
            raise RunStopped("limit_reached")
        if counts["jobs"] >= limits["max_jobs"]:
            raise RunStopped("limit_reached")

    def checkpoint():
        return {"version": 1, "pending": pending, "seen": sorted(seen), "visited": sorted(visited), "counts": dict(counts), "spent_usd": str(spent), "elapsed_seconds": round(elapsed_before + time.monotonic() - start, 3)}

    def checkpoint_event():
        emit({"kind": "checkpoint", "checkpoint": checkpoint()})

    def fetch(url, **kwargs):
        # Authenticated search requests never retry automatically. Free public
        # retrieval gets at most the explicitly bounded retry allowance.
        retries = 0 if kwargs.get("max_redirects") == 0 else min(2, limits["max_retries"])
        for attempt in range(retries + 1):
            stop_check()
            counts["pages"] += 1
            counts["turns"] += 1
            remaining = limits["timeout_seconds"] - elapsed_before - (time.monotonic() - start)
            try:
                return safe_fetch(url, timeout=min(15, remaining), cancelled=cancelled, **kwargs)
            except FetchError as exc:
                transient = any(word in str(exc) for word in ("timed out", "within safe network limits", "HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504"))
                if not transient or attempt == retries:
                    raise
                emit({"kind": "warning", "message": "Public source request failed transiently; trying one bounded retry."})

    def add(job, source_query=None):
        if cancelled():
            raise RunStopped("cancelled")
        remaining = limits["timeout_seconds"] - elapsed_before - (time.monotonic() - start)
        if remaining <= 0:
            raise RunStopped("time_limit")
        key = job["url"].split("#", 1)[0]
        if key in seen:
            counts["duplicates"] += 1
            return
        job["source_query"] = source_query
        job["source_queries"] = [source_query] if source_query else []
        for source in job.get("sources", []):
            source["source_queries"] = job["source_queries"]
        emit({"kind": "job", "job": job, "remaining_seconds": remaining, "discovery_turns": counts["turns"]})
        seen.add(key)
        counts["jobs"] += 1
        if time.monotonic() - start + elapsed_before >= limits["timeout_seconds"]:
            raise RunStopped("time_limit")

    def warning(message):
        counts["warnings"] += 1
        emit({"kind": "warning", "message": message})

    def process_query(item):
        nonlocal spent
        if counts["queries"] >= limits["max_queries"]:
            raise RunStopped("limit_reached")
        provider = web.get("provider")
        budget = _money(web.get("budget_usd", 0))
        cost = _money(web.get("cost_per_query_usd"))
        if budget <= 0 or cost <= 0:
            raise RunStopped("budget_required")
        if spent + cost > budget:
            raise RunStopped("budget_exhausted")
        if provider == "brave":
            key = os.getenv("BRAVE_SEARCH_API_KEY")
            if not key:
                raise RunStopped("provider_unavailable")
            url = "https://api.search.brave.com/res/v1/web/search?" + urlencode({"q": item["query"], "count": 10})
            kwargs = {"headers": {"X-Subscription-Token": key, "Accept": "application/json"}, "max_redirects": 0}
        elif provider == "adzuna":
            app_id, key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
            if not app_id or not key:
                raise RunStopped("provider_unavailable")
            country = str(web.get("country", "gb")).lower()
            if country not in {"gb", "fr", "it", "es", "us", "de", "nl", "au", "ca", "nz", "pl", "at", "be", "br", "ch", "in", "mx", "sg", "za"}:
                raise FetchError("Configure a country supported by Adzuna; it does not cover every relocation market.")
            url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1?" + urlencode({"app_id": app_id, "app_key": key, "what": item["query"], "results_per_page": 10, "sort_by": "date"})
            kwargs = {"max_redirects": 0}
        else:
            raise RunStopped("provider_unavailable")
        # Reserve before dispatch: ambiguous failures may have been billed. No
        # automatic paid retries, including after a run resumes.
        counts["queries"] += 1
        spent += cost
        checkpoint_event()
        emit({"kind": "usage", "provider": provider, "billing_mode": "paid_api", "reserved_usd": str(cost), "spent_usd": str(spent)})
        data = fetch(url, **kwargs).json()
        results = (data.get("web") or {}).get("results", []) if provider == "brave" else data.get("results", [])
        leads = []
        for result in results:
            target = result.get("url") if provider == "brave" else result.get("redirect_url")
            if target:
                # Search snippets are leads, not verified jobs. Every result must
                # pass direct public listing extraction before a job event exists.
                leads.append({"kind": "page", "url": target, "depth": 0, "source_query": item["query"]})
        # Verify the leads already paid for before attempting another query, so a
        # later budget/provider stop still leaves useful verified partial results.
        pending[0:0] = leads

    def process_page(item):
        url = item["url"]
        page_key = url if not item.get("offset") else f'{url}?skip={item["offset"]}'
        if page_key in visited:
            counts["duplicates"] += 1
            return
        ats = _ats_location(url)
        if ats and ats[2]:
            add(_import(url, fetch), item.get("source_query"))
        elif ats:
            kind, board, _, base = ats
            if not re.fullmatch(r"[A-Za-z0-9_-]+", board):
                raise FetchError("Invalid ATS board identifier.")
            identity = board_identity(url)
            rows, more = _board_rows(identity, fetch, offset=int(item.get("offset", 0)))
            for raw in rows:
                if cancelled():
                    raise RunStopped("cancelled")
                try:
                    job = _board_job({**identity, "url": url}, raw)
                    if job is None:
                        continue
                    if item.get("company"):
                        job["company"] = plain_text(item["company"])[:300]
                    add(job, item.get("source_query"))
                except FetchError as exc:
                    warning(str(exc))
            if more:
                pending.append({**item, "offset": int(item.get("offset", 0)) + len(rows)})
        else:
            host = urlsplit(url).hostname or ""
            if host == "linkedin.com" or host.endswith(".linkedin.com"):
                raise ImportNeedsText("LinkedIn lead needs pasted text or its direct employer listing.")
            page = fetch(url)
            soup = BeautifulSoup(page.text, "html.parser")
            postings = _jsonld_jobs(soup)
            for posting in postings:
                if len(postings) > 1 and not posting.get("url"):
                    warning("A job summary has no direct vacancy URL; it was not promoted to a verified job.")
                    continue
                target = urljoin(page.url, str(posting.get("url") or page.url))
                try:
                    add(_from_jsonld(posting, target), item.get("source_query"))
                except (FetchError, ValueError, TypeError, KeyError, AttributeError):
                    warning("A structured vacancy lacked a usable title, description or direct URL.")
            if not postings and item.get("depth", 0) < (3 if mode == "deep" else 2):
                links = []
                for anchor in soup.find_all("a", href=True):
                    candidate = urljoin(page.url, anchor["href"]).split("#", 1)[0]
                    label = anchor.get_text(" ", strip=True)
                    if re.search(r"jobs?|careers?|vacanc|positions?|join.?(us|team)|greenhouse|lever\.co|ashbyhq|workable|comeet", candidate + " " + label, re.I):
                        try:
                            _url_parts(candidate)
                        except FetchError:
                            continue
                        if candidate not in links and candidate not in visited and candidate != url:
                            links.append(candidate)
                pending.extend({"kind": "page", "url": link, "depth": item.get("depth", 0) + 1, "source_query": item.get("source_query")} for link in links)
                if not links:
                    warning("Career page yielded no verified vacancies or supported public job links. Save the employer as a speculative target or paste a direct advert.")
        visited.add(page_key)

    while pending:
        item = pending[0]
        try:
            stop_check()
            if item["kind"] == "query":
                # Remove before dispatch and persist reservation so interruption
                # cannot reissue an ambiguously billed request automatically.
                pending.pop(0)
                try:
                    process_query(item)
                except RunStopped as exc:
                    if str(exc) in {"budget_required", "budget_exhausted", "provider_unavailable"}:
                        pending.insert(0, item)
                    raise
                except FetchError:
                    warning("Search provider failed; partial results and reserved spend are preserved. No paid retry was made.")
                    status = "provider_failed"
                    break
            else:
                process_page(item)
                pending.pop(0)
        except RunStopped as exc:
            status = str(exc)
            break
        except (FetchError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
            if item in pending:
                pending.remove(item)
            warning(str(exc) if isinstance(exc, FetchError) else "Source returned an unsupported job structure.")
        checkpoint_event()
        emit({"kind": "progress", "message": f'{counts["jobs"]} verified roles found; {counts["duplicates"]} duplicate leads removed.', **counts})
    if not pending and not counts["pages"] and status == "completed":
        status = "setup_required"
        warning("No public sources are enabled. Enable an employer board in Settings, or add a public careers URL, then fetch again.")
    result = {"status": status, **counts, "spent_usd": str(spent), "checkpoint": checkpoint(), "concurrency": 1}
    checkpoint_event()
    emit({"kind": "progress", "message": f"Discovery {status.replace('_', ' ')}.", **result})
    return result


COVERAGE_DEFAULTS = {
    "query_objective": 50, "board_objective": 100, "max_requests": 1000,
    "max_new_employer_requests": 150, "max_board_requests": 400,
    "max_vacancy_requests": 400, "max_historical_requests": 0,
    "max_ai_reviews": 25, "per_host_limit": 200, "timeout_seconds": 900,
}
PHASE_LIMITS = {"new_employer": "max_new_employer_requests", "known_boards": "max_board_requests", "vacancy": "max_vacancy_requests", "historical": "max_historical_requests"}
ADZUNA_COUNTRIES = {"gb", "fr", "it", "es", "us", "de", "nl", "au", "ca", "nz", "pl", "at", "be", "br", "ch", "in", "mx", "sg", "za"}


def coverage_limits(settings, mode):
    """Scope and mode limits are independent of the legacy small-search caps."""
    search = settings.get("search", {})
    coverage = search.get("coverage") or {}
    scoped = (search.get("scope_budgets") or {}).get(search.get("scope", "overseas"), {})
    mode_defaults = (coverage.get("mode_defaults") or {}).get(mode, {})
    # The original flat bootstrap defaults remain in persisted settings. New
    # routine-search defaults must not be masked by those untouched values,
    # while genuinely changed flat values remain user overrides. An explicit
    # coverage[mode] value is always exact, including one equal to the baseline.
    changed_flat = {k: coverage[k] for k, baseline in COVERAGE_DEFAULTS.items() if k in coverage and coverage[k] != baseline}
    values = {**COVERAGE_DEFAULTS, **mode_defaults, **changed_flat, **coverage.get(mode, {}), **scoped, **scoped.get(mode, {})}
    return {k: max(0, min(int(values[k]), 7200 if k == "timeout_seconds" else 50000)) for k in COVERAGE_DEFAULTS}


def _country_codes(value):
    from . import policy
    return policy.country_codes(value)


def _job_countries(job):
    from . import policy
    return sorted({option["country"] for option in policy.location_options(job)})


def _london_location(job):
    from . import policy
    return policy._london(job) or (job.get("office_days") == 0 and "WORLDWIDE" in _job_countries(job))


# Role phrases in the main local language of each listed country where adverts
# are often written in it. Every third query for that country adds one.
_GERMAN_PHRASES = ["Datenanalyst", "Softwareentwickler", "technischer Berater"]
_GREEK_PHRASES = ["αναλυτής δεδομένων", "μηχανικός λογισμικού"]
LOCAL_ROLE_PHRASES = {
    "FR": ["analyste données", "ingénieur Python", "consultant technique"], "GR": _GREEK_PHRASES, "CY": _GREEK_PHRASES,
    "ES": ["analista de datos", "ingeniero software"], "IT": ["analista dati", "sviluppatore Python"],
    "IL": ["data analyst", "software engineer"], "DE": _GERMAN_PHRASES, "CH": _GERMAN_PHRASES,
    "NL": ["data-analist", "softwareontwikkelaar", "technisch consultant"],
    "PT": ["analista de dados", "engenheiro de software", "consultor técnico"],
    "PL": ["analityk danych", "programista Python", "konsultant techniczny"],
}


def _volume_queries(settings, maximum):
    search = settings.get("search", {})
    explicit = (search.get("web") or {}).get("queries")
    if explicit:
        return [{"query": q} for q in list(dict.fromkeys(str(q)[:350] for q in explicit if q))[:maximum]]
    scope = search.get("scope", "overseas")
    locations = [("GB", ["London"])] if scope == "london" else [(c, v.get("cities") or [c]) for c, v in settings.get("locations", {}).items() if isinstance(v, dict) and v.get("enabled", True) and c != "GB"]
    from .policy import COUNTRIES
    names = {code: aliases[0] for code, aliases in COUNTRIES.items()}
    roles = search.get("role_families") or ["software engineer", "data analyst", "solutions engineer", "technical support", "QA analyst"]
    local = LOCAL_ROLE_PHRASES
    result, seen_queries = [], set()
    for index in range(maximum):
        for country, cities in locations:
            # Rotate cities immediately, shifting the role after each city pass.
            # Each city eventually visits every role, even with equal list lengths.
            city_index = index % len(cities)
            location = str(cities[city_index])
            place = names.get(country, country) if location == country else f"{location}, {names.get(country, country)}"
            variants = [roles[(index // len(cities) + city_index) % len(roles)]]
            if index % 3 == 2 and country in local:
                variants.append(local[country][(index // 3) % len(local[country])])
            for role in variants:
                query = f"{role} careers {place}"
                if query in seen_queries:
                    continue
                seen_queries.add(query)
                result.append({"query": query, "what": role, "where": "" if location == country else location, "country": country.lower()})
                if len(result) >= maximum:
                    return result
    return result[:maximum]


def _board_rows(identity, fetch, offset=0, page_size=100):
    url = identity["endpoint"]
    if identity["type"] == "lever":
        url += "?" + urlencode({"mode": "json", "skip": offset, "limit": page_size})
    data = fetch(url, max_bytes=BOARD_MAX_BYTES).json()
    rows = data if identity["type"] == "lever" else data.get("jobs")
    if not isinstance(rows, list):
        raise FetchError("ATS returned an unsupported response.")
    return rows, identity["type"] == "lever" and len(rows) == page_size


def _board_job(identity, raw):
    kind = identity["type"]
    title = plain_text(raw.get("title") or raw.get("text")).strip()
    if re.search(r"^(?:open|general|spontaneous|speculative|unsolicited) application\b|^(?:join (?:our|the) )?talent (?:pool|community|network)\b|^expressions? of interest\b", title, re.I):
        return None
    if kind == "ashby" and raw.get("isListed") is False:
        return None
    # Greenhouse prospect posts are talent pools, not advertised vacancies.
    if kind == "greenhouse" and "internal_job_id" in raw and raw["internal_job_id"] is None:
        return None
    # Never give multiple incomplete rows the shared board URL as their identity.
    url_field = {"greenhouse": "absolute_url", "lever": "hostedUrl", "ashby": "jobUrl"}[kind]
    if not raw.get(url_field):
        identifier = str(raw.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise FetchError("ATS record has neither a direct vacancy URL nor a recoverable requisition ID.")
        raw = {**raw, url_field: identity["url"].rstrip("/") + ("/jobs/" if kind == "greenhouse" else "/") + identifier}
    parser = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}[kind]
    return parser(raw, identity["board"], identity["url"], allow_partial=True)


def verify_board(url, *, provenance=None, fetch=safe_fetch):
    """Read one official board page for a seed receipt; never writes to storage.

    Pagination remains discovery's responsibility. The receipt states explicitly
    when the first page is partial, and stores only location facts from adverts.
    """
    identity = board_identity(url)
    if not identity:
        raise FetchError("Use an observed supported employer board URL.")
    rows, more = _board_rows(identity, fetch)
    countries, count = set(), 0
    for raw in rows:
        try:
            job = _board_job(identity, raw)
            if job:
                countries.update(_job_countries(job))
                count += 1
        except (FetchError, ValueError, TypeError, KeyError, AttributeError):
            continue
    stamp = _stamp()
    return {**identity, "name": identity["board"], "countries": sorted(countries), "verified_at": stamp, "verification": "retrieved_official_public_feed", "provenance": provenance or [{"url": url, "kind": "user_supplied", "retrieved_at": stamp}], "sampled_listed_jobs": count, "pagination_remaining": more}


def discover(settings: dict, mode: str, emit, cancelled) -> dict:
    """Discover a broad inventory with separate phase, host, scope and money caps."""
    deliver = emit

    def emit(event):
        try:
            deliver(event)
        except RunStopped:
            raise
        except Exception as exc:
            # Persistence/controller failures are not malformed public adverts.
            # Let the host stop safely with its last durable checkpoint intact.
            raise RuntimeError("Discovery could not persist its progress.") from exc

    if mode not in {"normal", "deep", "bootstrap"}:
        raise ValueError("Search mode must be normal, deep or bootstrap.")
    search = settings.get("search", {})
    if mode != "bootstrap" and not search.get("coverage") and not search.get("registry"):
        return _discover_legacy(settings, mode, emit, cancelled)
    limits = coverage_limits(settings, mode)
    scope = search.get("scope", "overseas")
    if scope not in {"overseas", "london"}:
        raise ValueError("Search scope must be overseas or london.")
    enabled = {"GB"} if scope == "london" else {str(c).upper() for c, v in settings.get("locations", {}).items() if isinstance(v, dict) and v.get("enabled", True) and str(c).upper() != "GB"}
    resume = search.get("checkpoint") or {}
    if resume and (resume.get("version") != 2 or resume.get("scope") != scope):
        raise ValueError("This checkpoint belongs to a different discovery format or scope.")
    fields = ("pages", "turns", "queries", "query_combinations", "boards_attempted", "boards_verified", "jobs", "unique_this_run", "new_unique", "refreshed", "rows_seen", "out_of_scope", "outside_professional_scope", "unknown_location", "duplicates", "warnings", "unlisted", "malformed", "provider_rows", "repeated_pages")
    counts = {k: int(resume.get("counts", {}).get(k, 0)) for k in fields}
    phases = {p: int(resume.get("phase_counts", {}).get(p, 0)) for p in PHASE_LIMITS}
    hosts = dict(resume.get("per_host", {}))
    spent = _money(resume.get("spent_usd", 0))
    elapsed_before = max(0, float(resume.get("elapsed_seconds", 0)))
    start = time.monotonic()
    seen, visited, boards_seen, verified_boards, seen_rows = (set(resume.get(k, [])) for k in ("seen", "visited", "boards_seen", "verified_boards", "seen_rows"))
    known = {str(u).split("#", 1)[0] for u in search.get("known_urls", [])}
    page_fingerprints = dict(resume.get("page_fingerprints", {}))
    web = search.get("web") or {}
    disabled_phases, blocked_hosts = set(), set()
    paid_paused = False
    limitations = list(resume.get("limitations", []))
    source_counts = deepcopy(resume.get("source_counts", {}))
    location_counts = dict(resume.get("location_counts", {}))
    query_counts = deepcopy(resume.get("query_counts", {}))
    pagination = deepcopy(resume.get("pagination", {}))
    pending = list(resume.get("pending", []))
    current_phase = "new_employer"

    def enqueue(url, phase, **extra):
        try:
            _url_parts(url)
        except FetchError:
            return
        url = url.split("#", 1)[0]
        identity = board_identity(url)
        # Direct ATS leads discover the full employer board as well as the role.
        if identity and phase != "historical":
            url, phase = identity["url"], "known_boards"
        key = (url, extra.get("offset", 0))
        if key[0] in visited and not key[1]:
            return
        for task in pending:
            if (task.get("url"), task.get("offset", 0)) == key:
                combined = []
                for entries in (task.get("provenance", []), extra.get("provenance", [])):
                    for entry in entries if isinstance(entries, list) else [entries]:
                        if isinstance(entry, dict) and entry not in combined:
                            combined.append(entry)
                task["provenance"] = combined
                return
        pending.append({"kind": "page", "url": url, "phase": phase, **extra})

    if "pending" not in resume:
        registry = search.get("registry", [])
        # Spend the primary London budget on feeds with observed UK vacancies
        # before unrelated boards. Unknown coverage remains discoverable.
        if scope == "london":
            registry = sorted(registry, key=lambda record: (0 if "GB" in record.get("countries", []) else 1 if not record.get("countries") else 2) if isinstance(record, dict) else 3)
        else:
            # Spend bounded runs on observed target-country feeds first. This
            # changes source order only; every enabled board remains queued.
            priorities = {country: config.get("priority", 0) for country, config in settings.get("locations", {}).items()
                          if country in enabled and isinstance(config, dict)}
            def board_priority(record):
                if not isinstance(record, dict):
                    return (3, 0)
                countries = record.get("countries") or []
                matches = [priorities[country] for country in countries if country in priorities]
                if matches:
                    return (0, -max(value if isinstance(value, (int, float)) else 0 for value in matches))
                return (1 if not countries else 2, 0)
            registry = sorted(registry, key=board_priority)
        for record in registry:
            if isinstance(record, dict) and record.get("enabled", True) and record.get("url"):
                enqueue(record["url"], "known_boards", provenance=record.get("provenance", []), company=record.get("name", ""))
        for source in search.get("sources", []):
            source = {"url": source} if isinstance(source, str) else source
            if not isinstance(source, dict) or not source.get("enabled", True):
                continue
            url = source.get("url")
            if not url and source.get("board") and source.get("type") in {"greenhouse", "lever", "ashby"}:
                url = {"greenhouse": "https://job-boards.greenhouse.io/", "lever": "https://jobs.lever.co/", "ashby": "https://jobs.ashbyhq.com/"}[source["type"]] + str(source["board"])
            if url:
                enqueue(url, "new_employer", company=source.get("company", ""), provenance=[{"url": url, "kind": "configured_source"}])
        for source in search.get("directory_sources", []):
            source = {"url": source} if isinstance(source, str) else source
            if isinstance(source, dict) and source.get("url") and source.get("enabled", True):
                enqueue(source["url"], "new_employer", directory=True, provenance=[{"url": source["url"], "kind": "configured_directory"}])
        if limits["max_historical_requests"]:
            for source in search.get("historical_sources", []):
                url = source.get("url") if isinstance(source, dict) else source
                enqueue(url, "historical")
        if web.get("enabled") and web.get("provider") in {"brave", "adzuna"}:
            for query in _volume_queries(settings, limits["query_objective"]):
                pending.append({"kind": "query", "phase": "new_employer", "page": 1, **query})

    def remaining():
        return limits["timeout_seconds"] - elapsed_before - (time.monotonic() - start)

    def alive():
        if cancelled():
            raise RunStopped("cancelled")
        if remaining() <= 0:
            raise RunStopped("time_limit")

    def checkpoint():
        return {"version": 2, "scope": scope, "pending": pending, "seen": sorted(seen), "seen_rows": sorted(seen_rows), "visited": sorted(visited), "boards_seen": sorted(boards_seen), "verified_boards": sorted(verified_boards), "page_fingerprints": dict(page_fingerprints), "counts": dict(counts), "phase_counts": dict(phases), "per_host": dict(hosts), "spent_usd": str(spent), "elapsed_seconds": round(elapsed_before + time.monotonic() - start, 3), "source_counts": deepcopy(source_counts), "location_counts": dict(location_counts), "query_counts": deepcopy(query_counts), "pagination": deepcopy(pagination), "limitations": sorted(set(limitations)), "counter_semantics": "Location/professional counters describe retained records; no recommendation filter runs before insertion"}

    def save():
        emit({"kind": "checkpoint", "checkpoint": checkpoint()})

    def warning(message):
        counts["warnings"] += 1
        emit({"kind": "warning", "message": message})

    def reserve_request(url):
        alive()
        if counts["pages"] >= limits["max_requests"]:
            raise RunStopped("request_limit")
        if phases[current_phase] >= limits[PHASE_LIMITS[current_phase]]:
            raise RunStopped("phase_limit")
        host = urlsplit(url).hostname.lower()
        if hosts.get(host, 0) >= limits["per_host_limit"]:
            raise RunStopped("host_limit")
        counts["pages"] += 1
        counts["turns"] += 1
        phases[current_phase] += 1
        hosts[host] = hosts.get(host, 0) + 1
        # Every outbound HTTP hop is reserved before dispatch, including redirects.
        save()

    def fetch(url, **kwargs):
        return safe_fetch(url, timeout=min(20, max(.05, remaining())), cancelled=cancelled, request_hook=reserve_request, **kwargs)

    def add(job, item=None):
        alive()
        key = job["url"].split("#", 1)[0]
        if key in seen:
            counts["duplicates"] += 1
            return
        first_row = key not in seen_rows
        if first_row:
            counts["rows_seen"] += 1
            seen_rows.add(key)
        countries = _job_countries(job)
        if not countries:
            counts["unknown_location"] += first_row
        elif not (enabled.intersection(countries) or (enabled and "WORLDWIDE" in countries)) or (scope == "london" and not _london_location(job)):
            counts["out_of_scope"] += first_row
        if settings.get("strategy", {}).get("mode") == "professional_london_first":
            from .professional import professional_family
            if not professional_family(job):
                counts["outside_professional_scope"] += first_row
        provenance = (item or {}).get("provenance") or []
        provenance = provenance if isinstance(provenance, list) else [provenance]
        from .store import canonical_url
        queries = set()
        for source in provenance:
            if not isinstance(source, dict) or not source.get("query") or not source.get("url"):
                continue
            try:
                if canonical_url(source["url"]) == canonical_url(job["url"]):
                    queries.add(str(source["query"]))
            except ValueError:
                continue
        queries = sorted(queries)
        job["source_queries"] = queries
        job["source_query"] = queries[0] if queries else None
        job["discovery_scope"] = scope
        for source in job.get("sources", []):
            source.update(source_queries=queries, discovery_scope=scope, provenance=provenance)
        # Callback errors must not claim a result was emitted or checkpoint it as
        # complete. The host saves the job synchronously before returning.
        reviews = max(0, int(settings.get("_review_turn_count", lambda: 0)()))
        emit({"kind": "job", "job": job, "remaining_seconds": max(0, remaining()), "discovery_turns": counts["turns"], "review_limit": limits["max_ai_reviews"], "remaining_ai_reviews": max(0, limits["max_ai_reviews"] - reviews), "coverage_run": True, "scope": scope})
        seen.add(key)
        counts["jobs"] += 1
        counts["unique_this_run"] += 1
        counts["refreshed" if key in known else "new_unique"] += 1
        for country in countries or ["unknown"]:
            location_counts[country] = location_counts.get(country, 0) + 1
        for source in {s.get("type", "unknown") for s in job.get("sources", [])} or {"unknown"}:
            bucket = source_counts.setdefault(source, {"parsed": 0, "emitted": 0})
            bucket["parsed"] += 1
            bucket["emitted"] += 1
        for query in queries:
            bucket = query_counts.setdefault(query, {"requests": 0, "leads_returned": 0, "emitted": 0})
            bucket["emitted"] += 1
        save()
        alive()

    def process_board(item, identity):
        offset = int(item.get("offset", 0))
        if not offset and identity["id"] not in boards_seen:
            counts["boards_attempted"] += 1
            boards_seen.add(identity["id"])
        rows, more = _board_rows(identity, fetch, offset=offset)
        fingerprint = hashlib.sha256(json.dumps([r.get("id") or r.get("jobUrl") or r.get("absolute_url") or r.get("hostedUrl") if isinstance(r, dict) else {"malformed": r} for r in rows], sort_keys=True).encode()).hexdigest()
        page_key = f'{identity["id"]}:{offset}'
        if any(key.startswith(identity["id"] + ":") and key != page_key and value == fingerprint for key, value in page_fingerprints.items()) and rows:
            counts["repeated_pages"] += 1
            limitations.append("pagination_repeated")
            warning("ATS repeated a previous page; enumeration stopped without inflating vacancy counts.")
            return
        first_page = page_key not in page_fingerprints
        page_fingerprints[page_key] = fingerprint
        counts["provider_rows"] += len(rows) if first_page else 0
        source_bucket = source_counts.setdefault(identity["type"], {"parsed": 0, "emitted": 0})
        source_bucket["provider_rows"] = source_bucket.get("provider_rows", 0) + (len(rows) if first_page else 0)
        previous = pagination.get(identity["id"], {})
        pagination[identity["id"]] = {"provider": identity["type"], "url": identity["url"],
                                       "pages_read": previous.get("pages_read", 0) + int(first_page),
                                       "rows_returned": previous.get("rows_returned", 0) + (len(rows) if first_page else 0),
                                       "next_offset": offset + len(rows) if more else None, "more_results": more,
                                       "response_byte_limit": BOARD_MAX_BYTES, "page_offset": offset,
                                       "page_complete": False, "page_jobs_processed": 0}
        countries = set()
        # Emit the verified registry record before jobs; root can retain a board
        # even if the run is cancelled during its large vacancy list.
        jobs = []
        for raw in rows:
            alive()
            try:
                job = _board_job(identity, raw)
                if job is None:
                    counts["unlisted"] += int(first_page)
                    source_bucket["unlisted"] = source_bucket.get("unlisted", 0) + int(first_page)
                    continue
                if item.get("company"):
                    job["company"] = plain_text(item["company"])[:300]
                countries.update(_job_countries(job))
                jobs.append(job)
            except (FetchError, ValueError, TypeError, KeyError, AttributeError, IndexError):
                counts["malformed"] += int(first_page)
                source_bucket["malformed"] = source_bucket.get("malformed", 0) + int(first_page)
                warning("An ATS record lacked a usable vacancy description or direct URL.")
        provenance = item.get("provenance") or [{"url": item["url"], "kind": "configured_source"}]
        emit({"kind": "board", "board": {**identity, "name": item.get("company") or identity["board"], "countries": sorted(countries), "verified_at": _stamp(), "verification": "retrieved_official_public_feed", "provenance": provenance, "pagination_remaining": more}})
        if identity["id"] not in verified_boards:
            counts["boards_verified"] += 1
            verified_boards.add(identity["id"])
        pagination[identity["id"]]["page_listed_jobs"] = len(jobs)
        for position, job in enumerate(jobs):
            try:
                add(job, item)
            finally:
                if job["url"].split("#", 1)[0] in seen:
                    pagination[identity["id"]]["page_jobs_processed"] = position + 1
        pagination[identity["id"]]["page_complete"] = True
        if more:
            enqueue(identity["url"], "known_boards", offset=offset + len(rows), company=item.get("company", ""), provenance=provenance)

    def process_page(item):
        identity = board_identity(item["url"])
        if identity and item["phase"] != "historical":
            process_board(item, identity)
            return
        if item["phase"] == "historical":
            add(_import(item["url"], fetch), item)
            return
        host = urlsplit(item["url"]).hostname or ""
        if host == "linkedin.com" or host.endswith(".linkedin.com"):
            raise ImportNeedsText("LinkedIn leads require pasted text or the direct employer listing.")
        page = fetch(item["url"])
        soup = BeautifulSoup(page.text, "html.parser")
        postings = _jsonld_jobs(soup)
        for posting in postings:
            if len(postings) > 1 and not posting.get("url"):
                counts["malformed"] += 1
                continue
            try:
                add(_from_jsonld(posting, urljoin(page.url, str(posting.get("url") or page.url))), item)
            except (FetchError, ValueError, TypeError, KeyError, AttributeError):
                counts["malformed"] += 1
                warning("A structured vacancy lacked a usable title, description or direct URL.")
        depth = int(item.get("depth", 0))
        if depth >= (5 if mode == "bootstrap" else 3):
            return
        links = []
        for anchor in soup.find_all(["a", "iframe"]):
            target = anchor.get("href") or anchor.get("src")
            if not target:
                continue
            url = urljoin(page.url, target).split("#", 1)[0]
            try:
                parts, _ = _url_parts(url)
            except FetchError:
                continue
            if parts.hostname in {"facebook.com", "www.facebook.com", "twitter.com", "x.com", "linkedin.com", "www.linkedin.com", "instagram.com", "www.instagram.com"} or re.search(r"\.(?:pdf|zip|png|jpg|svg|mp4|css|js)$", parts.path, re.I):
                continue
            label = anchor.get_text(" ", strip=True)
            if anchor.name == "iframe" and not board_identity(url):
                continue
            careers = bool(re.search(r"jobs?|careers?|vacanc|positions?|join.?(us|team)|recruit|emplois?|carri[eè]r|offres?|karriere|εργασ|greenhouse|lever\.co|ashbyhq|workable|comeet", url + " " + label, re.I))
            directory_link = item.get("directory") and (parts.hostname != urlsplit(page.url).hostname or bool(re.search(r"compan(?:y|ies)|portfolio|investments?", url + " " + label, re.I)))
            if not careers and not directory_link:
                continue
            if url in links or url == item["url"]:
                continue
            links.append(url)
            provenance = item.get("provenance") or []
            provenance = provenance if isinstance(provenance, list) else [provenance]
            enqueue(url, "new_employer" if not postings else "vacancy", depth=depth + 1, directory=bool(directory_link and parts.hostname == urlsplit(page.url).hostname), provenance=[*provenance, {"url": page.url, "kind": "public_directory_link" if item.get("directory") else "official_career_link", "retrieved_at": _stamp()}])
        if not postings and not links:
            warning("Public page yielded no structured vacancies or further supported career links.")

    def process_query(item):
        nonlocal spent
        provider = web.get("provider")
        budget, cost = _money(web.get("budget_usd")), _money(web.get("cost_per_query_usd"))
        if budget <= 0 or cost <= 0:
            raise RunStopped("budget_required")
        if spent + cost > budget:
            raise RunStopped("budget_exhausted")
        page_number = int(item.get("page", 1))
        if provider == "brave":
            key = os.getenv("BRAVE_SEARCH_API_KEY")
            if not key:
                raise RunStopped("provider_unavailable")
            url = "https://api.search.brave.com/res/v1/web/search?" + urlencode({"q": item["query"], "count": 20, "offset": page_number - 1})
            kwargs = {"headers": {"X-Subscription-Token": key, "Accept": "application/json"}, "max_redirects": 0}
        else:
            app_id, key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
            if not app_id or not key:
                raise RunStopped("provider_unavailable")
            country = str(item.get("country") or web.get("country") or ("gb" if scope == "london" else "")).lower()
            if country not in ADZUNA_COUNTRIES or (scope == "overseas" and country == "gb"):
                warning("Adzuna does not support this selected country; no UK fallback query was sent.")
                pending.remove(item)
                return
            query_args = {"app_id": app_id, "app_key": key, "what": item.get("what") or item["query"], "results_per_page": 50, "sort_by": "date"}
            if item.get("where"):
                query_args["where"] = item["where"]
            url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page_number}?" + urlencode(query_args)
            kwargs = {"max_redirects": 0}
        # Preflight limits before reserving money, then remove this paid task so
        # an ambiguous failure cannot charge it again on resume.
        alive()
        host = urlsplit(url).hostname
        if counts["pages"] >= limits["max_requests"]:
            raise RunStopped("request_limit")
        if phases[current_phase] >= limits[PHASE_LIMITS[current_phase]]:
            raise RunStopped("phase_limit")
        if hosts.get(host, 0) >= limits["per_host_limit"]:
            raise RunStopped("host_limit")
        pending.remove(item)
        spent += cost
        counts["queries"] += 1
        counts["query_combinations"] += page_number == 1
        save()
        emit({"kind": "usage", "provider": provider, "billing_mode": "paid_api", "reserved_usd": str(cost), "spent_usd": str(spent)})
        data = fetch(url, **kwargs).json()
        results = (data.get("web") or {}).get("results", []) if provider == "brave" else data.get("results", [])
        query_bucket = query_counts.setdefault(item["query"], {"requests": 0, "leads_returned": 0, "emitted": 0})
        query_bucket["requests"] += 1
        query_bucket["leads_returned"] += len(results)
        for result in results:
            target = result.get("url") if provider == "brave" else result.get("redirect_url")
            if target:
                try:
                    parts, _ = _url_parts(target)
                except FetchError:
                    continue
                identity, ats = board_identity(target), _ats_location(target)
                is_board = bool(identity and (not ats[2] if ats else len(parts.path.strip("/").split("/")) == 1))
                stamp = _stamp()
                emit({"kind": "source_query_seen", "url": target, "query": item["query"], "provider": provider,
                      "at": stamp, "board_url": identity["url"] if identity else None, "is_board": is_board})
                enqueue(target, "vacancy", provenance=[{"url": target, "kind": "search_result", "query": item["query"], "provider": provider, "retrieved_at": stamp}])
        page_cap = max(1, min(int(web.get("max_pages_per_query", 5)), 10))
        more = bool((data.get("query") or {}).get("more_results_available")) if provider == "brave" else int(data.get("count", 0)) > page_number * 50
        query_bucket.update(last_page=page_number, page_cap=page_cap, more_results=more,
                            truncated=bool(more and page_number >= page_cap))
        if query_bucket["truncated"]:
            limitations.append("query_page_cap")
        if more and results and page_number < page_cap:
            pending.append({**item, "page": page_number + 1})
        save()

    status = "completed"
    cursor = int(resume.get("phase_cursor", 0))
    try:
        while pending:
            alive()
            if counts["pages"] >= limits["max_requests"]:
                raise RunStopped("request_limit")
            item = None
            for _ in range(len(PHASE_LIMITS)):
                phase = list(PHASE_LIMITS)[cursor % len(PHASE_LIMITS)]
                cursor += 1
                if phase in disabled_phases or phases[phase] >= limits[PHASE_LIMITS[phase]]:
                    continue
                # Free leads already obtained take priority over another paid query.
                def schedulable(task):
                    if task["phase"] != phase or (task["kind"] == "query" and paid_paused):
                        return False
                    identity = board_identity(task.get("url"))
                    host = urlsplit(identity["endpoint"] if identity else task.get("url", "")).hostname
                    return host not in blocked_hosts
                candidates = [i for i in pending if schedulable(i)]
                item = next((i for i in candidates if i["kind"] != "query"), candidates[0] if candidates else None)
                if item:
                    break
            if item is None:
                status = limitations[-1] if limitations else "phase_limits"
                break
            current_phase = item["phase"]
            try:
                if item["kind"] == "query":
                    process_query(item)
                else:
                    process_page(item)
                    visited.add(item["url"] if not item.get("offset") else f'{item["url"]}?skip={item["offset"]}')
                    pending.remove(item)
            except RunStopped as exc:
                reason = str(exc)
                if reason in {"budget_required", "budget_exhausted", "provider_unavailable"}:
                    # Preserve paid tasks while still processing all free sources.
                    limitations.append(reason)
                    paid_paused = True
                    warning(f"Search provider {reason.replace('_', ' ')}; free public sources continue.")
                elif reason == "phase_limit":
                    disabled_phases.add(current_phase)
                    limitations.append("phase_limits")
                elif reason == "host_limit":
                    limitations.append("host_limit")
                    # A hot shared ATS host must not starve other providers.
                    identity = board_identity(item.get("url"))
                    blocked_hosts.add(urlsplit(identity["endpoint"] if identity else item.get("url", "")).hostname)
                else:
                    raise
            except (FetchError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
                if item in pending:
                    pending.remove(item)
                reason = str(exc) if isinstance(exc, FetchError) else "Source returned an unsupported job structure."
                host = urlsplit(item.get("url", "")).hostname
                warning(f"{host}: {reason}" if host else reason)
                limitations.append("source_failed")
                if item.get("url"):
                    emit({"kind": "verification_failed", "url": item["url"], "reason": str(exc) if isinstance(exc, FetchError) else "Unsupported source structure"})
                if item["kind"] == "query":
                    limitations.append("provider_failed")
                    paid_paused = True
            save()
            emit({"kind": "progress", "message": f'{counts["jobs"]} unique listings retained from {counts["boards_verified"]} verified boards; {counts["out_of_scope"]} outside the search priority and retained.', **counts, "phase_counts": dict(phases)})
        if not pending and limitations:
            status = "completed_with_source_limits"
        elif not pending:
            status = "coverage_complete" if counts["boards_attempted"] >= limits["board_objective"] and counts["query_combinations"] >= limits["query_objective"] else "sources_exhausted"
    except RunStopped as exc:
        status = str(exc)
    if not pending and not counts["pages"] and status == "sources_exhausted":
        status = "setup_required"
    message = ("No public sources are enabled. Enable an employer board in Settings, or add a public careers URL, then fetch again."
               if status == "setup_required" else
               f"Retained {counts['jobs']} vacancies from {counts['boards_verified']} verified employer boards; {counts['new_unique']} new.")
    if "source_failed" in limitations:
        message += " Some sources could not be read; review the source warnings before retrying."
    if not counts["pages"]:
        warning(message if status == "setup_required" else "No public discovery request completed. Review the run's source warnings and request limits before retrying.")
    state = checkpoint()
    state["phase_cursor"] = cursor
    result = {"status": status, "stop_reason": status, "message": message, "scope": scope, **counts, "requests": counts["pages"], "phase_counts": phases, "per_host": hosts, "coverage": limits, "coverage_met": counts["boards_attempted"] >= limits["board_objective"] and counts["query_combinations"] >= limits["query_objective"], "limitations": sorted(set(limitations)), "spent_usd": str(spent), "checkpoint": state, "concurrency": 1,
              **{key: state[key] for key in ("source_counts", "location_counts", "query_counts", "pagination", "counter_semantics")}}
    emit({"kind": "checkpoint", "checkpoint": state})
    emit({"kind": "progress", "message": f"Discovery stopped: {status.replace('_', ' ')}.", **result})
    return result
