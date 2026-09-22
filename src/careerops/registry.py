"""Employer-board identities and merge rules, independent of storage or network.

The host persists these JSON records. A discovered link is only a candidate;
discovery emits a verified record after retrieving its official public feed.
"""
from __future__ import annotations

import re
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit


def board_identity(url):
    """Canonical identity from an observed public ATS URL, never a guessed slug."""
    if not isinstance(url, str) or len(url) > 4096 or re.search(r"[\x00-\x20\x7f\\]", url):
        return None
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or parts.username is not None or parts.password is not None or parts.port not in {None, 80, 443}:
            return None
    except ValueError:
        return None
    segments = [x for x in parts.path.split("/") if x]
    if not segments or not re.fullmatch(r"[A-Za-z0-9_-]+", segments[0]):
        return None
    host, board = (parts.hostname or "").lower(), segments[0]
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"}:
        if board == "embed":
            board = parse_qs(parts.query).get("for", [""])[0]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", board):
                return None
        kind, region = "greenhouse", "eu" if ".eu." in host else "global"
        canonical = f"https://job-boards{'.eu' if region == 'eu' else ''}.greenhouse.io/{board}"
        # Greenhouse uses one public API for its regional job-board hosts.
        endpoint = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
        key = f"greenhouse:{board}"
    elif host in {"jobs.lever.co", "jobs.eu.lever.co"}:
        kind, region = "lever", "eu" if ".eu." in host else "global"
        canonical = f"https://jobs{'.eu' if region == 'eu' else ''}.lever.co/{board}"
        endpoint = f"https://api{'.eu' if region == 'eu' else ''}.lever.co/v0/postings/{board}"
        key = f"lever:{region}:{board}"
    elif host == "jobs.ashbyhq.com":
        kind, region = "ashby", "global"
        canonical = f"https://jobs.ashbyhq.com/{board}"
        endpoint = f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
        key = f"ashby:{board}"
    else:
        return None
    return {"id": key, "type": kind, "board": board, "region": region, "url": canonical, "endpoint": endpoint}


def merge_registry(existing, incoming):
    """Merge verified records, keeping all original discovery provenance.

    A successful retrieval is evidence of a reachable public board, not an
    assertion that every vacancy is suitable or still accepting applications.
    """
    merged = {}
    for value in [*(existing or []), *(incoming or [])]:
        if not isinstance(value, dict) or not value.get("verified_at"):
            continue
        identity = board_identity(value.get("url"))
        if not identity:
            continue
        old = merged.get(identity["id"], {})
        record = {**deepcopy(old), **deepcopy(value), **identity}
        provenance = []
        for source in [old.get("provenance", []), value.get("provenance", [])]:
            source = source if isinstance(source, list) else [source]
            for item in source:
                if isinstance(item, dict) and item.get("url") and item not in provenance:
                    provenance.append(deepcopy(item))
        record["provenance"] = provenance
        record["countries"] = sorted(set(old.get("countries", [])) | set(value.get("countries", [])))
        record["first_verified_at"] = old.get("first_verified_at") or value.get("first_verified_at") or value["verified_at"]
        record.setdefault("name", identity["board"])
        merged[identity["id"]] = record
    return list(merged.values())
