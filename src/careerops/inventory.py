"""Pure views over one canonical discovery universe and independent user state."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import math
import re
import unicodedata

from careerops.policy import POLICY_VERSION, _geography, _merged, country_codes, default_settings, location_options, role_text, vacancy_is_closed


INACTIVE_STATUSES = {"applied", "interview", "offer", "closed", "dismissed", "submitted", "rejected", "withdrawn"}
FIT_GROUPS = ("strong", "plausible", "stretch", "not_suitable", "low")
ROLE_FAMILIES = {
    "people_hr": r"\b(?:human resources|HR|total rewards|recruiter|recruitment|talent acquisition|people partner)\b",
    "marketing": r"\b(?:marketing|brand specialist|public relations)\b",
    "retail_hospitality": r"\b(?:retail|grocery|hospitality|bartender|waiter|stockroom|picker.packer|warehouse)\b",
    "business_development": r"\b(?:restaurant development|business development|account executive|sales representative)\b",
    "administration": r"\b(?:administrator|administrative|receptionist|data entry)\b",
    "gaming_design": r"\bgame design\b|game econom",
    "hardware_semiconductors": r"\b(?:physical design|VLSI|semiconductor|silicon|ASIC|hardware engineer|chip design)\b",
    "customer_service": r"\bcustomer service\b|call handler|call centre|call center",
    "ai_automation": r"\b(?:AI|artificial intelligence|machine learning|ML|LLM|applied intelligence|automation)\b|intelligence artificielle|τεχνητ.{0,10}νοημοσ|בינה מלאכותית",
    "quantum": r"\bquantum\b|quantique|κβαντ|קוונט",
    "robotics_simulation": r"robotic|robotique|autonom|computer vision|simulation|ρομποτ|רובוט",
    "quant_trading": r"\bquant(?:itative)?\b|trading (?:technology|systems|engineer|developer)|(?:risk|numerical|financial|mathematical) modell?ing|quantitatif",
    "scientific_research": r"scientific|scientifique|research (?:engineer|assistant|scientist)|numerical|chercheur|ερευνητ|חוקר",
    "customer_success": r"customer success|client success|réussite client|הצלחת לקוח",
    "implementation_solutions": r"implementation|solutions? (?:engineer|consultant|architect|analyst)|forward deployed|intégration|הטמע",
    "technical_qa": r"\b(?:QA|UAT)\b|quality assurance|software test|test (?:engineer|analyst)|model (?:evaluation|quality)",
    "technical_support": r"(?:technical|product|systems?) support|support (?:engineer|specialist)|support technique|τεχνικ.{0,10}υποστ|תמיכה טכנית",
    "data_analytics": r"\bdata (?:analyst|scientist|engineer|quality|science)|\b(?:analytics|reporting|MI)\b|données|δεδομέν|נתונים",
    "software": r"software|backend|front.?end|full.?stack|engineering manager|\b(?:python|developer|programmer|devops|SRE|Unity)\b|développeur|développeuse|logiciel|προγραμματ|מפתח|תוכנה",
    "finance_operations": r"finance|financial|FP&A|collections?|account(?:s|ing|ant)|billing|payroll|operations|business analyst|comptab|οικονομ|חשבונ",
}


def _fold(value: object) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", str(value or "")) if not unicodedata.combining(char)).casefold()


def _role_family(job: dict) -> str:
    title = str(job.get("title") or "")
    # Role title wins over employer marketing in a job description.
    for family, pattern in ROLE_FAMILIES.items():
        if re.search(pattern, title, re.I):
            return family
    supplied = str(job.get("role_family") or "")
    if supplied in ROLE_FAMILIES:
        return supplied
    # For vague titles, inspect only clearly labelled role/requirements sections.
    if not re.fullmatch(r"\s*(?:(?:junior|senior|graduate|associate)\s+)?(?:analyst|engineer|technical specialist|technical consultant|researcher)\s*", title, re.I):
        return "other"
    description = role_text(job)
    section = re.search(r"(?:responsibilities|what you.ll do|about the role|requirements|qualifications)\s*:?\s*(.{0,2000})", description, re.I | re.S)
    if section:
        for family, pattern in ROLE_FAMILIES.items():
            if re.search(pattern, section[1], re.I):
                return family
    return "other"


def _numeric(value, fallback=0.0) -> float:
    if isinstance(value, bool):
        return fallback
    try:
        number = float(value)
        return number if math.isfinite(number) else fallback
    except (TypeError, ValueError):
        return fallback


def _verification_attempt_failed(job: dict) -> bool:
    attempt = job.get("verification_attempt")
    if not isinstance(attempt, dict) or str(attempt.get("status", "")).upper() not in {"UNKNOWN", "FAIL", "FAILED"}:
        return False
    try:
        attempted = datetime.fromisoformat(str(attempt.get("at") or "").replace("Z", "+00:00"))
        previous = datetime.fromisoformat(str(job.get("last_verified") or "").replace("Z", "+00:00"))
        attempted = attempted.replace(tzinfo=timezone.utc) if attempted.tzinfo is None else attempted
        previous = previous.replace(tzinfo=timezone.utc) if previous.tzinfo is None else previous
        return attempted >= previous
    except (ValueError, TypeError):
        return True


def _routing_settings(settings: dict) -> dict:
    # Routing does not need the potentially large employer/source registry.
    defaults = default_settings()
    return {"locations": _merged(defaults["locations"], settings.get("locations", {})),
            "lanes": _merged(defaults["lanes"], settings.get("lanes", {})),
            "strategy": _merged(defaults["strategy"], settings.get("strategy") or {})}


def classify_job(job: dict, settings: dict) -> dict:
    """Annotate inventory access without changing historical Top picks decisions."""
    return _classify_job(job, _routing_settings(settings))


def presentation(job):
    """Readable titles and source-backed language notices, without changing ranking."""
    title, advert = str(job.get('title') or ''), str(job.get('description') or '')
    languages = ('English', 'Greek', 'Galician', 'Basque', 'Catalan', 'Hebrew', 'French', 'Italian',
                 'Spanish', 'German', 'Portuguese', 'Dutch', 'Russian', 'Kazakh', 'Arabic', 'Polish',
                 'Turkish', 'Romanian', 'Lithuanian', 'Latvian', 'Estonian', 'Finnish', 'Swedish',
                 'Norwegian', 'Danish', 'Czech', 'Slovak', 'Ukrainian', 'Mandarin', 'Chinese',
                 'Japanese', 'Korean', 'Hindi', 'Welsh', 'Irish', 'Maltese', 'Hungarian', 'Bulgarian')
    language_pattern = '|'.join(languages)
    tail_language = re.search(rf'\s[-–—]\s*({language_pattern})(?:\s+(?:speaking|speaker))?(?:\s*\([^)]*\))?\s*$', title, re.I)
    notices = []
    mentioned = {word.casefold() for word in re.findall(rf'\b({language_pattern})\b', advert, re.I)}
    fragments = re.split(r'[;\n]|(?<=[.!?])\s+(?=[A-Z])', advert) if mentioned else []
    for name in languages:
        if name.casefold() not in mentioned:
            continue
        quotes = [line.strip(' -•\t') for line in fragments if re.search(r'\b' + name + r'\b', line, re.I)
                  and re.search(r'\b(?:fluen\w*|native|proficien\w*|language|speak\w*|required|essential|mandatory)\b', line, re.I)
                  and not re.search(r'\b(?:programming|coding) language\b', line, re.I)]
        if not quotes:
            continue
        quote = quotes[0]
        negated = bool(re.search(r'optional|not (?:required|essential|necessary|needed)|no .{0,25}(?:required|necessary)', quote, re.I))
        preferred = bool(re.search(r'preferred|desirable|a plus|advantage|nice to have', quote, re.I))
        required = not negated and not preferred and bool(re.search(r'required|essential|mandatory|must|native|fluen|proficien', quote, re.I))
        level = 'native or near-native' if re.search(r'near.native', quote, re.I) else 'native' if re.search(r'\bnative\b', quote, re.I) else 'fluent' if re.search(r'fluen', quote, re.I) else 'proficient' if re.search(r'proficien', quote, re.I) else ''
        condition = next((c for c in job.get('evaluation', {}).get('conditions', []) if c.get('name') == 'Mandatory language' and re.search(r'\b' + name + r'\b', str(c.get('evidence') or c.get('reason') or ''), re.I)), {})
        candidate = {'PASS':'verified', 'FAIL':'not_met'}.get(condition.get('status'), 'unconfirmed')
        requirement = 'required' if required else 'preferred' if preferred else 'not_required' if negated else 'unconfirmed'
        mixed_languages = len({n.casefold() for n in re.findall(rf'\b({language_pattern})\b', quote, re.I)}) > 1
        if mixed_languages:
            level = ''  # A shared sentence does not establish each language's exact level.
            if negated or preferred:
                requirement, required = 'unconfirmed', False
        label = f'{name} {requirement.replace("_", " ")}' + (f' — {level}' if required and level else '') + '.'
        if required:
            label += ' ' + {'verified':'Your matching fluency is verified.', 'not_met':'Your recorded language level does not meet this requirement.', 'unconfirmed':'Your matching fluency is unconfirmed.'}[candidate]
        notices.append({'language':name, 'requirement':requirement, 'label':label, 'source_quote':quote,
                        'source_url':job.get('url'), 'candidate_status':candidate})
    if tail_language:
        name = next(name for name in languages if name.casefold() == tail_language[1].casefold())
        if not any(n['language'] == name for n in notices):
            notices.append({'language':name, 'requirement':'unconfirmed', 'label':f'{name} appears in the employer title; the language requirement is unconfirmed.',
                            'source_quote':'', 'source_url':job.get('url'), 'candidate_status':'unconfirmed'})
        notices.sort(key=lambda n: (n['language'] != name, n['requirement'] != 'required'))
    else:
        notices.sort(key=lambda n: n['requirement'] != 'required')
    cleaned = re.sub(r'\s*[-–—]\s*\[[^\]]+\]\s*$', '', title)
    removed = []
    while re.search(r'\s[-–—]\s', cleaned):
        suffix = re.split(r'\s[-–—]\s', cleaned)[-1].strip()
        language_tag = bool(re.fullmatch(rf'(?:{language_pattern})(?:\s+(?:speaking|speaker))?(?:\s*\([^)]*\))?', suffix, re.I))
        metadata_tag = bool(re.fullmatch(r'remote(?: friendly)?|hybrid|on.?site|full.?time|part.?time|fixed.?term|permanent|contract|CDI|CDD|m/f/d|f/m/d|H/F|\[[^\]]+\]', suffix, re.I))
        location_tag = suffix.casefold().strip(' 🇫🇷🇬🇧🇬🇷🇮🇱') in {s.casefold().strip() for s in re.split(r'[,/;]', str(job.get('location') or '')) if s.strip()}
        country_tag = bool(re.fullmatch(r'UK|GB|United Kingdom|US|USA|United States|France|Spain|Italy|Greece|Cyprus|Israel|Malta|Germany|Ireland|Portugal', suffix, re.I)) and bool(set(country_codes(suffix)) & set(country_codes(str(job.get('location') or ''))))
        employer = str(job.get('company') or '').casefold()
        company_tag = len(employer) > 3 and suffix.casefold().startswith(employer)
        if not any((language_tag, metadata_tag, location_tag, country_tag, company_tag)):
            break  # A substantive specialism, e.g. Trading Systems, stays in the role title.
        if language_tag:
            name = next(name for name in languages if re.match(r'\b' + name + r'\b', suffix, re.I))
            if not any(n['language'] == name for n in notices):
                notices.append({'language':name, 'requirement':'unconfirmed', 'label':f'{name} appears in the employer title; the language requirement is unconfirmed.',
                                'source_quote':'', 'source_url':job.get('url'), 'candidate_status':'unconfirmed'})
        removed.insert(0, suffix)
        cleaned = re.split(r'\s[-–—]\s', cleaned)[0:-1]
        cleaned = ' — '.join(cleaned)
    network = re.search(r'(?:This is not an active job opening|not (?:a current|an active) vacancy)[^.\n]*', advert, re.I)
    return {'title':cleaned or title, 'language':notices, 'removed_title_tags':removed,
            'notice':'Talent network — no current vacancy promised.' if network else None,
            'notice_source_quote':network.group() if network else None}


def _classify_job(job: dict, config: dict) -> dict:
    evaluation = job.get("evaluation") if isinstance(job.get("evaluation"), dict) else {}
    candidacy = evaluation.get("candidacy") or {}
    professional = config.get("strategy", {}).get("mode") == "professional_london_first"
    options = location_options(job)
    targets, london = _geography(job, config, options)
    all_countries = sorted({item["country"] for item in options if item["country"] != "WORLDWIDE"})
    if job.get("all_locations_required"):
        # Recommendation routing can reject mandatory multi-office attendance;
        # the universe still records every explicit work location for filtering.
        all_countries = sorted(set(all_countries) | set(country_codes(str(job.get("location") or ""))))
    role_family = _role_family(job)
    targets = [country for country in targets if country != "GB"]
    worldwide_quant = bool(config["lanes"].get("overseas_quant_worldwide") and
                           role_family in {"quant_trading", "quantum", "scientific_research"})
    if not targets and worldwide_quant:
        targets = [country for country in all_countries if country != "GB"]
    region = "london" if professional and london else "overseas" if targets else "london" if london else "other"
    countries = (sorted(set(targets) | ({"GB"} if london else set())) if targets or london else all_countries) if professional else sorted(set(targets)) if region == "overseas" else ["GB"] if region == "london" else all_countries
    eligibility = evaluation.get("eligibility", "needs_checking")
    if eligibility not in {"clear", "needs_checking", "blocked"}:
        eligibility = "needs_checking"
    is_closed = vacancy_is_closed(job)
    blockers = [str(item) for item in evaluation.get("blockers", []) if item
                and (is_closed or not str(item).casefold().startswith("closed vacancy:"))]
    if eligibility == "blocked" and not blockers and evaluation.get("blockers"):
        eligibility = "needs_checking"
    if blockers:
        eligibility = "blocked"
    # Source verification is independent from posting age or first discovery.
    verification_failed = bool(job.get("verification_error") or job.get("last_verification_error") or
                               job.get("verification_status") in {"pending", "unknown", "failed", "could_not_verify"} or _verification_attempt_failed(job))
    verification = "closed" if is_closed else "verified_open" if job.get("last_verified") and not verification_failed else "pending"
    talent_pool = job.get("opportunity_type") == "talent_pool" or bool(re.fullmatch(r"\s*(?:open|general|spontaneous|speculative) applications?\s*|\s*(?:join (?:our|the) )?talent (?:pool|community)\s*", str(job.get("title") or ""), re.I))
    if talent_pool:
        verification = "pending"
    fit = _numeric(evaluation.get("fit"))
    senior_unknown = any(item.get("name") == "Mandatory experience" and item.get("status") == "UNKNOWN"
                         for item in evaluation.get("conditions", []) if isinstance(item, dict))
    if fit < 35:
        fit_group = "low"
    elif evaluation.get("stretch") or senior_unknown or any("leadership" in str(reason).lower() for reason in evaluation.get("filtered_reasons", [])):
        fit_group = "stretch"
    elif fit >= 75 and evaluation.get("match", "strong_match") == "strong_match":
        fit_group = "strong"
    elif fit >= 45:
        fit_group = "plausible"
    else:
        fit_group = "stretch"
    if candidacy.get("band") in FIT_GROUPS:
        fit_group = candidacy["band"]
    if professional:
        from .professional import professional_family
        family = professional_family(job)
        if family:
            role_family = family
    excluded = []
    if professional and not family:
        excluded.append("Outside the configured professional target families")
    if talent_pool:
        excluded.append("General talent pool, not a verified vacancy")
    if is_closed:
        excluded.append("Vacancy is confirmed closed")
    if eligibility == "blocked":
        excluded.extend(blockers or ["Confirmed non-negotiable eligibility blocker; inspect the source conditions"])
    status = str(job.get("status") or "new")
    if status in INACTIVE_STATUSES:
        excluded.append(f"User/application history status: {status}")
    stage = (job.get("application") or {}).get("stage", "not_started")
    if stage != "not_started":
        excluded.append(f"Application stage: {stage}; retained in Applications")
    if job.get("duplicate_of") is not None or job.get("is_duplicate") is True:
        excluded.append("Duplicate source listing; inspect its canonical opportunity")
    if job.get("sample"):
        excluded.append("Sample fixture; not a real vacancy")
    hidden = bool(job.get("hidden", status == "dismissed"))
    if hidden:
        excluded.append("Hidden by the user")
    if region == "other":
        excluded.append("No enabled overseas work location or applicable London location is established")
    elif region == "london" and not professional and not evaluation.get("admitted"):
        excluded.extend(evaluation.get("filtered_reasons") or ["Does not pass the configured selective London policy"])
    uncertainties = list(dict.fromkeys(str(gap) for gap in evaluation.get("gaps", []) if gap))
    if verification == "pending":
        uncertainties.append("Live vacancy verification is pending; an unsuccessful fetch does not mean closed")
    if not evaluation:
        uncertainties.append("Fit and eligibility have not yet been assessed")
    indexed = not (job.get("duplicate_of") is not None or job.get("is_duplicate") is True or job.get("sample") or job.get("malformed_beyond_recovery"))
    universe_visible = indexed and not is_closed and not hidden
    recommendation_reasons = list(excluded)
    if fit_group not in {"strong", "plausible", "stretch"}:
        recommendation_reasons.append("Evidence match is below the recommendation bands; retained in All Jobs")
    recommended = universe_visible and not recommendation_reasons
    recommendation_status = "hidden" if hidden else "closed" if is_closed else "recommended" if recommended else "not_recommended"
    auth_conditions = [condition for condition in evaluation.get("conditions", []) if isinstance(condition, dict) and
                       str(condition.get("name") or "").casefold() in {"work authorisation", "work authorization", "right to work", "mandatory citizenship", "visa"}]
    auth_statuses = {condition.get("status") for condition in auth_conditions}
    work_authorisation = "blocked" if "FAIL" in auth_statuses else "clear" if auth_statuses == {"PASS"} else "needs_checking"
    return {
        "policy_version": POLICY_VERSION, "region": region, "countries": countries,
        "all_countries": all_countries, "location_options": options, "london_alternative": london and region == "overseas",
        "role_family": role_family, "fit_group": fit_group, "eligibility": eligibility,
        "verification": verification, "accessible": not excluded,
        "exclusion_reasons": list(dict.fromkeys(excluded)), "uncertainties": list(dict.fromkeys(uncertainties)),
        "top_pick_admitted": bool(not excluded and candidacy.get("band") in {"strong", "plausible"}) if candidacy else bool(evaluation.get("admitted")),
        "salary": "known" if job.get("salary_min") is not None or job.get("salary_max") is not None else "unknown",
        "work_pattern": _work_pattern(job), "sponsorship": str(job.get("sponsorship") or "unknown"),
        "relocation": _relocation(job), "application_stage": stage,
        "indexed": indexed, "universe_visible": universe_visible, "recommended": recommended,
        "recommendation_status": recommendation_status,
        "recommendation_reasons": list(dict.fromkeys(recommendation_reasons)),
        "reason_not_recommended": "; ".join(dict.fromkeys(recommendation_reasons)) if not recommended else "",
        "candidacy_score": _numeric(candidacy.get("score", evaluation.get("fit"))), "candidacy_band": fit_group,
        "vacancy_status": "closed" if is_closed else "open" if verification == "verified_open" else "unknown",
        "hidden": hidden, "saved": bool(job.get("bookmarked", status == "saved")),
        "sources": _sources(job), "source_queries": _source_queries(job), "seniority": _seniority(job),
        "work_authorisation": work_authorisation, "work_authorisation_conditions": auth_conditions,
    }


def _sources(job):
    values = list(job.get("source_types") or [])
    values.extend(source.get("type") for source in job.get("sources", []) if isinstance(source, dict))
    if job.get("source"):
        values.append(job["source"])
    return sorted({str(value) for value in values if value}) or ["not_recorded"]


def _source_queries(job):
    values = list(job.get("source_queries") or [])
    if job.get("source_query"):
        values.append(job["source_query"])
    for source in job.get("sources", []):
        if isinstance(source, dict):
            values.extend(source.get("source_queries") or [])
            if source.get("source_query") or source.get("query"):
                values.append(source.get("source_query") or source["query"])
    return sorted({str(value) for value in values if value}) or ["not_recorded"]


def _seniority(job):
    explicit = str(job.get("seniority") or "").lower()
    if explicit in {"entry", "mid", "senior", "lead"}:
        return explicit
    title = str(job.get("title") or "")
    for level, pattern in (("lead", r"\b(?:lead|head|director|principal|staff|manager|VP)\b"),
                           ("senior", r"\b(?:senior|sr)\b"),
                           ("entry", r"\b(?:junior|jr|graduate|entry|intern|apprentice)\b"),
                           ("mid", r"\bmid(?:dle)?\b")):
        if re.search(pattern, title, re.I):
            return level
    return "unknown"


def _location_matches(job, location):
    item = job["inventory"]
    if location == "london":
        return item["region"] == "london" or item["london_alternative"]
    if location == "remote":
        return item["work_pattern"] == "remote"
    if location == "southern_france":
        places = " ".join(str(option.get("location") or option.get("city") or "") for option in item["location_options"])
        places += " " + str(job.get("location") or "")
        return "FR" in item["all_countries"] and bool(re.search(r"\b(?:nice|sophia.antipolis|antibes|cannes|marseille|aix.en.provence|toulon|montpellier|n[iî]mes|avignon|toulouse|bordeaux|perpignan|provence|occitanie|southern france|south of france)\b", places, re.I))
    return {"israel": "IL", "greece": "GR", "cyprus": "CY"}.get(location) in item["all_countries"]


def _work_pattern(job: dict) -> str:
    explicit = str(job.get("work_pattern") or "").casefold().replace("-", "").replace("_", "").replace(" ", "")
    if explicit in {"hybrid", "onsite", "inoffice", "office", "remote"}:
        return "onsite" if explicit in {"onsite", "inoffice", "office"} else explicit
    days = job.get("office_days")
    return "remote" if days == 0 else "hybrid" if isinstance(days, (int, float)) and 0 < days < 5 else "onsite" if days == 5 else "unknown"


def _relocation(job: dict) -> str:
    value = job.get("relocation")
    if value is True or value in ("advertised", "available", "provided", "yes"):
        return "advertised"
    if value is False or value in ("unavailable", "not_provided", "no"):
        return "unavailable"
    # Only explicit relocation support wording counts, never an office address.
    description = str(job.get("description") or "")
    if re.search(r"(?:no|not (?:provide|offer)|without).{0,25}relocation (?:support|assistance|package)|relocation (?:support|assistance|package).{0,15}(?:unavailable|not (?:provided|offered))", description, re.I):
        return "unavailable"
    if re.search(r"(?:offer|provide|includes?).{0,25}relocation (?:support|assistance|package)|relocation (?:support|assistance|package) (?:is )?(?:available|provided|offered)", description, re.I):
        return "advertised"
    return "unknown"


def _boolean(value, default=False) -> bool:
    if value is None or value == "":
        return default
    if value is True or value is False:
        return value
    if str(value).strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if str(value).strip().lower() in {"0", "false", "no", "off"}:
        return False
    raise ValueError("Boolean filters must be true or false")


def _choices(value) -> list[str]:
    if value is None or value == "" or value == "all":
        return []
    values = value if isinstance(value, (list, tuple, set)) else str(value).split(",")
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip() and str(item).strip() != "all"))


def _counts(jobs: list[dict]) -> dict:
    fields = {"regions": "region", "role_families": "role_family", "fit_groups": "fit_group",
              "eligibility": "eligibility", "verification": "verification"}
    counts = {"total": len(jobs), "accessible": sum(job["inventory"]["accessible"] for job in jobs)}
    counts["excluded"] = counts["total"] - counts["accessible"]
    for output, field in fields.items():
        counts[output] = dict(sorted(Counter(job["inventory"][field] for job in jobs).items()))
    counts["total_by_region"] = dict(counts["regions"])
    counts["countries"] = dict(sorted(Counter(country for job in jobs for country in job["inventory"]["countries"]).items()))
    counts["statuses"] = dict(sorted(Counter(str(job.get("status") or "new") for job in jobs).items()))
    counts["seniority"] = dict(sorted(Counter(job["inventory"]["seniority"] for job in jobs).items()))
    counts.update(_universe_counts(jobs))
    for output, field in (("by_location", "all_countries"), ("by_source", "sources"),
                          ("by_query", "source_queries"), ("by_role_family", "role_family"),
                          ("by_recommendation_class", "fit_group")):
        grouped = {}
        for job in jobs:
            values = job["inventory"][field]
            values = values if isinstance(values, list) else [values]
            for value in values or ["unknown"]:
                grouped.setdefault(value, []).append(job)
        counts[output] = {key: _universe_counts(rows) for key, rows in sorted(grouped.items())}
    return counts


def _universe_counts(jobs):
    result = {"stored": len(jobs), **{key: sum(job["inventory"][key] for job in jobs)
                                     for key in ("indexed", "universe_visible", "recommended")}}
    for band in FIT_GROUPS:
        result[band] = sum(job["inventory"]["recommended"] and job["inventory"]["fit_group"] == band for job in jobs)
    result["all_bands"] = dict(Counter(job["inventory"]["fit_group"] for job in jobs if job["inventory"]["indexed"]))
    return result


def query_inventory(jobs: list[dict], settings: dict, filters: dict | None = None) -> dict:
    """Filter all canonical records before pagination; no overseas score/cap gate.

    ``include_stretch`` defaults true and includes plausible, stretch and low
    relevance. Turning it off means strong-only unless an explicit fit filter is
    chosen. Explicit blocked/closed/applied/dismissed filters enter inspect mode.
    Facet counts cover the selected region before narrower filters, so options do
    not disappear after selecting a country or family. ``all_matching_ids`` is
    the entire ordered scope, independent of the current page.
    """
    raw = dict(filters or {})
    view = str(raw.get("view") or "search").lower()
    if view not in {"search", "all", "recommended", "needs_checking", "saved", "applications"}:
        raise ValueError("View must be all, recommended, needs_checking, search, saved, or applications")
    history = view in {"saved", "applications"}
    professional = (settings.get("strategy") or {}).get("mode", "professional_london_first") == "professional_london_first"
    region = str(raw.get("region") or ("all" if professional or history or view == "all" else "overseas")).lower()
    if region not in {"overseas", "london", "other", "all"}:
        raise ValueError("Region must be overseas, london, other, or all")
    try:
        page = int(1 if raw.get("page") in (None, "") else raw["page"])
        per_page = int(50 if raw.get("per_page") in (None, "") else raw["per_page"])
    except (TypeError, ValueError):
        raise ValueError("Pagination requires integer page and per_page") from None
    if page < 1 or not 1 <= per_page <= 200:
        raise ValueError("Page must be positive and per_page must be between 1 and 200; page size does not cap the inventory")
    countries = [code.upper() for code in _choices(raw.get("country"))]
    families = _choices(raw.get("role_family"))
    fits = _choices(raw.get("fit"))
    eligibilities = _choices(raw.get("eligibility"))
    verifications = _choices(raw.get("verification"))
    sources = _choices(raw.get("source"))
    seniorities = _choices(raw.get("seniority"))
    if any(value not in {"entry", "mid", "senior", "lead", "unknown"} for value in seniorities):
        raise ValueError("Seniority must be entry, mid, senior, lead, or unknown")
    saved = _boolean(raw["saved"]) if raw.get("saved") not in (None, "", "all") else None
    hidden = _boolean(raw["hidden"]) if raw.get("hidden") not in (None, "", "all") else None
    date_field = str(raw.get("date_field") or "posted")
    if date_field not in {"posted", "discovered", "last_seen"}:
        raise ValueError("Date field must be posted, discovered, or last_seen")
    location = str(raw.get("location") or "")
    if location not in {"", "all", "london", "israel", "greece", "cyprus", "southern_france", "remote"}:
        raise ValueError("Unsupported location preset")
    dates = {}
    for key in ("date_from", "date_to"):
        value = str(raw.get(key) or "")
        try:
            if value:
                datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Date filters require YYYY-MM-DD") from None
        dates[key] = value
    if dates["date_from"] and dates["date_to"] and dates["date_from"] > dates["date_to"]:
        raise ValueError("Start date must not be after end date")
    statuses = [status for status in _choices(raw.get("status")) if status != "actionable"]
    extra = {key: _choices(raw.get(key)) for key in ("salary", "work_pattern", "sponsorship", "relocation", "application_stage", "work_authorisation")}
    accepted = {"salary": {"known", "unknown"}, "work_pattern": {"remote", "hybrid", "onsite", "unknown"},
                "sponsorship": {"advertised", "unavailable", "required_authorisation", "unknown"},
                "relocation": {"advertised", "unavailable", "unknown"},
                "work_authorisation": {"clear", "needs_checking", "blocked"},
                "application_stage": {"not_started", "in_progress", "applied", "screening", "interview", "offer", "rejected", "withdrawn"}}
    for key, values in extra.items():
        if any(value not in accepted[key] for value in values):
            raise ValueError(f"Unsupported {key} filter")
    if any(value not in FIT_GROUPS for value in fits):
        raise ValueError("Fit must be strong, plausible, stretch, not_suitable, or low")
    if any(value not in {"clear", "needs_checking", "blocked"} for value in eligibilities):
        raise ValueError("Eligibility must be clear, needs_checking, or blocked")
    if any(value not in {"verified_open", "pending", "closed"} for value in verifications):
        raise ValueError("Verification must be verified_open, pending, or closed")
    include_stretch = _boolean(raw.get("include_stretch"), True)
    include_excluded = _boolean(raw.get("include_excluded"), False) or "blocked" in eligibilities or "closed" in verifications or bool(set(statuses) & INACTIVE_STATUSES)
    query = str(raw.get("q") or "").strip()
    normalized = {"view": view, "region": region, "country": ",".join(countries), "role_family": ",".join(families),
                  "fit": ",".join(fits), "eligibility": ",".join(eligibilities), "verification": ",".join(verifications),
                  "status": ",".join(statuses) or "actionable", "q": query, "page": page, "per_page": per_page,
                  "include_stretch": include_stretch, "include_excluded": include_excluded,
                  "source": ",".join(sources), "seniority": ",".join(seniorities), "saved": saved,
                  "location": location, "hidden": hidden, "date_field": date_field, **dates,
                  **{key: ",".join(values) for key, values in extra.items()}}
    annotated = []
    routing_settings = _routing_settings(settings)
    for original in jobs:
        if view == "saved" and not original.get("bookmarked", original.get("status") == "saved"):
            continue
        if view == "applications" and (original.get("application") or {}).get("stage", "not_started") == "not_started" and original.get("status") not in {"applied", "submitted", "interview", "offer", "rejected", "withdrawn"}:
            continue
        job = dict(original)
        job["inventory"] = _classify_job(job, routing_settings)
        if region == "all" or job["inventory"]["region"] == region:
            annotated.append(job)
    counts = _counts(annotated)
    if view == "all":
        counts["countries"] = dict(sorted(Counter(country for job in annotated for country in job["inventory"]["all_countries"]).items()))
    matches = []
    for job in annotated:
        item = job["inventory"]
        if view in {"all", "needs_checking"} and (not item["indexed"] or item["hidden"] and hidden is not True or item["verification"] == "closed" and "closed" not in verifications):
            continue
        if view == "needs_checking" and not (item["eligibility"] == "needs_checking" or item["verification"] == "pending" or item["work_authorisation"] == "needs_checking"):
            continue
        if view == "recommended" and not item["recommended"]:
            continue
        if view == "search" and not include_excluded and not item["accessible"]:
            continue
        if countries and not set(countries).intersection(item["all_countries"] if view in {"all", "needs_checking"} else item["countries"]):
            continue
        if location not in {"", "all"} and not _location_matches(job, location):
            continue
        if sources and not set(sources).intersection(item["sources"]):
            continue
        if seniorities and item["seniority"] not in seniorities:
            continue
        if saved is not None and saved != item["saved"]:
            continue
        if hidden is not None and hidden != item["hidden"]:
            continue
        if any(dates.values()):
            field = {"posted": "posted_at", "discovered": "first_seen", "last_seen": "last_seen"}[date_field]
            posted = str(job.get(field) or "")[:10]
            if not posted or dates["date_from"] and posted < dates["date_from"] or dates["date_to"] and posted > dates["date_to"]:
                continue
        if families and item["role_family"] not in families:
            continue
        if fits and item["fit_group"] not in fits:
            continue
        if view != "all" and not history and not fits and not include_stretch and item["fit_group"] not in ({"strong", "plausible"} if job.get("evaluation", {}).get("candidacy") else {"strong"}):
            continue
        if eligibilities and item["eligibility"] not in eligibilities:
            continue
        if verifications and item["verification"] not in verifications:
            continue
        if statuses and str(job.get("status") or "new") not in statuses:
            continue
        if any(values and item[key] not in values for key, values in extra.items()):
            continue
        if query and _fold(query) not in _fold(" ".join(str(job.get(field) or "") for field in ("title", "company", "location", "description"))):
            continue
        matches.append(job)
    groups = {group: index for index, group in enumerate(FIT_GROUPS)}
    def ordering(job):
        annotation, evaluation = job["inventory"], job.get("evaluation") or {}
        if evaluation.get("candidacy"):
            from .professional import candidacy_order
            return (not annotation["accessible"] and not history, *candidacy_order(job))
        return (not annotation["accessible"], groups[annotation["fit_group"]],
                -_numeric(evaluation.get("priority")), 0.0, 0.0,
                annotation["region"] != "london", _fold(job.get("company")), _fold(job.get("title")), str(job.get("id", "")))
    matches.sort(key=ordering)
    start = (page - 1) * per_page
    return {"jobs": deepcopy(matches[start:start + per_page]), "total": len(matches), "page": page, "per_page": per_page,
            "filters": normalized, "counts": counts, "matching_counts": _universe_counts(matches),
            "all_matching_ids": [job["id"] for job in matches if job.get("id") is not None]}


def inventory_diagnostics(jobs, settings, runs=None):
    """Current canonical counts plus observed run receipts, never invented recall."""
    from .discovery import coverage_limits, _volume_queries, ADZUNA_COUNTRIES
    routing = _routing_settings(settings)
    annotated = [dict(job, inventory=_classify_job(job, routing)) for job in jobs]
    counts = _counts(annotated)
    search, audits = settings.get("search") or {}, []
    for run in runs or []:
        receipt = run.get("result") or run.get("summary") or run
        checkpoint = receipt.get("checkpoint") or run.get("checkpoint") or {}
        observed = {**checkpoint, **checkpoint.get("counts", {}), **receipt}
        audits.append({"id": run.get("id"), "status": run.get("status"), "scope": run.get("scope"),
                       **{key: observed.get(key) for key in ("rows_seen", "jobs", "new_unique", "refreshed", "out_of_scope", "outside_professional_scope", "unknown_location", "duplicates", "unlisted", "malformed", "queries", "source_counts", "query_counts", "location_counts", "pagination", "limits", "limitations")},
                       "pending_tasks": len(checkpoint.get("pending", [])),
                       "coverage": observed.get("coverage"),
                       "counter_semantics": observed.get("counter_semantics", "Historical location/professional exclusions were discarded before storage")})
    web = search.get("web") or {}
    return {"totals": _universe_counts(annotated),
            **{key: value for key, value in counts.items() if key.startswith("by_")},
            "run_audit": audits,
            "discovery": {"web_search_enabled": bool(web.get("enabled") and web.get("provider") in {"brave", "adzuna"}),
                          "web_provider": web.get("provider") or "not_configured",
                          "source_count": len(search.get("sources", [])), "registry_count": len(search.get("registry", [])),
                          "limits": {mode: coverage_limits(settings, mode) for mode in ("normal", "deep", "bootstrap")},
                          "planned_queries": {scope: _volume_queries({**settings, "search": {**search, "scope": scope}}, coverage_limits({**settings, "search": {**search, "scope": scope}}, "normal")["query_objective"]) for scope in ("london", "overseas")},
                          "providers": {"greenhouse": "Full public jobs feed; 8 MB response ceiling; no client row cap",
                                        "ashby": "Full listed public jobs feed; 8 MB response ceiling; no client row cap",
                                        "lever": "100 rows per page; skip/limit pagination until exhausted or run budget; repeated pages detected",
                                        "brave": f"20 leads per page; at most {max(1, min(int(web.get('max_pages_per_query', 5)), 10))} pages per query; leads require direct vacancy retrieval",
                                        "adzuna": "50 leads per page; same configured page cap; Israel, Greece and Cyprus unsupported",
                                        "adzuna_supported_countries": sorted(ADZUNA_COUNTRIES)}},
            "notes": ["Indexed means canonical legitimate records; All Jobs omits confirmed closed and user-hidden records.",
                      "Strong/plausible/stretch counters count recommendations; all_bands counts every indexed record.",
                      "Locations and sources overlap for jobs with multiple locations or sources; do not sum them.",
                      "Historical discarded rows and missing source queries cannot be reconstructed from stored jobs. not_recorded is explicit.",
                      "Discovery counts are observed source rows, not a claim to the entire labour market. A search result is a lead until its advert is retrieved."]}
