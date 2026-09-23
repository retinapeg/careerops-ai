"""Evidence-preserving extraction and deterministic, editable career policies.

This module performs no I/O and never calls a model. Extracted facts contain a
literal source excerpt. Absence of an advert statement is not negative evidence.
Scores are documented heuristics, not hiring probabilities.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from functools import lru_cache
import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit


POLICY_VERSION = "2026-09-11.11-universe"
COUNTRIES = {
    "GB": ("United Kingdom", "UK", "Great Britain", "England"),
    "IL": ("Israel", "Israël", "ישראל"), "GR": ("Greece", "Ελλάδα", "Ελλάς", "Grèce", "Hellas"),
    "FR": ("France", "République française"),
    "CY": ("Cyprus", "Κύπρος", "Chypre"), "MT": ("Malta", "Malte"), "ES": ("Spain", "España", "Espagne"),
    "IT": ("Italy", "Italia", "Italie"), "US": ("United States", "USA"),
    "DE": ("Germany", "Deutschland", "Allemagne"), "CH": ("Switzerland", "Suisse"), "SG": ("Singapore",),
    "PL": ("Poland", "Polska", "Pologne"), "PT": ("Portugal",), "NL": ("Netherlands",), "IE": ("Ireland",),
}
CITIES = {
    "GB": ("London", "Canary Wharf", "Londres", "לונדון", "Λονδίνο"),
    "IL": ("Tel Aviv", "Tel-Aviv", "Haifa", "Herzliya", "Jerusalem", "תל אביב", "תל-אביב", "חיפה", "הרצליה", "ירושלים"),
    "GR": ("Crete", "Heraklion", "Chania", "Athens", "Thessaloniki", "Athina", "Ηράκλειο", "Κρήτη", "Χανιά", "Αθήνα", "Θεσσαλονίκη"),
    "FR": ("Nice", "Sophia Antipolis", "Marseille", "Montpellier", "Toulouse", "Paris", "Sophia-Antipolis", "Aix-en-Provence", "Côte d'Azur", "Île-de-France"),
    "CY": ("Nicosia", "Limassol", "Larnaca", "Paphos"),
    "MT": ("Valletta", "Sliema", "St Julian's"),
    "ES": ("Barcelona", "Valencia", "Malaga", "Málaga", "Alicante", "Madrid"),
    "IT": ("Milan", "Rome", "Naples", "Bologna", "Turin", "Trieste"),
    "US": ("New York", "Chicago", "Boston", "San Francisco", "San Jose"),
    "DE": ("Berlin", "Munich"), "CH": ("Zurich", "Zürich", "Geneva"),
    "SG": ("Singapore",), "PL": ("Kraków", "Krakow", "Warsaw", "Warszawa"),
    "PT": ("Lisbon", "Lisboa", "Porto"), "NL": ("Amsterdam",), "IE": ("Dublin",),
}
# Neutral starting cities for each overseas country: well-known cities listed
# alphabetically, drawn from CITIES so each one is recognised in adverts. The
# order of CITIES itself drives classification and is deliberately separate.
DEFAULT_CITIES = {
    "IL": ("Haifa", "Jerusalem", "Tel Aviv"), "GR": ("Athens", "Thessaloniki"),
    "FR": ("Marseille", "Paris", "Toulouse"), "CY": ("Larnaca", "Nicosia", "Paphos"),
    "MT": ("Sliema", "St Julian's", "Valletta"), "ES": ("Barcelona", "Madrid", "Valencia"),
    "IT": ("Milan", "Naples", "Rome"), "US": ("Chicago", "New York", "San Francisco"),
    "DE": ("Berlin", "Munich"), "CH": ("Geneva", "Zurich"), "SG": ("Singapore",),
    "PL": ("Krakow", "Warsaw"), "PT": ("Lisbon", "Porto"), "NL": ("Amsterdam",), "IE": ("Dublin",),
}
COUNTRY_CURRENCIES = {"IL": "ILS", "US": "USD", "CH": "CHF", "SG": "SGD", "PL": "PLN"}
CITY_ALIASES = {
    "תל אביב": "Tel Aviv", "תל-אביב": "Tel Aviv", "חיפה": "Haifa", "הרצליה": "Herzliya", "ירושלים": "Jerusalem",
    "Ηράκλειο": "Heraklion", "Κρήτη": "Crete", "Χανιά": "Chania", "Αθήνα": "Athens", "Athina": "Athens", "Θεσσαλονίκη": "Thessaloniki",
    "Sophia-Antipolis": "Sophia Antipolis", "Londres": "London", "לונדון": "London", "Λονδίνο": "London", "Tel-Aviv": "Tel Aviv",
}
SKILLS = {
    "Python": r"\bpython\b", "NumPy": r"\bnumpy\b", "pandas": r"\bpandas\b",
    "Jupyter": r"\bjupyter\b", "Git": r"\bgit(?:hub)?\b", "GitHub": r"\bgithub\b",
    "numerical methods": r"\bnumerical (?:methods|computing|modelling|modeling|analysis)\b",
    "Qiskit": r"\bqiskit\b", "QuTiP": r"\bqutip\b", "C++": r"(?<!\w)c\+\+(?!\w)",
    "C": r"\bc (?:programming|language)\b", "OpenCL": r"\bopencl\b",
    "MATLAB": r"\bmatlab\b", "Fortran": r"\bfortran\b", "GPU": r"\bgpu\b",
    "HPC": r"\bhpc\b|\bhigh.performance computing\b", "Excel": r"\bexcel\b",
    "Power BI": r"\bpower\s?bi\b", "SQL": r"\bsql\b", "Java": r"\bjava\b",
    "JavaScript": r"\bjavascript\b", "TypeScript": r"\btypescript\b",
    "React": r"\breact(?:js)?\b", "Rust": r"\brust\b", "Docker": r"\bdocker\b",
    "Kubernetes": r"\bkubernetes\b", "AWS": r"\baws\b", "Azure": r"\bazure\b",
    "PyTorch": r"\bpytorch\b", "TensorFlow": r"\btensorflow\b",
    "accounts receivable": r"\baccounts receivable\b", "collections": r"\bcollections\b",
    "credit control": r"\bcredit control\b", "payroll": r"\bpayroll\b",
    "NetSuite": r"\bnetsuite\b", "Recurly": r"\brecurly\b",
    "Salesforce": r"\bsalesforce\b", "Zendesk": r"\bzendesk\b",
    "C#": r"(?<!\w)c#(?!\w)", "Unity": r"\bunity\b", "game design": r"\bgame design\b",
    "game development": r"\bgame development\b", "VLSI": r"\bvlsi\b",
    "physical design": r"\bphysical design\b", "TCL": r"\btcl\b", "Bash": r"\bbash\b",
    "Perl": r"\bperl\b", "EDA": r"\beda\b", "static timing analysis": r"\bstatic timing analysis\b",
    "Kotlin": r"\bkotlin\b", "Android": r"\bandroid\b", ".NET": r"(?<!\w)\.net\b",
}
QUANT_PATTERN = re.compile(
    r"\bquant(?:itative)?\b|scientific (?:program|comput)|research engineer|"
    r"trading (?:technology|systems|software)|numerical (?:model|method)|"
    r"\b(?:mathematical|numerical|convex|combinatorial|portfolio|stochastic) optimi[sz]ation\b|"
    r"\boptimi[sz]ation (?:engineer|scientist|researcher)\b|\bquantum\b|(?:risk|financial) modell?ing", re.I
)
TECH_PATTERN = re.compile(
    r"python|software|\bdata (?:analyst|science|scientist|engineer|quality)|"
    r"\bresearch\b|machine learning|\bAI\b|technical|implementation|solutions|"
    r"numerical|quant|systems|analytics|modelling|modeling|physics", re.I
)
BLOCKER_NAMES = {
    "closed vacancy", "mandatory qualification", "mandatory driving", "work authorisation",
    "mandatory language", "mandatory clearance", "mandatory citizenship", "unpaid employment",
    "pay to apply", "commission only", "secondary school teaching", "explicit exclusion", "mandatory experience", "mandatory student enrolment",
}
TERMINAL_STATES = {"applied", "interview", "offer", "closed", "dismissed", "submitted", "rejected"}
CLOSED_PATTERN = re.compile(r"(?:this (?:job|position|vacancy|role)|applications?)(?: is| are| has been)? (?:now )?(?:closed|filled)|no longer accepting applications|(?:job|position|vacancy|role) (?:has )?expired|(?:job|position|vacancy|role) (?:is )?no longer (?:available|open)", re.I)


def vacancy_is_closed(job: dict) -> bool:
    """A stopped application workflow is not source evidence of a closed job."""
    if _source_says_closed(str(job.get("description") or "")):
        return True
    evidence = list(job.get("evidence") or []) + list(job.get("conditions") or [])
    closure = job.get("closure_evidence")
    if isinstance(closure, dict):
        evidence.append(closure)
    for item in evidence:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or item.get("name") or "").casefold()
        quote = str(item.get("quote") or item.get("evidence") or "")
        if (field in {"vacancy_status", "closed vacancy", "vacancy_closed", "verification"} or item is closure) and item.get("source"):
            if _source_says_closed(quote):
                return True
            if quote.casefold().strip() == "closed" and item.get("status") in {"verified", "source_verified"}:
                return True
    return False


@lru_cache(maxsize=2048)
def _source_says_closed(text: str) -> bool:
    return bool(CLOSED_PATTERN.search(text))


def default_settings() -> dict:
    """Return an independent editable settings document; no execution is enabled."""
    from .professional import STRATEGY_DEFAULTS
    return {
        "version": 1, "policy_version": POLICY_VERSION,
        "strategy": deepcopy(STRATEGY_DEFAULTS),
        "lanes": {"mediterranean": True, "overseas_quant": True, "london": True,
                  "exceptional": True, "cashflow": False, "overseas_quant_worldwide": False},
        # Every overseas country is listed but none is enabled: the user chooses
        # which markets to search, with equal priority until they say otherwise.
        "locations": {country: {"enabled": False, "cities": list(DEFAULT_CITIES[country]),
                                "excluded_cities": [], "priority": 50, "salary_min": None,
                                "currency": COUNTRY_CURRENCIES.get(country, "EUR")}
                      for country in sorted(COUNTRIES) if country != "GB"},
        # Illustrative salary figures for a fresh install, not recommendations;
        # each user sets their own floors and exceptional trigger in Settings.
        "london": {"salary_remote_one_day": 35000, "salary_two_days": 38000,
                   "salary_three_plus_days": 42000, "unknown_salary_min_fit": 80,
                   "unknown_salary_min_priority": 80},
        "exceptional": {"base_gbp": 80000, "markets": {}},
        "thresholds": {"mediterranean": {"fit": 45, "priority": 60},
                       "overseas_quant": {"fit": 35, "priority": 55},
                       "london": {"fit": 65, "priority": 75},
                       "exceptional": {"fit": 40, "priority": 60},
                       "cashflow": {"fit": 35, "priority": 45}},
        "weights": {"fit": {"skills": .55, "domain": .25, "responsibility": .20},
                    "value": {"location": .35, "career": .30, "compensation": .20, "work_pattern": .15},
                    "priority": {"fit": .50, "value": .35, "freshness": .10, "simplicity": .05}},
        "queue": {"recommended": 6, "stretch": 2, "london": 2, "per_company": 2,
                  "unknown_salary_london": 1},
        "providers": {"active": "none", "model": "", "billing_mode": "disabled", "budget_usd": 0},
        "inventory": {"policy": "professional", "region": "all", "per_page": 50, "include_stretch": True},
        "search": {"scope": "london", "registry": [], "directory_sources": [], "historical_sources": [], "scope_budgets": {},
                   "coverage": {"query_objective": 50, "board_objective": 100, "max_requests": 1000,
                                "max_new_employer_requests": 150, "max_board_requests": 400, "max_vacancy_requests": 400,
                                "max_historical_requests": 0, "max_ai_reviews": 25, "per_host_limit": 200, "timeout_seconds": 900,
                                "mode_defaults": {
                                    "normal": {"query_objective": 12, "board_objective": 25, "max_requests": 120,
                                               "max_new_employer_requests": 40, "max_board_requests": 50, "max_vacancy_requests": 25,
                                               "max_historical_requests": 0, "max_ai_reviews": 5, "per_host_limit": 40, "timeout_seconds": 180},
                                    "deep": {"query_objective": 30, "board_objective": 60, "max_requests": 400,
                                             "max_new_employer_requests": 100, "max_board_requests": 150, "max_vacancy_requests": 120,
                                             "max_historical_requests": 0, "max_ai_reviews": 15, "per_host_limit": 100, "timeout_seconds": 600},
                                    "bootstrap": {}}},
                   "sources": [
                       {"type": "greenhouse", "company": "Monzo", "url": "https://job-boards.greenhouse.io/monzo", "enabled": True},
                       {"type": "greenhouse", "company": "Anthropic", "url": "https://job-boards.greenhouse.io/anthropic", "enabled": True},
                       {"type": "ashby", "company": "OpenAI", "url": "https://jobs.ashbyhq.com/openai", "enabled": True},
                       {"type": "lever", "company": "Palantir", "url": "https://jobs.lever.co/palantir", "enabled": True},
                   ], "role_families": ["software engineer", "data analyst", "solutions engineer", "technical support", "QA analyst"],
                   "normal": {"max_pages": 12, "max_jobs": 80, "max_queries": 6, "max_turns": 6,
                              "concurrency": 2, "timeout_seconds": 90, "max_retries": 1},
                   "deep": {"max_pages": 36, "max_jobs": 240, "max_queries": 18, "max_turns": 16,
                            "concurrency": 2, "timeout_seconds": 240, "max_retries": 1}},
        "schedules_enabled": False,
    }


def upgrade_professional_settings(settings: dict) -> dict:
    """One-time strategy migration; retain user salary, location and other edits.

    Call with the raw stored settings, before filling defaults. After migration,
    subsequent user scope/strategy edits are authoritative and survive restarts.
    """
    upgraded = deepcopy(settings)
    from .professional import STRATEGY_DEFAULTS
    previous = upgraded.get("strategy") if isinstance(upgraded.get("strategy"), dict) else {}
    if previous.get("migration_version", 0) >= 1:
        return upgraded
    upgraded["strategy"] = _merged(deepcopy(STRATEGY_DEFAULTS), previous)
    upgraded["strategy"].update(mode="professional_london_first", migration_version=1)
    upgraded.setdefault("search", {})["scope"] = "london"
    return upgraded


def default_profile() -> dict:
    """Start without candidate claims; private facts must be entered explicitly."""
    return {
        "version": 1, "name": "", "location": "", "drives": None, "availability": "",
        "contact": {}, "qualifications": [], "qualifications_not_held": [], "education": [],
        "employment": [], "research": [], "projects": [], "skills": [], "skill_levels": {}, "domains": [],
        "languages": {}, "work_authorisation": {}, "citizenships": [], "currently_enrolled": None,
        "evidence": [],
    }


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _plain(text: str) -> str:
    if not re.search(r"</?[a-z][^>]*>", text, re.I):
        return text.strip()
    parser = _TextParser()
    parser.feed(text)
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


def _has(text: str, phrase: str) -> bool:
    def fold(value: str) -> str:
        return "".join(char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char)).casefold()
    return bool(re.search(r"(?<!\w)" + re.escape(fold(phrase)) + r"(?!\w)", fold(text)))


def country_codes(location: str) -> list[str]:
    """Countries explicitly named by a location field, including local scripts.

    This helper must not be run on company marketing or whole adverts to infer a
    work location. It returns all alternatives rather than guessing one country.
    """
    if not isinstance(location, str) or not location.strip():
        return []
    return list(_cached_country_codes(location))


@lru_cache(maxsize=4096)
def _cached_country_codes(location: str) -> tuple[str, ...]:
    if location.strip().upper() in COUNTRIES:
        return (location.strip().upper(),)
    found = {code for code, names in COUNTRIES.items() if any(_has(location, name) for name in names)}
    found.update(code for code, names in CITIES.items() if any(_has(location, name) and (name != "Nice" or re.search(r"\bNice\b", location)) for name in names))
    return tuple(sorted(found))


def _country(location: str) -> str | None:
    found = country_codes(location)
    return found[0] if len(found) == 1 else None


@lru_cache(maxsize=4096)
def _city(location: str, country: str | None) -> str | None:
    found = next((city for city in CITIES.get(country, ()) if _has(location, city)), None)
    return next((canonical for alias, canonical in CITY_ALIASES.items() if found and _has(found, alias)), found)


def location_options(job: dict) -> list[dict]:
    """Resolve actual employee-location fields, never employer/customer mentions.

    ATS ``available_locations`` are authoritative alternatives. A bare remote
    label does not supply country permission. Contradictory scalar country data
    cannot turn an explicitly London-only location into an overseas opportunity.
    """
    source = str(job.get("url") or "supplied location")
    location = str(job.get("location") or "")
    forbidden = re.search(r"headquarters|head office|our offices|offices in|customers|clients", location, re.I)
    primary = [] if forbidden else country_codes(location)
    declared = country_codes(str(job.get("country") or ""))
    if not primary and not forbidden:
        primary = declared
    if job.get("all_locations_required") and "GB" in primary and len(primary) > 1:
        # A mandatory London office cannot be converted to an optional overseas
        # alternative merely because another required office is listed.
        primary = ["GB"]
    options = [{"country": code, "city": _city(location, code), "location": location,
                "source": source, "kind": "primary"} for code in primary]
    alternatives = job.get("available_locations") or []
    if isinstance(alternatives, (str, dict)):
        alternatives = [alternatives]
    if not job.get("all_locations_required"):
        for alternative in alternatives:
            if isinstance(alternative, str):
                label, alternative_source = alternative, source
                codes = country_codes(label)
            elif isinstance(alternative, dict):
                label = str(alternative.get("location") or alternative.get("name") or alternative.get("city") or "")
                codes = country_codes(label) or country_codes(str(alternative.get("country") or alternative.get("country_code") or ""))
                alternative_source = str(alternative.get("source") or source)
            else:
                continue
            if re.search(r"headquarters|customers|clients", label, re.I):
                continue
            options.extend({"country": code, "city": _city(label, code), "location": label,
                            "source": alternative_source, "kind": "alternative"} for code in codes)
    if job.get("office_days") == 0:
        countries = job.get("remote_countries") or []
        if isinstance(countries, list):
            for country in countries:
                codes = ["WORLDWIDE"] if str(country).upper() == "WORLDWIDE" else country_codes(str(country))
                options.extend({"country": code, "city": None, "location": str(country), "source": source, "kind": "remote"} for code in codes)
    if job.get("office_days") == 0 and job.get("remote_countries"):
        options = [item for item in options if item["kind"] == "remote"]
    unique = {}
    for item in options:
        unique.setdefault((item["country"], item["city"], item["kind"]), item)
    return list(unique.values())


def _excerpt(text: str, match: re.Match) -> str:
    start = max(text.rfind("\n", 0, match.start()), text.rfind(". ", 0, match.start()) + 1, 0)
    ends = [pos for pos in (text.find("\n", match.end()), text.find(". ", match.end())) if pos >= 0]
    end = min(ends) + 1 if ends else len(text)
    return text[start:end].strip()[:500]


def _record(job: dict, field: str, value: Any, quote: str, source: str, status: str = "extracted") -> None:
    if job.get(field) is None or job.get(field) in ("", "unknown", "UNKNOWN") or job.get(field) == []:
        job[field] = value
        job["evidence"].append({"field": field, "quote": quote, "source": source, "status": status})


def _number(value: str, k: str = "") -> float:
    value = value.replace(" ", "")
    separators = [index for index, char in enumerate(value) if char in ",."]
    if separators:
        decimal = separators[-1] if len(value) - separators[-1] - 1 in {1, 2} else None
        value = "".join("." if index == decimal else "" if char in ",." else char for index, char in enumerate(value))
    return float(value) * (1000 if k else 1)


def _salary(job: dict, text: str, source: str) -> None:
    if job.get("salary_geography_unresolved") is True:
        # Source compensation tiers remain in compensation_details. A currency
        # elsewhere in a multi-location description cannot restore a local rate.
        job.update(salary_min=None, salary_max=None, salary_currency=None,
                   salary_type="unknown", salary_period="unknown")
        return
    number = r"(?:\d{1,3}(?:[,. ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
    prefix = re.compile(r"(?P<currency>£|€|\$|₪|GBP\s*|EUR\s*|USD\s*|ILS\s*)\s*"
                        rf"(?P<low>{number})\s*(?P<lowk>[kK]?)"
                        r"(?:\s*(?:-|–|—|to)\s*(?:£|€|\$|₪|GBP|EUR|USD|ILS)?\s*"
                        rf"(?P<high>{number})\s*(?P<highk>[kK]?))?", re.I)
    suffix = re.compile(rf"(?P<low>{number})\s*(?P<lowk>[kK]?)"
                        rf"(?:\s*(?:-|–|—|to)\s*(?P<high>{number})\s*(?P<highk>[kK]?))?"
                        r"\s*(?P<currency>GBP|EUR|USD|ILS|€|£|₪)(?!\w)", re.I)
    matches = list(prefix.finditer(text))
    matches.extend(match for match in suffix.finditer(text) if not any(match.start() < item.end() and item.start() < match.end() for item in matches))
    parsed = []
    for match in sorted(matches, key=lambda item: item.start()):
        quote = _excerpt(text, match)
        low = _number(match["low"], match["lowk"] or (match["highk"] if match["high"] else ""))
        high = _number(match["high"], match["highk"] or match["lowk"]) if match["high"] else low
        # Do not truncate an unsupported magnitude such as $10M into $10,
        # or promote a contradictory extracted range to asserted source facts.
        if (match.end() < len(text) and text[match.end()].isalpha() and not text[match.end() - 1].isspace()) or low > high:
            continue
        currency = {"£": "GBP", "€": "EUR", "$": "USD" if job.get("country") == "US" else "unknown", "₪": "ILS"}.get(match["currency"].strip(), match["currency"].strip().upper())
        before = text[max(0, match.start() - 50):match.start()].lower()
        after = text[match.end():match.end() + 90].lower()
        segment = before + " " + after
        other_before = re.search(r"(bonus|equity|stock|sign.on)\s*(?::|of|up to|worth|valued at)?\s*$", before)
        other_after = re.match(r"\s*(?:in )?(bonus|equity|stock)\b", after)
        period = "unknown"
        for name, regex in (("annual", r"(?:per |a |/)(?:year|annum)\b|annual|\bp\.?a\.?\b"),
                            ("month", r"(?:per |a |/)(?:month)\b|monthly"),
                            ("hour", r"(?:per |an? |/)(?:hour|hr)\b|hourly"),
                            ("day", r"(?:per |a |/)(?:day)\b|daily")):
            if re.search(regex, segment):
                period = name
                break
        explicit_base = bool(re.search(r"base(?: salary)?\s*(?::|of|up to|from)?\s*$", before) or re.match(r"\s*(?:(?:per |a |/)(?:year|annum)\s*)?base\b", after))
        explicit_ote = bool(re.search(r"(?:\bote|on.target earnings|total compensation)\s*(?::|of|up to|from)?\s*$", before) or re.match(r"\s*(?:(?:per |a |/)(?:year|annum)\s*)?(?:ote\b|on.target earnings|total compensation)", after))
        # Employer revenue/funding is not employee compensation. In particular
        # '$10 billion in lifetime revenue' must not become a $10 base salary.
        if not explicit_base and not explicit_ote and re.search(r"\b(?:revenue|turnover|valuation|funding|market cap|investment raised|annual spend|customer spend|client spend|account value|contract value|deal size|portfolio value)\b", quote, re.I):
            continue
        allowance = re.search(r"\b(?:allowance|wellness|working from home|equipment budget|learning budget|meal vouchers?|reimbursement)\b", quote, re.I)
        salary_type = "base" if explicit_base else "ote" if explicit_ote or re.search(r"\bote\b|on.target earnings|total compensation", quote, re.I) else "base"
        if re.search(r"(?:up to|maximum of)\s*$", before):
            low = None
        elif re.search(r"(?:from|minimum of|starting at)\s*$", before) or after.lstrip().startswith("+"):
            high = None
        if other_before or other_after:
            salary_type = (other_before or other_after)[1]
        elif allowance and not explicit_base and not re.search(r"\b(?:salary|wage|base pay)\b", quote, re.I):
            salary_type = "allowance"
        elif not explicit_base and not explicit_ote and period == "unknown" and not re.search(r"\b(?:salary|pay|wage|compensation|earnings|remuneration)\b", quote, re.I):
            # A bare monetary amount does not establish base compensation.
            salary_type = "unclassified_amount"
        parsed.append({"amount_min": low, "amount_max": high, "currency": currency,
                       "period": period, "type": salary_type, "evidence": quote,
                       "source": source, "explicit_base": explicit_base})
    choices = [item for item in parsed if item["type"] in {"base", "ote"}]
    if not choices:
        if parsed:
            job["other_compensation"] = parsed
        return
    chosen = next((item for item in choices if item["explicit_base"]), next((item for item in choices if item["type"] == "base"), choices[0]))
    for field, key in (("salary_min", "amount_min"), ("salary_max", "amount_max"), ("salary_currency", "currency"),
                       ("salary_period", "period"), ("salary_type", "type")):
        _record(job, field, chosen[key], chosen["evidence"], source)
    other = [item for item in parsed if item is not chosen]
    if other:
        job["other_compensation"] = other


def _requirements(text: str, source: str) -> list[dict]:
    requirements = []
    mandatory = r"\brequired\b|\bmandatory\b|\bessential\b|\bmust\b|\brequire[sd]?\b|\bminimum\b"
    patterns = [
        ("qualification", "PhD", r"\bph\.?d\.?\b|\bdoctorate\b|doctoral degree"),
        ("qualification", "MSc", r"\bm\.?sc\.?\b|\bmaster['’]?s(?: (?:degree|qualification))?\b"),
        ("qualification", "degree", r"\bbachelor['’]?s(?: (?:degree|qualification))?\b|\bb\.?s(?:c)?\.?\b|\bb\.?a\.?\b"),
        ("driving", "driving", r"driving licen[cs]e|driver'?s licen[cs]e|\bmust drive\b|\bown (?:a )?car\b"),
        ("language", "Hebrew", r"\bhebrew\b"), ("language", "French", r"\bfrench\b"),
        ("language", "Greek", r"\bgreek\b"), ("language", "English", r"\benglish\b"),
        ("language", "Italian", r"\bitalian\b"), ("language", "Spanish", r"\bspanish\b"),
        ("language", "Kazakh", r"\bkazakh\b"), ("language", "Russian", r"\brussian\b"),
        ("clearance", "security clearance", r"(?:active|current|existing) (?:security |SC |DV )?clearance"),
        ("citizenship", "US", r"(?:US|U\.S\.|United States) citizen(?:ship)?"),
        ("citizenship", "GB", r"(?:British|UK|United Kingdom) citizen(?:ship)?"),
    ]
    for kind, value, pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            quote = _excerpt(text, match)
            if kind == "qualification":
                # MSc. and Ph.D. are not sentence boundaries. Preserve the
                # whole degree-alternative clause, including curly apostrophes.
                start = max(text.rfind("\n", 0, match.start()) + 1, text.rfind(";", 0, match.start()) + 1, match.start() - 180)
                ends = [end for end in (text.find("\n", match.end()), text.find(";", match.end())) if end >= 0]
                end = min(ends) if ends else min(len(text), match.end() + 300)
                quote = text[start:end].strip()[:500]
            negative = bool(re.search(r"not (?:required|mandatory|essential)|no .{0,25}(?:required|necessary)", quote, re.I))
            must = bool(re.search(mandatory, quote, re.I)) and not negative
            headings = list(re.finditer(r"(?:^|\n|[.;]\s+)(?:#+\s*)?((?:(?:basic|minimum|essential|required|preferred) )?(?:requirements|qualifications|education|skills)|nice to have|benefits|responsibilities|about us)\s*:?\s*$", text[:match.start()], re.I | re.M))
            if headings and re.search(r"^(?:(?:basic|minimum|essential|required) )?(?:requirements|qualifications|education|skills)$", headings[-1][1], re.I):
                must = not negative and not bool(re.search(r"preferred|desirable|a plus|advantage", quote, re.I))
                context = text[headings[-1].start():match.end()].strip()
                if kind != "qualification" and len(context) <= 500:
                    quote = context
            if kind == "language" and re.search(r"(?:fluent|fluency|native|proficien\w+).{0,20}" + re.escape(value), quote, re.I):
                must = not bool(re.search(r"preferred|desirable|a plus|advantage|not required", quote, re.I))
            equivalent = bool(re.search(r"or (?:an? )?(?:equivalent|comparable)|equivalent .{0,35}(?:accepted|considered)|or .{0,25}experience", quote, re.I))
            alternatives = []
            if kind == "qualification":
                values = [item_value for item_kind, item_value, item_pattern in patterns
                          if item_kind == "qualification" and re.search(item_pattern, quote, re.I)]
                if len(values) > 1 and re.search(r"\bor\b|/", quote, re.I):
                    alternatives = values
                if re.search(r"curriculum|\bpursuing\b|\benrolled\b|currently studying", quote, re.I):
                    # An internship within a degree asks for current enrolment,
                    # not possession of an already completed higher award.
                    requirements.append({"kind": "student_enrolment", "value": "current student enrolment", "mandatory": must,
                                         "equivalent_accepted": False, "evidence": quote, "source": source})
                    must = False
                if re.search(r"academic research|research.heavy|such as|for example|e\.g\.", quote, re.I) and not re.search(r"(?:Ph\.?D\.?|M\.?Sc\.?)\s+(?:is )?(?:required|mandatory|essential)", quote, re.I):
                    must = False
            requirements.append({"kind": kind, "value": value, "mandatory": must,
                                 "proficiency": "native" if kind == "language" and re.search(r"\bnative\b", quote, re.I) else None,
                                 "equivalent_accepted": equivalent, "accepted_qualifications": alternatives,
                                 "evidence": quote, "source": source})
    for match in re.finditer(r"\b(?:currently\s+)?(?:pursuing|enrolled in|studying (?:for|towards))\s+(?:an?\s+)?[^.\n]{0,100}(?:degree|bachelor|master|university|college)|\b(?:must|should) be (?:a )?(?:current |registered )?student\b", text, re.I):
        requirements.append({"kind": "student_enrolment", "value": "current student enrolment", "mandatory": True,
                             "equivalent_accepted": False, "evidence": _excerpt(text, match), "source": source})
    # Keep a multi-line years requirement together with its actual discipline.
    # A company history, preferred years, and candidate experience are distinct.
    years_pattern = re.compile(r"\b(?:(at least|minimum(?: of)?|more than)\s+)?(\d{1,2})(\+)?\s*(?:years?|yrs?)\b", re.I)
    areas = ("product operations", "business development", "product lifecycle management", "software engineering",
             "software development", "data science", "machine learning", "quantitative research", "finance",
             "technical support", "customer success", "implementation", "research", "sales", "operations",
             "collections", "accounts receivable", "credit control", "accounting", "payroll", "corporate finance", "fp&a",
             "game design", "game development", "engineering management", "people management", "team management")
    for match in years_pattern.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        sentences = list(re.finditer(r"[.!?]\s+", text[:match.start()]))
        sentence_start = sentences[-1].end() if sentences else 0
        start = max(line_start, sentence_start, match.start() - 100, 0)
        tail = text[match.end():match.end() + 280]
        end_match = re.search(r"[.!](?:\s|$)|\n\s*\n|\n\s*[-•]|\n\s*(?:required|essential|mandatory)\s*(?=\n|$)", tail, re.I)
        end = min(match.end() + end_match.end() if end_match else match.end() + 280, start + 380, len(text))
        # A complete same-line requirement ends before the next bullet. Keep
        # genuine wrapped '7 years / in / Product Operations' clauses together.
        newline = text.find("\n", match.end(), end)
        if newline >= 0 and len(text[match.end():newline].strip().split()) >= 3:
            end = newline
        quote = text[start:end].strip()
        # Preserve a literal degree OR experience route. Dots inside B.S. are
        # not sentence boundaries, and the years cannot become an independent
        # mandatory condition when the preceding word is explicitly "or".
        prefix = text[max(line_start, match.start() - 500):match.start()]
        alternative_awards = [award for kind, award, pattern in patterns
                              if kind == "qualification" and re.search(pattern, prefix, re.I)]
        degree_alternative = bool(alternative_awards and re.search(r"\bor\s*$", prefix, re.I))
        if degree_alternative:
            quote = text[max(line_start, match.start() - 500):end].strip()
        if re.search(r"(?:our company|the company|we) (?:has|have)|founded|operating for|company history", quote, re.I):
            continue
        preference_scope = re.sub(r"\([^)]*\)", "", quote)
        preferred = bool(re.search(r"preferred|preferably|desirable|a plus|nice to have|not required", preference_scope, re.I))
        must = bool(match[1] or match[3] or re.search(r"\brequired\b|\bessential\b|\bmust\b|\bmandatory\b", quote, re.I)) and not preferred
        scopes = [area for area in areas if _has(quote, area)]
        # Do not duplicate 'operations' when a more specific discipline exists.
        if any(area != "operations" and "operations" in area for area in scopes) and "operations" in scopes:
            scopes.remove("operations")
        requirements.append({"kind": "experience", "value": ", ".join(scopes) or "relevant professional experience",
                             "areas": scopes, "minimum_years": int(match[2]), "mandatory": must,
                             "alternative_qualifications": alternative_awards if degree_alternative else [],
                             "equivalent_accepted": bool(re.search(r"or (?:an? )?equivalent|equivalent experience", quote, re.I)),
                             "evidence": quote, "source": source})
    return requirements


def _temporary_remote_benefit(quote: str) -> bool:
    return bool(re.search(r"(?:for|up to|maximum of)\s+\d+\s+(?:days?|weeks?|months?).{0,45}(?:a|per|each)\s+year|workation|work.from.anywhere (?:benefit|allowance)", quote, re.I))


def extract_job(description: str, supplied: dict | None = None) -> dict:
    """Conservative offline extraction; explicit supplied facts take precedence.

    Supplied structured values are user/source assertions, not verified facts.
    Unknowns remain null. This cannot infer every natural-language requirement;
    uncertain overseas feasibility and unverified vacancies require review.
    """
    if not isinstance(description, str):
        raise ValueError("Job description must be text")
    if supplied is not None and not isinstance(supplied, dict):
        raise ValueError("Supplied job facts must be an object")
    job = deepcopy(supplied or {})
    text = _plain(description or str(job.get("description") or job.get("text") or ""))
    source = str(job.get("url") or "manual paste")
    job["description"] = text
    job["evidence"] = list(job.get("evidence") or [])
    # A source adapter may report closure as a structured status rather than
    # advert prose. Preserve that literal field before Store restores the
    # user's application status. Legacy terminal workflows are not receipts.
    if (job.get("status") == "closed" and job.get("vacancy_status") == "closed"
            and not job.get("legacy") and not job.get("verification_error")):
        try:
            observed_at = datetime.fromisoformat(str(job.get("last_verified") or "").replace("Z", "+00:00"))
            source_url = urlsplit(source)
            verified_source = observed_at.tzinfo is not None and source_url.scheme in {"http", "https"} and bool(source_url.hostname)
        except ValueError:
            verified_source = False
        if verified_source:
            receipt = {"field": "vacancy_status", "quote": "closed", "source": source,
                       "status": "source_verified", "observed_at": job["last_verified"],
                       "basis": "supplied_source_status"}
            if receipt not in job["evidence"]:
                job["evidence"].append(receipt)
    for field in ("title", "company", "location", "country", "city", "salary_min", "salary_max",
                  "salary_currency", "salary_period", "salary_type", "office_days", "remote_countries",
                  "sponsorship", "posted_at", "requisition_id"):
        if job.get(field) is not None and job.get(field) != "" and not any(item.get("field") == field for item in job["evidence"]):
            job["evidence"].append({"field": field, "quote": str(job[field]), "source": source,
                                    "status": "supplied"})
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for field, pattern in (("title", r"^(?:job title|role|position)\s*:\s*(.+)$"),
                           ("company", r"^(?:company|employer)\s*:\s*(.+)$"),
                           ("location", r"^(?:job )?location\s*:\s*(.+)$"),
                           ("requisition_id", r"^(?:requisition|job|req)(?: id| number| #)?\s*:\s*(.+)$")):
        match = re.search(pattern, text, re.I | re.M)
        if match:
            _record(job, field, match[1].strip(), match[0], source)
    if not job.get("title") and lines and len(lines[0]) <= 140 and TECH_PATTERN.search(lines[0]):
        _record(job, "title", lines[0], lines[0], source)
    if not job.get("location"):
        candidates = lines[:3]
        based = re.search(r"(?:based|located) in\s+([^.;\n]{2,100})", text, re.I)
        if based:
            candidates.insert(0, based[1])
        for candidate in candidates:
            if re.search(r"headquarters|head office|our offices|offices in|customers|clients", candidate, re.I):
                continue
            if _country(candidate):
                _record(job, "location", candidate, candidate, source)
                break
    location = str(job.get("location") or "")
    code = _country(location)
    if code:
        _record(job, "country", code, location, source, "extracted" if location in text else "derived_from_supplied")
    if job.get("country"):
        job["country"] = _country(str(job["country"])) or str(job["country"]).upper()
    city = _city(location, job.get("country"))
    if city:
        _record(job, "city", city, location, source, "extracted" if location in text else "derived_from_supplied")
    _salary(job, text, source)
    days = re.search(r"\b([0-5]|zero|one|two|three|four|five)\s*days?\s*(?:(?:a|per|each)\s*week\s*)?(?:in (?:the )?office|on.?site|office attendance)", text, re.I)
    if not days:
        days = re.search(r"\b(?:in (?:the )?office|on.?site)\s*([0-5]|zero|one|two|three|four|five)\s*days?", text, re.I)
    if days:
        value = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}.get(days[1].lower())
        _record(job, "office_days", value if value is not None else int(days[1]), _excerpt(text, days), source)
    elif re.search(r"fully remote|100% remote|remote.only", text, re.I):
        match = re.search(r"fully remote|100% remote|remote.only", text, re.I)
        if not _temporary_remote_benefit(_excerpt(text, match)):
            _record(job, "office_days", 0, _excerpt(text, match), source)
    elif re.search(r"five.days? .?on.?site|full.time on.?site|on.?site (?:every|each) (?:working |week)?day", text, re.I):
        match = re.search(r"five.days? .?on.?site|full.time on.?site|on.?site (?:every|each) (?:working |week)?day", text, re.I)
        _record(job, "office_days", 5, _excerpt(text, match), source)
    if job.get("office_days") is not None:
        if isinstance(job["office_days"], bool) or not isinstance(job["office_days"], (int, float)) or job["office_days"] not in range(6):
            raise ValueError("Office days must be a number from 0 to 5 or null")
        job["office_days"] = int(job["office_days"])
    remote = re.search(r"(?:work (?:remotely )?from|remote (?:within|from|in)|remote.{0,25}(?:permitted|allowed|eligible) countries\s*:?)\s*([^.;\n]{2,160})", text, re.I)
    if remote and not _temporary_remote_benefit(_excerpt(text, remote)):
        permitted = [code for code, names in COUNTRIES.items() if any(_has(remote[1], name) for name in names)]
        if re.search(r"anywhere|worldwide|any country", remote[1], re.I):
            permitted = ["WORLDWIDE"]
        if permitted:
            _record(job, "remote_countries", permitted, _excerpt(text, remote), source)
    sponsorship = [
        ("unavailable", r"(?:no|not offer(?:ing)?|cannot (?:provide|offer)|do not (?:provide|offer))\s+(?:visa )?sponsorship|sponsorship (?:is )?(?:not (?:available|provided)|unavailable)"),
        ("required_authorisation", r"(?:must|require[sd]?).{0,55}(?:existing|current|valid|already).{0,35}(?:work authori[sz]ation|right to work|work permit)|(?:existing|current) (?:work authori[sz]ation|right to work|work permit) (?:is )?required|must (?:already )?(?:have|hold).{0,35}(?:right to work|work permit|work authori[sz]ation)"),
        ("advertised", r"(?:visa )?sponsorship (?:is )?(?:available|provided|offered)|(?:provide|offer)(?:s)? (?:visa )?sponsorship"),
    ]
    for value, pattern in sponsorship:
        match = re.search(pattern, text, re.I)
        if match:
            _record(job, "sponsorship", value, _excerpt(text, match), source)
            break
    timezone_match = re.search(r"(?:time\s?zone|working hours)\s*:\s*([^\n;]{2,100})", text, re.I)
    if timezone_match:
        _record(job, "timezone_requirement", timezone_match[1], timezone_match[0], source)
    posting = re.search(r"(?:posted(?: on)?|date posted|publication date)\s*:?\s*(\d{4}-\d{2}-\d{2})(?!\d)", text, re.I)
    if posting:
        try:
            datetime.fromisoformat(posting[1])
            _record(job, "posted_at", posting[1], posting[0], source)
        except ValueError:
            pass
    job.setdefault("requirements", [])
    for requirement in _requirements(text, source):
        if requirement not in job["requirements"]:
            job["requirements"].append(requirement)
    detected_skills = [name for name, pattern in SKILLS.items() if re.search(pattern, text, re.I)]
    job["extracted_skills"] = detected_skills
    for skill in detected_skills:
        match = re.search(SKILLS[skill], text, re.I)
        item = {"field": "skill", "quote": _excerpt(text, match), "source": source, "status": "extracted", "value": skill}
        if item not in job["evidence"]:
            job["evidence"].append(item)
    for field in ("country", "city", "posted_at", "first_seen", "last_seen", "last_verified", "salary_min", "salary_max",
                  "salary_currency", "office_days", "timezone_requirement", "requisition_id"):
        job.setdefault(field, None)
    for field, value in {"title": "Imported opportunity", "company": "Unknown employer", "location": "Unknown location",
                         "url": "", "salary_period": "unknown", "salary_type": "unknown", "sponsorship": "unknown",
                         "status": "new", "sample": False, "remote_countries": [], "conditions": [], "sources": [], "notes": ""}.items():
        job.setdefault(field, value)
    if not isinstance(job["remote_countries"], list):
        raise ValueError("Remote countries must be a list of ISO country codes")
    job["remote_countries"] = list(dict.fromkeys(code for value in job["remote_countries"] for code in (country_codes(str(value)) or [str(value).upper()])))
    for field in ("salary_min", "salary_max"):
        if job.get(field) is not None and (isinstance(job[field], bool) or not isinstance(job[field], (int, float)) or job[field] < 0):
            raise ValueError("Salary amounts must be non-negative numbers or null")
    if job["salary_min"] is not None and job["salary_max"] is not None and job["salary_min"] > job["salary_max"]:
        raise ValueError("Salary minimum cannot exceed its maximum")
    job["content_fingerprint"] = sha256(text.encode("utf-8")).hexdigest()
    job["extraction_version"] = POLICY_VERSION
    return job


def _merged(base: dict, override: dict) -> dict:
    output = deepcopy(base)
    for key, value in override.items():
        output[key] = _merged(output[key], value) if isinstance(value, dict) and isinstance(output.get(key), dict) else deepcopy(value)
    return output


def _score(components: dict, weights: dict) -> float:
    usable = {key: max(0, float(weights.get(key, 0))) for key in components}
    total = sum(usable.values())
    return round(sum(components[key] * weight for key, weight in usable.items()) / total, 1) if total else 50.0


def _known_boolean(value: Any) -> bool | None:
    if value is True or value is False:
        return value
    if isinstance(value, dict):
        if value.get("status") in {"PASS", "FAIL", "UNKNOWN"}:
            return True if value["status"] == "PASS" else False if value["status"] == "FAIL" else None
        return _known_boolean(value.get("authorised", value.get("verified", value.get("value"))))
    if isinstance(value, str):
        normal = value.strip().lower()
        if normal in {"yes", "true", "authorised", "authorized", "fluent", "native", "proficient"}:
            return True
        if normal in {"no", "false", "not authorised", "not authorized", "none"}:
            return False
    return None


def _annual_base(job: dict, settings: dict, currency: str = "GBP") -> tuple[float | None, float | None]:
    if job.get("salary_type") != "base" or job.get("salary_period") not in {"annual", "year", "yearly", "month", "monthly"}:
        return None, None
    low, high = job.get("salary_min"), job.get("salary_max")
    if job.get("salary_period") in {"month", "monthly"}:
        low, high = low * 12 if low is not None else None, high * 12 if high is not None else None
    original = str(job.get("salary_currency") or "").upper()
    if original == currency:
        return low, high
    # Optional conversion requires a dated source. No default exchange rates.
    fx = settings.get("fx", {}).get(f"{original}_{currency}", {})
    if fx.get("source") and fx.get("date") and isinstance(fx.get("rate"), (int, float)) and fx["rate"] > 0:
        try:
            datetime.fromisoformat(fx["date"])
        except (TypeError, ValueError):
            return None, None
        return low * fx["rate"] if low is not None else None, high * fx["rate"] if high is not None else None
    return None, None


def _london(job: dict, options: list[dict] | None = None) -> bool:
    options = location_options(job) if options is None else options
    if job.get("office_days") == 0 and job.get("remote_countries"):
        # Explicit work-country permission outranks a headquarters/office field.
        return any(item["kind"] == "remote" and item["country"] in {"GB", "WORLDWIDE"} for item in options)
    return any(item["country"] == "GB" and
               (_city(item["location"], "GB") in {"London", "Canary Wharf"} or job.get("office_days") == 0)
               for item in options)


def _geography(job: dict, settings: dict, options: list[dict] | None = None) -> tuple[list[str], bool]:
    options = location_options(job) if options is None else options
    targets = []
    for country, preference in settings["locations"].items():
        if not preference.get("enabled", False):
            continue
        suitable = [item for item in options if item["country"] in {country, "WORLDWIDE"}
                    and not any(_has(item["location"], city) or _has(str(item.get("city") or ""), city) for city in preference.get("excluded_cities", []))]
        if suitable:
            targets.append(country)
    return targets, _london(job, options)


def reextract_job(job: dict) -> dict:
    """Refresh generated fields while preserving source/user facts and history."""
    if job.get("extraction_version") == POLICY_VERSION:
        return deepcopy(job)
    refreshed = deepcopy(job)
    explicit_salary = any(isinstance(item, dict) and (item.get("field") == "salary" or str(item.get("field", "")).startswith("salary_"))
                          and str(item.get("status", "")).upper() in {"SUPPLIED", "PASS", "VERIFIED", "SOURCE_VERIFIED"}
                          and item.get("source") and (item.get("quote") or item.get("evidence"))
                          for item in refreshed.get("evidence", []))
    generated_salary = {item.get("field") for item in refreshed.get("evidence", [])
                        if isinstance(item, dict) and str(item.get("field", "")).startswith("salary_")
                        and item.get("status") == "extracted" and not explicit_salary}
    generated_remote = {item.get("field") for item in refreshed.get("evidence", [])
                        if isinstance(item, dict) and item.get("field") in {"office_days", "remote_countries"}
                        and item.get("status") == "extracted" and _temporary_remote_benefit(str(item.get("quote") or ""))}
    generated_salary.update(generated_remote)
    for field in generated_salary:
        refreshed.pop(field, None)
    refreshed["evidence"] = [item for item in refreshed.get("evidence", [])
                             if not (isinstance(item, dict) and item.get("field") in generated_salary and item.get("status") == "extracted")]
    if job.get("extraction_version"):
        refreshed.pop("requirements", None)
        refreshed.pop("other_compensation", None)
    return extract_job(str(refreshed.get("description") or ""), refreshed)


def role_text(job: dict) -> str:
    """Prefer actual duties/requirements over employer and benefits boilerplate."""
    title = str(job.get("title") or "")
    description = str(job.get("description") or "")
    start = re.search(r"(?:^|\n)(?:key )?(?:responsibilities|what you(?:.ll| will) do|what (?:we.re|we are) looking for|about the role|role overview|requirements|(?:basic |minimum |required )?qualifications)\s*:?\s*(?:\n|$)", description, re.I)
    if start:
        description = description[start.end():]
        end = re.search(r"(?:^|\n)(?:about (?:us|the company|[A-Z][\w-]+)|(?:our |company )?benefits|notice to candidates|equal opportunity)\s*:?\s*(?:\n|$)", description, re.I)
        if end and end.start() > 0:
            description = description[:end.start()]
    return title + "\n" + description


def evaluate_job(job: dict, profile: dict, settings: dict) -> dict:
    """Return transparent scores and lane-specific decisions without mutating inputs.

    Skills: matched named skills / named advert skills (50 when unspecified).
    Domain: overlap between the advert duties and profile domains 100, other quant 85,
    technical work with evidenced projects 75, unknown 50, unrelated work 20.
    Responsibility: entry/junior 85, ordinary 75, senior 45, leadership 10;
    a title alone never becomes an eligibility blocker.
    Value: configured location weight, relevant career 100/85/20, unknown pay 50,
    London verified floor 80 and exceptional base 100; work pattern 100/85/70/55
    for 0/1/2/3+ office days (50 when unknown).
    Freshness: actual posting <=72h 100, <=7d 85, <=30d 65, older 40, unknown 50.
    Simplicity is neutral unless explicit supplied application steps are known.
    """
    config = _merged(default_settings(), settings)
    text = "\n".join(str(job.get(key) or "") for key in ("title", "description"))
    duties = role_text(job)
    source = str(job.get("url") or "manual paste")
    conditions: list[dict] = []
    gaps: list[str] = []
    blockers: list[str] = []
    filtered: list[str] = []

    def condition(name: str, status: str, evidence: str, *, blocking: bool = True, source_url: str = source) -> None:
        record = {"name": name, "status": status, "evidence": evidence, "source": source_url, "blocking": blocking}
        if not any(item["name"] == name and item["status"] == status and item["evidence"] == evidence for item in conditions):
            conditions.append(record)
        if status == "FAIL" and blocking and evidence:
            blockers.append(f"{name}: {evidence}")
        elif status == "UNKNOWN":
            gaps.append(f"{name}: {evidence}")

    for item in job.get("conditions", []):
        if not isinstance(item, dict) or item.get("status") not in {"PASS", "FAIL", "UNKNOWN"}:
            continue
        name = str(item.get("name", "Unspecified condition"))
        if name.casefold() == "closed vacancy" and not vacancy_is_closed(job):
            continue
        evidence = str(item.get("evidence") or item.get("quote") or "")
        status = item["status"] if evidence else "UNKNOWN"
        condition(name, status, evidence or "Source evidence required", blocking=item.get("blocking", name.lower() in BLOCKER_NAMES), source_url=item.get("source") or source)
    closed = CLOSED_PATTERN.search(text)
    if vacancy_is_closed(job):
        condition("Closed vacancy", "FAIL", _excerpt(text, closed) if closed else "Vacancy explicitly marked closed")
    elif job.get("last_verified"):
        condition("Vacancy status", "PASS", f"Application page last retrieved {job['last_verified']}")
    else:
        condition("Vacancy status", "UNKNOWN", "Application page has not been verified")
    for name, pattern in (
        ("Unpaid employment", r"\bunpaid (?:role|position|internship|work|employment)\b"),
        ("Pay to apply", r"(?:pay|payment|fee).{0,30}(?:to.apply|application fee)|application fee.{0,20}(?:required|pay)"),
        ("Commission only", r"\bcommission.only\b|\bno base salary\b"),
        ("Secondary school teaching", r"\bsecondary\b.{0,65}\b(?:teach|teacher|teaching)\b|\b(?:teacher|teaching)\b.{0,65}\b(?:secondary|KS3|KS4|KS5|GCSE)\b|\b(?:KS3|KS4|KS5|GCSE)\b.{0,40}\bteach\w*"),
    ):
        match = re.search(pattern, text, re.I)
        if match:
            quote = _excerpt(text, match)
            before = text[max(0, match.start() - 45):match.start()]
            negated = name != "Secondary school teaching" and bool(re.search(r"\b(?:not|never|don't|do not|no need to)\s+(?:an? |be |have to )*$", before, re.I))
            if not negated:
                condition(name, "FAIL", quote)
    qualification_text = " ".join(str(value.get("name") or value.get("qualification") or "") if isinstance(value, dict) else str(value) for value in profile.get("qualifications", []) if not isinstance(value, dict) or value.get("completed", True))
    missing_qualifications = {str(value).lower() for value in profile.get("qualifications_not_held", [])}
    # Re-extract generated requirements after a parser version change. This also
    # repairs old overlong excerpts when the store performs a read/rescore, while
    # preserving unversioned manually supplied structured requirements.
    refresh_extraction = job.get("extraction_version") and job.get("extraction_version") != POLICY_VERSION
    requirements = _requirements(text, source) if refresh_extraction else deepcopy(job.get("requirements") or _requirements(text, source))
    unverified_senior_experience = False
    unverified_professional_experience = False
    experience_shortfall = False
    student_unverified = False
    for requirement in requirements:
        if not requirement.get("mandatory"):
            continue
        kind, value = requirement.get("kind"), str(requirement.get("value") or "")
        quote = str(requirement.get("evidence") or "Requirement needs source confirmation")
        status = "UNKNOWN"
        if kind == "qualification":
            accepted = requirement.get("accepted_qualifications") or [value]
            held = any(_has(qualification_text, award) or (award == "degree" and bool(re.search(r"\bBSc\b|bachelor", qualification_text, re.I))) for award in accepted)
            if held:
                status = "PASS"
            elif requirement.get("equivalent_accepted"):
                quote += "; accepted equivalence needs assessment"
            elif all(award.lower() in missing_qualifications for award in accepted):
                status = "FAIL"
            condition("Mandatory qualification", status, quote)
        elif kind == "driving":
            status = "PASS" if profile.get("drives") is True else "FAIL" if profile.get("drives") is False else "UNKNOWN"
            condition("Mandatory driving", status, quote)
        elif kind == "student_enrolment":
            enrolled = _known_boolean(profile.get("currently_enrolled"))
            status = "PASS" if enrolled is True else "FAIL" if enrolled is False else "UNKNOWN"
            student_unverified = status != "PASS"
            condition("Mandatory student enrolment", status, quote)
        elif kind == "language":
            language_values = profile.get("languages", {})
            entry = next((val for key, val in language_values.items() if key.casefold() == value.casefold()), None) if isinstance(language_values, dict) else None
            known = _known_boolean(entry)
            if requirement.get("proficiency") == "native" and known is not False:
                level = entry.get("level", entry.get("proficiency", "")) if isinstance(entry, dict) else entry if isinstance(entry, str) else ""
                known = True if str(level).casefold() in {"native", "native-level", "native level", "mother tongue"} else None
                if known is None:
                    quote += "; native-level language ability is not verified in the candidate profile"
            condition("Mandatory language", "PASS" if known is True else "FAIL" if known is False else "UNKNOWN", quote)
        elif kind == "clearance":
            known = _known_boolean(profile.get("security_clearance"))
            condition("Mandatory clearance", "PASS" if known is True else "FAIL" if known is False else "UNKNOWN", quote)
        elif kind == "citizenship":
            citizenships = profile.get("citizenships", [])
            status = "PASS" if value in citizenships else "FAIL" if profile.get("citizenships_confirmed") is True else "UNKNOWN"
            condition("Mandatory citizenship", status, quote)
        elif kind == "experience":
            alternatives = requirement.get("alternative_qualifications") or []
            has_award = any(_has(qualification_text, award) or (award == "degree" and bool(re.search(r"\bBSc\b|bachelor", qualification_text, re.I))) for award in alternatives)
            if has_award:
                # A held degree satisfies the award level. A different subject
                # remains an explicit equivalence question, never invented
                # chemistry/biology experience or an independent years deficit.
                degree_clause = re.split(r"\bor\s+(?=(?:at least\s+)?\d+\+?\s*years?)", quote, flags=re.I)[0]
                subjects = [subject for subject in ("physics", "chemistry", "biology", "materials science", "chemical engineering", "computer science", "mathematics", "statistics", "engineering") if _has(degree_clause, subject)]
                subject_supported = not subjects or any(_has(qualification_text, subject) for subject in subjects)
                condition("Qualification or experience", "PASS" if subject_supported else "UNKNOWN",
                          quote + ("; candidate holds an accepted degree" if subject_supported else "; candidate holds the degree level, but acceptance of their degree subject as a related field needs confirmation"))
                continue
            minimum_years = requirement.get("minimum_years", 0)
            areas = requirement.get("areas") or [value]
            experience = profile.get("experience_years", {})
            known = []
            for area in areas:
                entry = next((record for key, record in experience.items() if key.casefold() == area.casefold()), None)
                # Explicit profile figures only: projects or employment dates are
                # never silently converted into years of specialist industry work.
                if isinstance(entry, dict) and entry.get("status", "").upper() not in {"VERIFY", "UNKNOWN", "UNRESOLVED"} and entry.get("source"):
                    entry = entry.get("years")
                elif isinstance(entry, dict):
                    entry = None
                known.append(entry if isinstance(entry, (int, float)) and not isinstance(entry, bool) else None)
            if any(years is not None and years >= minimum_years for years in known):
                status = "PASS"
            elif known and all(years is not None and years < minimum_years for years in known) and not requirement.get("equivalent_accepted"):
                status = "FAIL"
                experience_shortfall = True
            else:
                status = "UNKNOWN"
                if minimum_years >= 5:
                    unverified_senior_experience = True
                if minimum_years >= 2:
                    unverified_professional_experience = True
                quote += f"; {minimum_years:g} years of the specified experience is not verified in the candidate profile"
            condition("Mandatory experience", status, quote)
    targets, is_london = _geography(job, config)
    country = job.get("country") or _country(str(job.get("location") or ""))
    options = location_options(job)
    if job.get("office_days") == 0 and job.get("remote_countries"):
        options = [item for item in options if item["kind"] == "remote"]
    alternatives = {item["country"] for item in options}
    if "WORLDWIDE" in alternatives:
        alternatives = set(profile.get("work_authorisation", {})) | set(targets) | ({"GB"} if is_london else set())
    relevant_alternatives = alternatives.intersection(set(targets) | ({"GB"} if is_london else set()))
    if relevant_alternatives:
        alternatives = relevant_alternatives
    required_locations = bool(job.get("all_locations_required"))
    if required_locations:
        alternatives.update(country_codes(str(job.get("location") or "")))
    # A permitted London alternative must not be discarded in favour of a
    # configured overseas location where the candidate lacks authorisation.
    auth_countries = sorted(alternatives) or ([country] if country else [])
    auth_values = [_known_boolean(profile.get("work_authorisation", {}).get(code)) for code in auth_countries]
    if required_locations:
        authorisation = False if False in auth_values else True if auth_values and all(value is True for value in auth_values) else None
    else:
        authorisation = True if True in auth_values else False if auth_values and all(value is False for value in auth_values) else None
    auth_place = ", ".join(str(value) for value in auth_countries if value)
    sponsorship = job.get("sponsorship", "unknown")
    if auth_place and auth_countries != ["GB"]:
        if authorisation is True:
            permitted = ", ".join(code for code, value in zip(auth_countries, auth_values) if value is True)
            condition("Work authorisation", "PASS", f"Candidate explicitly records authorisation for {'all required locations' if required_locations else 'a permitted location'}: {permitted}", source_url="candidate profile")
        elif authorisation is False and sponsorship in {"required_authorisation", "unavailable"}:
            quote = next((item.get("quote", "") for item in job.get("evidence", []) if item.get("field") == "sponsorship"), sponsorship)
            condition("Work authorisation", "FAIL", f"{quote}; candidate explicitly lacks authorisation for {auth_place}")
        else:
            condition("Work authorisation", "UNKNOWN", "Sponsorship advertised; eligibility needs checking" if sponsorship == "advertised" else "Country authorisation and sponsorship need checking")
    elif auth_countries == ["GB"] and authorisation is True:
        condition("Work authorisation", "PASS", "Candidate explicitly records UK work authorisation", source_url="candidate profile")
    elif auth_countries == ["GB"] and authorisation is False and sponsorship in {"required_authorisation", "unavailable"}:
        condition("Work authorisation", "FAIL", "Existing UK authorisation required or sponsorship unavailable; candidate records no authorisation")
    elif auth_countries == ["GB"] and authorisation is not True:
        condition("Work authorisation", "UNKNOWN", "UK work authorisation is not confirmed in the profile")
    if re.search(r"\bremote\b", text + " " + str(job.get("location") or ""), re.I) and not job.get("remote_countries"):
        condition("Remote country permission", "UNKNOWN", "Remote wording does not establish permission to work from a target country")
    if not country:
        condition("Location", "UNKNOWN", "Country is not established by the source")
    if job.get("office_days") is None:
        condition("Office frequency", "UNKNOWN", "Office attendance days are not disclosed", blocking=False)
    if not job.get("posted_at"):
        gaps.append("Posting date not disclosed; first seen is not the posting date")
    skill_names = [str(value.get("name") or "") if isinstance(value, dict) else str(value) for value in profile.get("skills", [])]
    candidate_skills = {value.casefold() for value in skill_names}
    wanted = [name for name, pattern in SKILLS.items() if re.search(pattern, duties, re.I)]
    wanted = list(wanted)
    if re.search(r"customer success|client solutions|solutions consultant|technical support|implementation|product specialist", str(job.get("title") or ""), re.I):
        # These roles explicitly combine systems and people. A preferred CRM
        # must not be the sole skill denominator while demonstrated communication
        # and analytical work disappear. Match both advert and candidate evidence.
        professional = " ".join(str(r.get("text") or "") for r in profile.get("evidence", [])
                                if isinstance(r, dict) and str(r.get("status", "")).upper() in {"DIRECT", "VERIFIED", "USER_PROVIDED", "TRANSFERABLE", "CURRENT_PROJECT"}
                                and not r.get("sensitive") and r.get("include_in_model") is not False)
        for name, pattern in {
            "clear communication": r"communicat\w*|explain\w*|present\w*",
            "customer-facing support": r"customer.facing|client.facing|customer support|customer service|support.ticket|customer.{0,20}relationship",
            "data interpretation": r"analys\w*|analyz\w*|data.{0,30}(?:patterns|insights)|patterns.{0,30}data",
            "systems troubleshooting": r"troubleshoot\w*|problem.solv\w*|investigat\w*.{0,30}issues",
        }.items():
            if re.search(pattern, text, re.I):
                if name not in wanted:
                    wanted.append(name)
                if re.search(pattern, professional, re.I):
                    candidate_skills.add(name.casefold())
    matched = [name for name in wanted if name.casefold() in candidate_skills]
    unmatched = [name for name in wanted if name.casefold() not in candidate_skills]
    skills_score = 100 * len(matched) / len(wanted) if wanted else 50
    if unmatched:
        gaps.append("Skills requiring evidence or refresh: " + ", ".join(unmatched))
    title = str(job.get("title") or "")
    quant = bool(QUANT_PATTERN.search(duties)) and not bool(re.search(r"(?:finance|financial|trading) (?:admin|administrator|assistant)|accounts (?:payable|receivable)", title, re.I))
    technical = bool(TECH_PATTERN.search(duties))
    ordinary_cashflow = bool(re.search(r"\b(?:office administrator|finance administrator|financial administrator|accounts payable|accounts receivable|administrative assistant|receptionist|warehouse|picker.packer|bartender|waiter|bar staff|retail(?: grocery)? assistant|grocery assistant|stockroom|data entry)\b", title, re.I))
    if ordinary_cashflow:
        technical = False
        quant = False
    # Judge an accounting/collections function on its actual discipline. Its
    # technology employer's API marketing copy is not evidence of technical work.
    finance_operations = bool(re.search(r"\b(?:collections?|accounts (?:receivable|payable)|credit control|billing|bookkeep\w*|payroll|accountant|accounting|AR specialist|AP specialist)\b", str(job.get("title") or ""), re.I))
    if finance_operations and not re.search(r"\b(?:data|systems|technical|software|analytics)\b", str(job.get("title") or ""), re.I):
        technical = False
        quant = False
    nontechnical_function = bool(re.search(r"\b(?:total rewards|human resources|HR|recruiter|talent acquisition|restaurant development|marketing specialist|marketing manager|retail|grocery|hospitality)\b", title, re.I))
    if nontechnical_function:
        technical = False
        quant = False
    if not technical and not quant and set(matched).issubset({"Excel", "SQL", "Git", "GitHub", "Python"}):
        skills_score = min(skills_score, 35)
    domains = [str(value).casefold() for value in profile.get("domains", [])]
    domain_overlap = [value for value in domains if _has(duties, value)]
    if nontechnical_function or ordinary_cashflow or re.search(r"\b(?:game design|unity|game client)\b", title, re.I):
        domain_overlap = []
    domain = 100 if domain_overlap else 85 if quant else 75 if technical else 20
    if not text.strip():
        domain = 50
    title = str(job.get("title") or "")
    leadership = bool(re.search(r"\b(?:chief|ceo|cto|cfo|vice president|head of|director|(?:engineering|software|development|research|people|team|analytics) manager)\b", title, re.I)
                      or re.search(r"\b(?:lead|manag|mentor|supervis)\w*\s+(?:and \w+\s+)?(?:a |the |our )?(?:team(?: of)?|engineers|designers|direct reports)\b", duties, re.I))
    leadership_record = profile.get("leadership_evidence")
    has_leadership = bool(leadership_record) and str(leadership_record).casefold().strip() not in {"none", "false", "unknown", "unverified"}
    senior = bool(re.search(r"\b(?:senior|lead|principal|staff)\b", title, re.I))
    junior = bool(re.search(r"\b(?:junior|graduate|entry.level|associate)\b", title, re.I))
    responsibility = 10 if leadership else 45 if senior else 85 if junior else 75 if technical else 50
    if unverified_senior_experience or experience_shortfall:
        responsibility = min(responsibility, 20)
    elif unverified_professional_experience:
        responsibility = min(responsibility, 45)
    unsupported_specialist_leadership = unverified_senior_experience and bool(re.search(r"\b(?:lead|head|director|principal|staff|chief)\b", title, re.I)) and not has_leadership
    if senior:
        gaps.append("Seniority: demonstrate the advertised responsibilities with project and research evidence")
    if leadership and not has_leadership:
        filtered.append("Executive or specialist leadership responsibilities lack supporting candidate evidence")
        condition("Leadership experience", "UNKNOWN", "Direct evidence of the advertised people/team leadership is not established", blocking=False)
    if re.search(r"\bstudent\b", title, re.I) and _known_boolean(profile.get("currently_enrolled")) is not True:
        student_unverified = True
        condition("Student eligibility", "UNKNOWN", "This is a student role; current enrolment eligibility needs confirmation", blocking=False)
    fit_components = {"skills": round(skills_score, 1), "domain": domain, "responsibility": responsibility}
    fit = _score(fit_components, config["weights"]["fit"])
    if (leadership and not has_leadership) or student_unverified:
        fit = min(fit, 64.9)
    low, high = _annual_base(job, config)
    salary_unknown = low is None and high is None
    original_salary_known = job.get("salary_min") is not None or job.get("salary_max") is not None
    if salary_unknown and is_london:
        gaps.append("Annual base salary is not disclosed or cannot be compared without a sourced currency conversion")
    elif not original_salary_known:
        gaps.append("Salary is not disclosed")
    exceptional_low = config["exceptional"]["base_gbp"]
    exceptional_pay = low is not None and low >= exceptional_low
    market = config["exceptional"].get("markets", {}).get(country, {})
    if market:
        market_low, _ = _annual_base(job, config, market.get("currency", job.get("salary_currency")))
        exceptional_pay = market_low is not None and market_low >= market.get("base", float("inf"))
    days = job.get("office_days")
    minimum = config["london"]["salary_remote_one_day"] if days is not None and days <= 1 else config["london"]["salary_two_days"] if days == 2 else config["london"]["salary_three_plus_days"]
    salary_state = "UNKNOWN"
    if not salary_unknown:
        certain_floor = min(config["london"][key] for key in ("salary_remote_one_day", "salary_two_days", "salary_three_plus_days")) if days is None else minimum
        if high is not None and high < certain_floor:
            salary_state = "FAIL"
        elif low is not None and low >= minimum:
            salary_state = "PASS"
        else:
            gaps.append("Pay needs confirmation: salary range or unknown office frequency overlaps the London threshold")
    london_note = f"Annual base threshold £{minimum:,.0f}" + (" for the stated office pattern" if days is not None else "; attendance unknown, all patterns checked")
    if is_london:
        condition("London salary preference", salary_state, london_note, blocking=False)
        if salary_state == "FAIL":
            filtered.append("London salary range is entirely below the applicable minimum")
        if days and days > 0:
            gaps.append("Check the journey from your location for the stated office attendance")
    location_value = max((config["locations"][code].get("priority", 80) for code in targets), default=75 if is_london else 50)
    compensation = 100 if exceptional_pay else 80 if is_london and salary_state == "PASS" else 50
    # An overseas preference is market-specific and never borrowed from London.
    overseas_salary_pass = True
    for target in targets:
        pref = config["locations"][target]
        if pref.get("salary_min") is not None:
            market_low, market_high = _annual_base(job, config, pref.get("currency", "EUR"))
            if market_high is not None and market_high < pref["salary_min"]:
                overseas_salary_pass = False
                filtered.append(f"{target} advertised base salary is below the configured market preference")
            elif market_low is None or market_low < pref["salary_min"]:
                gaps.append(f"{target} salary preference needs confirmation")
    value_components = {"location": location_value, "career": 100 if quant else 85 if technical else 20,
                        "compensation": compensation, "work_pattern": 50 if days is None else 100 if days == 0 else 85 if days == 1 else 70 if days == 2 else 55}
    value = _score(value_components, config["weights"]["value"])
    freshness = 50
    if job.get("posted_at"):
        try:
            posted = datetime.fromisoformat(str(job["posted_at"]).replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - (posted.replace(tzinfo=timezone.utc) if posted.tzinfo is None else posted)).total_seconds() / 86400
            if age >= 0:
                freshness = 100 if age <= 3 else 85 if age <= 7 else 65 if age <= 30 else 40
            else:
                gaps.append("Advert posting date is in the future; freshness remains neutral")
        except (ValueError, TypeError):
            gaps.append("Posting date could not be parsed; freshness remains neutral")
    steps = job.get("application_steps")
    simplicity = 90 if isinstance(steps, int) and 0 < steps <= 2 else 70 if isinstance(steps, int) and steps <= 4 else 30 if isinstance(steps, int) and steps > 4 else 50
    priority_components = {"fit": fit, "value": value, "freshness": freshness, "simplicity": simplicity}
    priority = _score(priority_components, config["weights"]["priority"])
    blocked = bool(blockers)
    eligibility = "blocked" if blocked else "needs_checking" if any(item["status"] == "UNKNOWN" and item["blocking"] for item in conditions) else "clear"
    known_fields = [bool(job.get("title") and job["title"] != "Imported opportunity"), bool(job.get("company") and job["company"] != "Unknown employer"),
                    bool(country), bool(job.get("last_verified")), bool(job.get("posted_at")), original_salary_known,
                    days is not None, bool(wanted), sponsorship != "unknown" or authorisation is not None,
                    bool(domain_overlap)]
    confidence = round(100 * sum(known_fields) / len(known_fields), 1)
    applicable: list[str] = []
    if targets:
        applicable.append("mediterranean")
    worldwide = config["lanes"].get("overseas_quant_worldwide") and country and country != "GB"
    if quant and (targets or worldwide):
        applicable.append("overseas_quant")
    if is_london:
        applicable.append("london")
    upside_evidence = job.get("exceptional_upside_evidence")
    evidence_upside = isinstance(upside_evidence, dict) and bool(upside_evidence.get("quote") and upside_evidence.get("source"))
    quant_upside = quant and bool(domain_overlap) and fit >= 65
    if (targets or is_london or worldwide) and (exceptional_pay or evidence_upside or quant_upside):
        applicable.append("exceptional")
    home_city = _city(str(profile.get("location") or ""), "GB")
    reachable = country == "GB" and (is_london or bool(home_city and _has(str(job.get("location") or ""), home_city)))
    if reachable:
        applicable.append("cashflow")
    lane_evaluations = {}
    admitted_lanes = []
    for lane in applicable:
        reasons = []
        threshold = config["thresholds"][lane]
        if not config["lanes"].get(lane):
            reasons.append("Lane is disabled")
        if fit < threshold["fit"]:
            reasons.append(f"Fit {fit:g} is below {threshold['fit']:g}")
        if priority < threshold["priority"]:
            reasons.append(f"Priority {priority:g} is below {threshold['priority']:g}")
        if blocked:
            reasons.append("Confirmed eligibility blocker")
        if leadership and not has_leadership:
            reasons.append("Leadership responsibilities lack supporting evidence")
        if unsupported_specialist_leadership:
            reasons.append("Specialist leadership requires substantial direct experience that is not established by candidate evidence")
        if ordinary_cashflow and lane not in {"cashflow", "exceptional"}:
            reasons.append("Ordinary cashflow work belongs in the separate optional mode")
        if lane == "london":
            if salary_state == "FAIL":
                reasons.append("Salary range is entirely below the office-pattern threshold")
            if salary_unknown and (fit < config["london"]["unknown_salary_min_fit"] or priority < config["london"]["unknown_salary_min_priority"]):
                reasons.append("Salary unknown: requires a particularly strong fit and priority")
            if not technical:
                reasons.append("Ordinary London lane requires relevant technical, research or valuable client-facing work")
        if lane in {"mediterranean", "overseas_quant"} and not overseas_salary_pass:
            reasons.append("Below the configured overseas market salary preference")
        passed = not reasons
        lane_evaluations[lane] = {"admitted": passed, "reasons": reasons, "thresholds": deepcopy(threshold)}
        if passed:
            admitted_lanes.append(lane)
        elif config["lanes"].get(lane):
            filtered.extend(f"{lane}: {reason}" for reason in reasons)
    if not applicable:
        filtered.append("No enabled-location lane applies; remote country permission cannot be assumed")
    admitted = bool(admitted_lanes)
    stretch = (leadership and not has_leadership) or student_unverified or (admitted and (unverified_professional_experience or fit < 65 or all(lane in {"overseas_quant", "exceptional"} for lane in admitted_lanes)))
    match_label = "strong_match" if fit >= 75 and not unverified_professional_experience and not experience_shortfall and not student_unverified and not (leadership and not has_leadership) else "credible_stretch" if fit >= 35 else "weak_match"
    evidence_phrase = ", ".join(matched[:4]) if matched else ", ".join(domain_overlap[:2]) or "transferable analytical evidence"
    lane_phrase = ", ".join(admitted_lanes) if admitted_lanes else "no admitting lane"
    why = f"{evidence_phrase} supports this {'quantitative/research' if quant else 'technical' if technical else 'transferable'} opportunity; {lane_phrase} (Fit {fit:g}, Priority {priority:g})."
    next_action = "Review the confirmed blocker before spending time on an application" if blocked else "Check eligibility and the source advert, then decide whether to prepare materials" if eligibility == "needs_checking" else "Review the evidence and prepare a tailored application" if admitted else "Review the filtering reasons or save manually if this opportunity is worthwhile"
    result = {"policy_version": POLICY_VERSION, "fit": fit, "value": value, "priority": priority,
            "confidence": confidence, "eligibility": eligibility, "match": match_label,
            "lanes": admitted_lanes, "applicable_lanes": applicable, "lane_evaluations": lane_evaluations,
            "admitted": admitted, "stretch": stretch, "why": why, "next_action": next_action,
            "gaps": list(dict.fromkeys(gaps)), "blockers": list(dict.fromkeys(blockers)), "conditions": conditions,
            "components": {"fit": fit_components, "value": value_components, "priority": priority_components,
                           "matched_skills": matched, "unmatched_skills": unmatched, "domain_evidence": domain_overlap,
                           "salary_state": salary_state if is_london else "not_applicable", "salary_unknown": salary_unknown,
                           "london": is_london, "target_countries": targets, "exceptional_pay": exceptional_pay},
            "filtered_reasons": list(dict.fromkeys(filtered))}
    if config.get("strategy", {}).get("mode") == "professional_london_first":
        from .professional import assess_candidacy
        candidacy = assess_candidacy(job, profile, config, result)
        result["candidacy"] = candidacy
        result["conditions"].extend(candidacy["conditions"])
        result["blockers"] = candidacy["blockers"]
        if result["blockers"]:
            result["eligibility"] = "blocked"
            result["admitted"] = False
    return result


def select_shortlist(jobs: list[dict], settings: dict) -> list[dict]:
    """Select a deterministic bounded queue; persistent stability is store-owned.

    Incumbent unfinished shortlist members retain precedence when marked with
    ``shortlisted``/``in_shortlist``. Both recommended and stretch groups count
    towards the shared London/company limits, including exceptional London jobs.
    """
    config = _merged(default_settings(), settings)
    if config.get("strategy", {}).get("mode") == "professional_london_first" and any(job.get("evaluation", {}).get("candidacy") for job in jobs):
        from .professional import professional_shortlist
        return professional_shortlist(jobs, config["strategy"])
    caps = config["queue"]
    candidates = [job for job in jobs if job.get("evaluation", {}).get("admitted")
                  and job.get("evaluation", {}).get("eligibility") != "blocked"
                  and job.get("status", "new") not in TERMINAL_STATES]
    def order(job: dict) -> tuple:
        evaluation = job["evaluation"]
        return (not bool(job.get("shortlisted") or job.get("in_shortlist")),
                -int(bool(evaluation.get("components", {}).get("target_countries") or "mediterranean" in evaluation.get("lanes", []))),
                -float(evaluation.get("priority", 0)), str(job.get("first_seen") or ""),
                str(job.get("id") or job.get("url") or job.get("title") or ""))
    counts: Counter = Counter()
    companies: Counter = Counter()
    identities: set[str] = set()
    chosen = []
    for job in sorted(candidates, key=order):
        evaluation = job["evaluation"]
        group = "stretch" if evaluation.get("stretch") else "recommended"
        company = re.sub(r"[^a-z0-9]", "", str(job.get("company") or "unknown").casefold())
        is_london = bool(evaluation.get("components", {}).get("london", _london(job)))
        unknown_salary = bool(evaluation.get("components", {}).get("salary_unknown", job.get("salary_min") is None and job.get("salary_max") is None))
        identity = f"req:{company}:{job['requisition_id']}" if job.get("requisition_id") else f"url:{str(job['url']).split('#')[0]}" if job.get("url") else f"id:{job['id']}" if job.get("id") is not None else f"content:{company}:{job.get('title')}:{job.get('location')}"
        if identity in identities or counts[group] >= caps[group] or companies[company] >= caps["per_company"]:
            continue
        if is_london and (counts["london"] >= caps["london"] or unknown_salary and counts["unknown_salary_london"] >= caps["unknown_salary_london"]):
            continue
        chosen.append(job)
        identities.add(identity)
        counts[group] += 1
        companies[company] += 1
        if is_london:
            counts["london"] += 1
            if unknown_salary:
                counts["unknown_salary_london"] += 1
    return chosen
