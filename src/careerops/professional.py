"""Professional candidacy heuristics grounded in candidate and advert evidence.

Pure functions only. Scores order review work; they are not hiring probabilities.
Projects support skills, never invented employment, specialist years or degrees.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
import re


STRATEGY_DEFAULTS = {
    "mode": "professional_london_first", "migration_version": 1,
    "shortlist_size": 10, "stretch_size": 2, "per_company": 3,
    "weights": {"core_skills": .45, "responsibilities": .25, "seniority": .15,
                "domain": .10, "location": .05},
}
CANDIDACY_RUBRIC_VERSION = "professional-candidacy-accounting-v1"
TARGET_FAMILIES = {
    "ai_automation": r"\b(?:AI|artificial intelligence|machine learning|ML|LLM|agentic|automation)\b",
    "quantum": r"quantum|quantique",
    "scientific_research": r"scientific|research (?:engineer|assistant|scientist)|numerical|simulation|physicist|chercheur",
    "quant_trading": r"\bquant(?:itative)?\b|trading (?:systems|technology|engineer)|(?:risk|financial|mathematical) modell?ing",
    "implementation_solutions": r"implementation|solutions? (?:engineer|consultant|analyst)|forward deployed|technical consultant|product specialist",
    "technical_support": r"(?:technical|product|systems?|application|software|IT) support|support (?:engineer|specialist)|support technique",
    "data_analytics": r"\bdata (?:analyst|scientist|engineer|warehouse|quality|validation|science)|\banalytics\b|reporting|\bMI analyst\b|business analyst|PMO analyst",
    "technical_qa": r"\b(?:QA|UAT)\b|quality assurance|software test|test (?:engineer|analyst)|model (?:evaluation|quality)",
    "software": r"software|backend|front.?end|full.?stack|\b(?:python|developer|programmer|devops|SRE|iOS|Android)\b|développeur",
    "professional_operations": r"operations? analyst|research.{0,15}analyst|investment analyst|modelling analyst|systems? analyst|technical operations",
}
EXCLUDED_TITLES = re.compile(
    r"\b(?:cleaner|housekeeper|housekeeping|janitor|picker|packer|driver|delivery rider|"
    r"bartender|barista|waiter|waitress|chef|cook|kitchen assistant|stockroom assistant|caregiver|"
    r"care assistant|support worker|nurse|secondary.{0,20}teacher|teacher|teaching assistant|"
    r"receptionist|concierge|office administrator|administrative assistant|medical writer|"
    r"recruiter|recruitment consultant|sales executive|account executive)\b", re.I)

# Git, GitHub and generic office tools are supporting tools, never the core
# denominator for a specialised engineering job.
TECH = {
    "Python": r"\bpython\b", "SQL": r"\bsql\b", "Swift": r"\bswift(?:ui)?\b|objective.c",
    "Kotlin": r"\bkotlin\b", "Java": r"\bjava\b", "Spring": r"\bspring(?:boot)?\b",
    "JavaScript": r"\bjavascript\b", "TypeScript": r"\btypescript\b", "React": r"\breact(?:js)?\b",
    "C++": r"(?<!\w)c\+\+(?!\w)", "C#/.NET": r"(?<!\w)c#|\.net\b", "Unity": r"\bunity\b", "Rust": r"\brust\b", "Go": r"\bgolang\b|\bgo (?:programming|language|developer)\b",
    "PHP": r"\bphp\b", "Ruby": r"\bruby\b", "Scala": r"\bscala\b",
    "pandas/NumPy": r"\b(?:pandas|numpy)\b", "ML framework": r"pytorch|tensorflow|scikit.learn|\bjax\b|(?:ML|machine learning) framework",
    "API integration": r"\b(?:REST|APIs?|HTTP(?!S?://)|OAuth)\b", "SQL databases": r"postgres(?:ql)?|mysql|relational database",
    "cloud infrastructure": r"\b(?:AWS|Azure|Kubernetes|GCP)\b|cloud infrastructure",
    "networking": r"\b(?:TCP|DNS|LAN|WAN|routing|network troubleshooting|computer networks?|network protocols?)\b|networking (?:concepts|protocols|technologies|knowledge|experience)",
    "Power BI": r"power\s?bi", "Excel": r"\bexcel\b", "scientific computing": r"numerical|scientific comput|quantum|physics|qiskit|qutip",
}
# Only recognised programming/platform specialisms define this title gate.
# Generic words such as marketing, systems, support or testing do not.
TITLE_TECH = {name: TECH[name] for name in (
    "Python", "SQL", "Swift", "Kotlin", "Java", "Spring", "JavaScript", "TypeScript", "React",
    "C++", "C#/.NET", "Unity", "Rust", "Go", "PHP", "Ruby", "Scala")}
TITLE_TECH["Swift"] = r"\biOS\b|" + TITLE_TECH["Swift"]
TITLE_TECH["Kotlin"] = r"\bAndroid\b|" + TITLE_TECH["Kotlin"]
RESPONSIBILITIES = {
    "analysis": r"analys\w*|analyz\w*|quantitative|numerical|data (?:quality|validation)|research",
    "software delivery": r"(?:build|develop|implement|maintain|design|test)\w*.{0,50}(?:software|system|API|Python|pipeline|application)|software develop|programming",
    "communication": r"communicat\w*|explain\w*|present\w*|stakeholder|client.facing|customer.facing",
    "troubleshooting": r"troubleshoot\w*|debug\w*|problem.solv\w*|investigat\w*.{0,35}(?:issue|incident|error)",
    "customer support": r"customer (?:service|support)|call handl\w*|support.ticket|client.{0,30}(?:support|onboard)|implementation",
    "testing": r"\b(?:pytest|UAT|QA|testing|validation|evaluat\w*)\b",
}


def professional_family(job: dict) -> str | None:
    """Title and role duties establish professional scope, not employer branding."""
    title = str(job.get("title") or "")
    if EXCLUDED_TITLES.search(title):
        return None
    for family, pattern in TARGET_FAMILIES.items():
        if re.search(pattern, title, re.I):
            return family
    from .policy import role_text
    duties = role_text(job)
    if re.search(r"customer success|client success|customer experience", title, re.I):
        return "customer_success" if re.search(r"SaaS|software|technical|platform|API|data|implementation", duties, re.I) else None
    if re.fullmatch(r"\s*(?:(?:junior|graduate|associate|senior)\s+)?(?:analyst|engineer|technical specialist|researcher)\s*", title, re.I):
        for family, pattern in TARGET_FAMILIES.items():
            if re.search(pattern, duties, re.I):
                return family
    return None


def _evidence(profile: dict) -> list[dict]:
    allowed = {"DIRECT", "TRANSFERABLE", "INDIRECT", "CURRENT_PROJECT", "USER_PROVIDED", "VERIFIED"}
    records = []
    for item in profile.get("evidence", []):
        if not isinstance(item, dict) or str(item.get("status", "")).upper() not in allowed:
            continue
        text = str(item.get("text") or "")
        # Assessment opinions and future learning plans cannot prove skills.
        if item.get("sensitive") or item.get("include_in_model") is False or re.search(r"strong fit for|suited to roles|could (?:learn|apply)|plans? to learn|not (?:yet )?(?:used|experienced)|no (?:direct )?experience", text, re.I):
            continue
        if item.get("id") and text:
            records.append({"id": str(item["id"]), "text": text, "status": str(item["status"]).upper()})
    return records


def _sections(text: str) -> tuple[str, str]:
    preferred = re.search(r"(?:nice.to.have|useful.{0,8}not essential|bonus (?:points|skills)|desirable skills|preferred qualifications|what (?:would|will) make you stand out)\s*:?", text, re.I)
    return (text[:preferred.start()], text[preferred.start():]) if preferred else (text, "")


def _supported(pattern: str, records: list[dict], declared: str) -> tuple[float, list[str]]:
    matches = [record for record in records if re.search(pattern, record["text"], re.I)]
    strengths = {"DIRECT": 1.0, "VERIFIED": 1.0, "USER_PROVIDED": .9, "CURRENT_PROJECT": .9, "TRANSFERABLE": .65, "INDIRECT": .4}
    score = max([strengths[record["status"]] for record in matches] + ([.9] if re.search(pattern, declared, re.I) else [0]))
    return score, [record["id"] for record in matches[:5]]


@lru_cache(maxsize=16)
def _support_table(records: tuple, declared: str) -> dict:
    """Reuse candidate evidence work across an inventory rescore, without I/O."""
    unpacked = [{"id": identity, "text": text, "status": status} for identity, text, status in records]
    return {pattern: _supported(pattern, unpacked, declared) for pattern in {*TECH.values(), *RESPONSIBILITIES.values()}}


def _education_conditions(job: dict, profile: dict, text: str) -> list[dict]:
    """Literal strict entry requirements vs explicitly softer graduate targeting."""
    qualifications = profile.get("qualifications", []) + profile.get("education", [])
    qualification_text = " ".join(str(item) for item in qualifications)
    lower_second = bool(re.search(r"2\s*:\s*2|lower division|lower second", qualification_text, re.I))
    years = []
    for item in profile.get("education", []):
        if isinstance(item, dict) and re.search(r"degree|bachelor|BSc|certificate|PGCert|master", str(item), re.I):
            matches = re.findall(r"\b(?:19|20)\d{2}\b", str(item.get("end") or item.get("graduation_year") or ""))
            years.extend(int(year) for year in matches)
    now_year = datetime.now(timezone.utc).year
    result = []
    sentences = re.split(r"(?<=[.!?;])\s+|\n+", text)
    for sentence in sentences:
        if not sentence.strip():
            continue
        quote = sentence.strip()[:600]
        preferred = bool(re.search(r"preferred|desirable|ideally|may be|typically|nice.to.have|bonus", sentence, re.I))
        negated = bool(re.search(r"not required|no (?:degree.class|minimum degree)|regardless of|any degree classification", sentence, re.I))
        strict = not preferred and not negated and bool(re.search(r"must|require|minimum|at least|only|eligible|2\s*:\s*1\s*(?:or|and)\s*(?:above|higher)|achieved|hold|have graduated", sentence, re.I))
        degree_class = re.search(r"2\s*:\s*1|upper.second|first.class (?:degree|honours)", sentence, re.I)
        if degree_class and not negated:
            result.append({"name": "Degree classification", "status": "FAIL" if strict and lower_second else "UNKNOWN",
                           "blocking": strict, "source": job.get("url") or "manual paste", "evidence": quote})
        recent = re.search(r"graduat\w*.{0,35}(?:within|in) (?:the )?(?:past|last) (one|two|three|[1-5]) years?", sentence, re.I)
        window = re.search(r"graduat\w*.{0,30}\b(20\d{2})\s*(?:[-–]|to|or|and)\s*(20\d{2})\b", sentence, re.I)
        if recent or window:
            length = {"one": 1, "two": 2, "three": 3}.get(recent[1].lower(), int(recent[1]) if recent[1].isdigit() else 0) if recent else 0
            earliest = now_year - length if recent else int(window[1])
            latest = now_year if recent else int(window[2])
            meets = any(earliest <= year <= latest for year in years)
            status = "PASS" if meets else "FAIL" if years and strict else "UNKNOWN"
            result.append({"name": "Graduation window" if strict else "Recent-graduate targeting", "status": status,
                           "blocking": strict, "source": job.get("url") or "manual paste", "evidence": quote})
    return result


def assess_candidacy(job: dict, profile: dict, settings: dict, legacy: dict) -> dict:
    from .policy import role_text
    text = role_text(job)
    essential, preferred = _sections(text)
    title = str(job.get("title") or "")
    family = professional_family(job)
    records = _evidence(profile)
    declared = " ".join(str(item.get("name", "")) if isinstance(item, dict) else str(item) for item in profile.get("skills", []))
    support = _support_table(tuple((item["id"], item["text"], item["status"]) for item in records), declared)
    why, gaps, ids = [], [], []
    wanted = {name: pattern for name, pattern in TECH.items() if re.search(pattern, essential, re.I)}
    # Platform identity establishes a core competency even when the advert only
    # enumerates generic collaboration tools such as Git/GitHub.
    title_specialisms = [name for name, pattern in TITLE_TECH.items() if re.search(pattern, title, re.I)]
    for key in title_specialisms:
        wanted[key] = TECH[key]
    if family not in {"data_analytics", "professional_operations"}:
        wanted.pop("Excel", None)
        wanted.pop("Power BI", None)
    strengths, evidence_by_skill = {}, {}
    for name, pattern in wanted.items():
        strengths[name], evidence_ids = support[pattern]
        evidence_by_skill[name] = list(evidence_ids)
        ids.extend(evidence_by_skill[name])
    # ML-framework alternatives are one capability, not four independent gaps.
    if "ML framework" not in wanted and re.search(r"at least one.{0,40}(?:ML|machine learning) framework", essential, re.I):
        wanted["ML framework"] = TECH["ML framework"]
        strengths["ML framework"], evidence_ids = support[TECH["ML framework"]]
        evidence_by_skill["ML framework"] = list(evidence_ids)
        ids.extend(evidence_by_skill["ML framework"])
    missing = [name for name, strength in strengths.items() if strength == 0]
    supported = [name for name, strength in strengths.items() if strength > 0]
    core_score = 100 * sum(strengths.values()) / len(strengths) if strengths else 50
    if supported:
        why.append("Candidate evidence supports " + ", ".join(supported[:6]) + ".")
    if missing:
        gaps.append("Core advertised skills without candidate evidence: " + ", ".join(missing) + ".")
    preferred_missing = [name for name, pattern in TECH.items() if name not in wanted and re.search(pattern, preferred, re.I) and not support[pattern][0]]
    if preferred_missing:
        gaps.append("Optional skills to refresh or learn: " + ", ".join(preferred_missing) + ".")
    duties_scores = {}
    for name, pattern in RESPONSIBILITIES.items():
        if re.search(pattern, essential, re.I):
            value, evidence_ids = support[pattern]
            duties_scores[name] = value
            ids.extend(evidence_ids)
    responsibility = 100 * sum(duties_scores.values()) / len(duties_scores) if duties_scores else 45
    if duties_scores and any(duties_scores.values()):
        why.append("Supported responsibilities: " + ", ".join(name for name, value in duties_scores.items() if value) + ".")
    no_role_evidence = not strengths and not any(duties_scores.values())
    leadership = bool(re.search(r"\b(?:chief|head of|director|vice president|principal|staff|(?:engineering|research|development|people|team) manager)\b", title, re.I))
    senior = leadership or bool(re.search(r"\b(?:senior|lead)\b", title, re.I))
    years_unknown = [condition for condition in legacy.get("conditions", []) if condition.get("name") == "Mandatory experience" and condition.get("status") == "UNKNOWN"]
    high_years = any(re.search(r"\b(?:[5-9]|\d{2})\s*(?:\+\s*)?years", condition.get("evidence", ""), re.I) for condition in years_unknown)
    seniority = 25 if leadership else 40 if senior or high_years else 60 if years_unknown else 85
    if senior or years_unknown:
        gaps.append("Advertised professional seniority or specialist years require evidence; research and projects do not establish those years.")
    education = _education_conditions(job, profile, essential)
    conditions = [*legacy.get("conditions", []), *education]
    gaps.extend(f"{condition.get('name')}: {condition.get('evidence')}" for condition in legacy.get("conditions", [])
                if condition.get("status") == "UNKNOWN" and condition.get("blocking") and condition.get("name") in {"Qualification or experience", "Mandatory language"})
    blockers = list(legacy.get("blockers", []))
    for condition in education:
        if condition["status"] == "FAIL" and condition["blocking"]:
            blockers.append(condition["name"] + ": " + condition["evidence"])
        elif condition["status"] == "UNKNOWN":
            gaps.append(condition["name"] + ": " + condition["evidence"])
    if any(condition["name"] == "Mandatory student enrolment" and condition["status"] != "PASS" for condition in conditions):
        gaps.append("Current student enrolment has not been established.")
    london = bool(legacy.get("components", {}).get("london"))
    targets = legacy.get("components", {}).get("target_countries", [])
    location_score = 100 if london else 70 if targets else 0
    relevant_domain = any(re.search(r"physics|quantum|numerical|mathematical|scientific", record["text"], re.I) for record in records)
    domain = 90 if family in {"quantum", "quant_trading", "scientific_research"} and relevant_domain else 75 if family else 10
    components = {"core_skills": round(core_score, 1), "responsibilities": round(responsibility, 1),
                  "seniority": seniority, "domain": domain, "location": location_score}
    hiring_terms = sum((job.get("salary_min") is not None or job.get("salary_max") is not None,
                        job.get("office_days") is not None, job.get("sponsorship") not in (None, "unknown"))) / 3 * 100
    try:
        posted = datetime.fromisoformat(str(job.get("posted_at") or "").replace("Z", "+00:00"))
        posted = posted.replace(tzinfo=timezone.utc) if posted.tzinfo is None else posted
        posting_timestamp = min(posted.timestamp(), datetime.now(timezone.utc).timestamp())
    except (ValueError, TypeError, OverflowError):
        posting_timestamp = 0
    weights = settings.get("strategy", {}).get("weights") or STRATEGY_DEFAULTS["weights"]
    score = sum(components[key] * max(0, float(weights.get(key, 0))) for key in components)
    total_weight = sum(max(0, float(weights.get(key, 0))) for key in components)
    score = score / total_weight if total_weight else 0
    weighted_base, adjustments, band_reasons = score, [], []
    salary_state = legacy.get("components", {}).get("salary_state")
    if salary_state == "FAIL":
        gaps.append("Disclosed salary is below the current editable London preference.")
        adjustments.append({"type": "penalty", "amount": -8, "before": score, "after": score - 8,
                            "reason": "Advertised salary is below the existing London preference."})
        score -= 8
    if job.get("salary_min") is None and job.get("salary_max") is None:
        gaps.append("Salary is undisclosed; clarify the range before committing substantial application time.")
    if job.get("office_days") is None:
        gaps.append("Confirm office attendance and commute before applying.")
    if job.get("all_locations_required"):
        gaps.append("All advertised workplaces are mandatory; confirm travel, attendance and work permission for each location.")
    eligibility_unknown = any(condition.get("blocking") and condition.get("status") == "UNKNOWN" and condition.get("name") not in {"Vacancy status", "Mandatory experience"} for condition in conditions)
    essential_identity_missing = any(key in missing for key in title_specialisms)
    clamped = max(0, min(100, score))
    if clamped != score:
        adjustments.append({"type": "clamp", "amount": clamped - score, "before": score, "after": clamped,
                            "reason": "Keep the heuristic within its 0–100 scale."})
    score = clamped
    band = "strong" if score >= 78 else "plausible" if score >= 58 else "stretch" if score >= 35 else "not_suitable"
    platform_gap = ("Swift" in missing and bool(re.search(r"\biOS\b", title, re.I))) or ("Kotlin" in missing and "Java" not in supported and bool(re.search(r"\bAndroid\b", title, re.I))) or ("Unity" in missing and bool(re.search(r"\bUnity\b", title, re.I)))
    language_specialism_unverified = bool(re.search(r"locali[sz]ation|linguistic|proofread|translat", title, re.I)) and any(
        condition.get("name") == "Mandatory language" and condition.get("status") == "UNKNOWN" for condition in conditions)
    if not family or blockers or leadership and not profile.get("leadership_evidence") or no_role_evidence or platform_gap or language_specialism_unverified:
        band = "not_suitable"
        band_reasons = [reason for applies, reason in (
            (not family, "Outside the configured professional role families"),
            (bool(blockers), "Confirmed eligibility blocker: " + "; ".join(blockers)),
            (leadership and not profile.get("leadership_evidence"), "Leadership experience is not evidenced"),
            (no_role_evidence, "No core role evidence identified"),
            (platform_gap, "Core platform specialism is not evidenced"),
            (language_specialism_unverified, "The role's specialist language requirement is unverified")) if applies]
    elif essential_identity_missing or core_score < 40 and bool(wanted) or high_years:
        band = "stretch" if score >= 35 else "not_suitable"
        band_reasons = [reason for applies, reason in (
            (essential_identity_missing, "A title-defining technical skill is not evidenced"),
            (core_score < 40 and bool(wanted), "Limited support for essential technical skills"),
            (high_years, "Required specialist years are unverified")) if applies]
    elif senior or years_unknown:
        band = "stretch"
        band_reasons = ["Advertised seniority or specialist years are not established by projects or research alone"]
    elif (missing or eligibility_unknown or job.get("all_locations_required") or any(item["status"] == "UNKNOWN" for item in education)) and band == "strong":
        band = "plausible"
        band_reasons = [reason for applies, reason in (
            (bool(missing), "Unproven essential skills: " + ", ".join(missing)),
            (eligibility_unknown, "A mandatory eligibility condition needs checking"),
            (job.get("all_locations_required"), "All advertised workplace requirements need checking"),
            (any(item["status"] == "UNKNOWN" for item in education), "Qualification or graduation requirements need checking")) if applies]
    # Make numeric ordering agree with the evidence band, without disguising
    # component values or treating an eligibility failure as a low skill score.
    cap = {"strong": 100, "plausible": 77.9, "stretch": 57.9, "not_suitable": 34.9}[band]
    pre_cap_score = score
    if score > cap:
        adjustments.append({"type": "band_cap", "amount": cap - score, "before": score, "after": cap,
                            "reason": "; ".join(band_reasons) or f"Numeric ceiling for the {band} evidence band"})
    score = round(min(score, cap), 1)
    score_breakdown = {"rubric_version": CANDIDACY_RUBRIC_VERSION, "weighted_base": weighted_base,
        "components": [{"name": key, "value": value, "weight": max(0, float(weights.get(key, 0))) / total_weight if total_weight else 0,
                        "contribution": value * max(0, float(weights.get(key, 0))) / total_weight if total_weight else 0} for key, value in components.items()],
        "adjustments": adjustments, "pre_cap_score": pre_cap_score, "band_cap": cap, "band_reasons": band_reasons,
        "final_score": score, "formula": "Round to 1 decimal: min(band ceiling, clamp(weighted component mean + disclosed adjustments, 0, 100))."}
    if not family:
        gaps.insert(0, "Outside the configured professional target families.")
    if london:
        why.append("London or explicitly permitted UK remote work fits the primary search geography.")
    elif targets:
        why.append("An advertised work location matches a configured overseas location.")
    next_action = ("Skip the recommendation queue; review the listed blockers or core mismatch." if band == "not_suitable" else
                   "Treat as a stretch: verify the core skill and seniority gaps before preparing materials." if band == "stretch" else
                   "Review the source and clarify the listed gaps, then tailor a professional application." if band == "plausible" else
                   "Prioritise a tailored application using the linked candidate evidence.")
    return {"band": band, "score": score, "score_breakdown": score_breakdown, "why": why, "gaps": list(dict.fromkeys(gaps)), "next_action": next_action,
            "evidence_ids": list(dict.fromkeys(ids)), "components": {**components, "weights": dict(weights),
                "matched_core_skills": supported, "missing_core_skills": missing, "skill_evidence": evidence_by_skill,
                "title_specialisms": title_specialisms,
                "responsibility_support": duties_scores, "role_family": family or "other",
                "london": london, "target_countries": targets, "in_scope": bool(london or targets),
                "salary_state": salary_state, "specialist_years_unverified": bool(years_unknown),
                "hiring_terms": round(hiring_terms, 1), "posting_timestamp": posting_timestamp,
                "posting_date": job.get("posted_at"), "source_verified_at": job.get("last_verified")},
            "conditions": education, "blockers": list(dict.fromkeys(blockers))}


def candidacy_order(job: dict) -> tuple:
    candidacy = job.get("evaluation", {}).get("candidacy", {})
    bands = {"strong": 0, "plausible": 1, "stretch": 2, "not_suitable": 3}
    return (bands.get(candidacy.get("band"), 4), -float(candidacy.get("score", 0)),
            -float(candidacy.get("components", {}).get("hiring_terms", 0)),
            -float(candidacy.get("components", {}).get("posting_timestamp", 0)),
            not bool(candidacy.get("components", {}).get("london")),
            str(job.get("company") or "").casefold(), str(job.get("title") or "").casefold(), str(job.get("id") or job.get("url") or ""))


def professional_shortlist(jobs: list[dict], strategy: dict) -> list[dict]:
    from .policy import vacancy_is_closed
    counts, companies, seen, result = Counter(), Counter(), set(), []
    for job in sorted(jobs, key=candidacy_order):
        evaluation = job.get("evaluation", {})
        candidacy = evaluation.get("candidacy", {})
        band = candidacy.get("band")
        if band not in {"strong", "plausible", "stretch"} or not candidacy.get("components", {}).get("in_scope"):
            continue
        if evaluation.get("eligibility") == "blocked" or vacancy_is_closed(job) or job.get("duplicate_of") or job.get("sample"):
            continue
        stage = (job.get("application") or {}).get("stage", "not_started")
        if stage != "not_started" or job.get("status") in {"applied", "submitted", "interview", "offer", "rejected", "withdrawn", "dismissed", "closed"}:
            continue
        company = re.sub(r"\W", "", str(job.get("company") or "unknown").casefold())
        identity = str(job.get("url") or job.get("id") or (company, job.get("title")))
        group = "stretch" if band == "stretch" else "recommended"
        cap = int(strategy.get("stretch_size", 2) if group == "stretch" else strategy.get("shortlist_size", 10))
        if identity in seen or counts[group] >= cap or companies[company] >= int(strategy.get("per_company", 3)):
            continue
        result.append(job)
        seen.add(identity)
        counts[group] += 1
        companies[company] += 1
    return result
