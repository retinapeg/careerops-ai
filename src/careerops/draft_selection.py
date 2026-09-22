"""Choose useful, diverse CV evidence without inventing or rewriting a claim.

This is a pure ranking helper for an already privacy-filtered evidence set. Call
it once for a short evidence summary and separately on each employment/project's
records for its bullets. Employer attribution and output wording remain intact.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math
import re


_STOP = set("a an and are as at be been by for from has have in into is it of on or our that the their this to using was were with worked work experience used uses use skills role roles".split())
_ALLOWED = {"DIRECT", "CURRENT_PROJECT", "TRANSFERABLE", "USER_PROVIDED", "VERIFIED"}
_JUDGEMENT = re.compile(
    r"\b(?:strong|good|excellent|natural) fit (?:for|with)\b|\brelevance\b|"
    r"\b(?:do not|must not|without) claim\b|\b(?:verify|unresolved|unsupported)\b|"
    r"\b(?:suitable|ideal) (?:for|candidate)\b|\b(?:requires?|needs?) (?:verification|confirmation)\b|"
    r"\bis live and working\b", re.I
)
_ACTION = re.compile(
    r"\b(?:built|develop\w*|implement\w*|integrat\w*|normalis\w*|normaliz\w*|"
    r"design\w*|validat\w*|investigat\w*|communicat\w*|convert\w*|resolv\w*|"
    r"maintain\w*|escalat\w*|protect\w*|prioritis\w*|prioritiz\w*|identif\w*|"
    r"analys\w*|analyz\w*|interpret\w*|verif\w*|test\w*|preserv\w*|ensure\w*|"
    r"deliver\w*|organis\w*|organiz\w*|present\w*|support\w*|applied|handled|kept)\b", re.I
)
_TOOLS_ONLY = re.compile(
    r"^(?:uses?|used|knows?|knowledge of|experience (?:of|with))\s+"
    r"(?:python|pandas|numpy|excel|power bi|sql|git(?:hub)?|jupyter|matlab|c\+\+)"
    r"(?:\s+(?:professionally|regularly))?[.!]?$", re.I
)
_CONCEPTS = {
    "model": {"ai", "llm", "llms", "model", "models", "language", "codex", "chatgpt", "schema", "generated", "validation", "deterministic"},
    "integration": {"api", "apis", "integration", "integrations", "integrated", "integrating", "rest", "json", "oauth", "external", "authenticated", "schema", "normalised", "normalisation"},
    "automation": {"automation", "automated", "workflow", "workflows", "pipeline", "pipelines", "idempotent", "persistent", "deduplication", "engine"},
    "client": {"client", "clients", "customer", "customers", "stakeholder", "stakeholders", "commercial", "investor", "recommendations", "communication", "communicated", "findings", "saas"},
    "solutions": {"solutions", "implementation", "support", "troubleshooting", "investigated", "investigation", "issues", "escalation", "product", "products", "tickets", "ticket"},
    "data": {"python", "sql", "pandas", "numpy", "data", "analysis", "analytical", "normalised", "records", "identifiers", "discrepancies", "reporting", "quality"},
    "research": {"research", "physics", "quantum", "numerical", "mathematical", "scientific", "simulation", "computational", "modelling", "modeling"},
    "reliability": {"accuracy", "accurate", "reliable", "reliability", "regulated", "validation", "validated", "testing", "tests", "procedures", "confidential", "urgent", "safety", "quality", "integrity"},
}


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+(?:\+\+)?", text.lower()) if word not in _STOP and len(word) > 1}


def _identifier(record: dict) -> str:
    return str(record.get("id") or record.get("evidence_id") or "")


def _summary(record: dict) -> bool:
    identifier = _identifier(record).lower()
    return bool(re.search(r"(?:^|:)(?:profile|technical|communication|api_skill)(?::|$)", identifier)
                or re.match(r"^(?:practical |significant |strong |extensive )?experience\b|^background (?:in|includes)\b", str(record.get("text") or ""), re.I))


def _group(record: dict) -> str:
    # Preserve the record's employer, including healthcare employers. A keyword
    # such as 'health' in an employer name is not a sensitive personal disclosure.
    if record.get("source_employer"):
        return str(record["source_employer"]).casefold()
    identifier = _identifier(record).split(":")
    family = identifier[1] if identifier and identifier[0] in {"bank", "seed"} and len(identifier) > 1 else identifier[0]
    return family or str(record.get("section") or record.get("source") or "unattributed").casefold()


def choose_evidence(records: list[dict], job: dict, maximum: int) -> list[dict]:
    """Return unchanged source records, ranked for specificity and role relevance.

    Status and literal evidence outrank career-fit opinions. Direct/current
    accomplishments beat short tool-name fragments. Selection balances relevant
    systems work, client communication and distinct employment/project sources;
    within an already filtered employer's records it selects substantive bullets
    without imposing irrelevant global diversity. At most one summary is used.
    """
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        raise ValueError("maximum must be a non-negative integer")
    if maximum == 0:
        return []
    job_text = " ".join(str(job.get(key) or "") for key in ("title", "description"))
    target_words = _tokens(job_text)
    target_concepts = {name for name, terms in _CONCEPTS.items() if target_words & terms}
    candidates = []
    seen_text = set()
    for position, record in enumerate(records):
        if not isinstance(record, dict) or record.get("automatic_use_allowed") is False:
            continue
        status = str(record.get("status") or record.get("evidence_level") or "").upper()
        text = str(record.get("text") or "").strip()
        if status not in _ALLOWED or not text or _JUDGEMENT.search(text) or _TOOLS_ONLY.fullmatch(text):
            continue
        words = _tokens(text)
        if len(words) < 2:
            continue
        normal = " ".join(re.findall(r"[a-z0-9]+", text.casefold()))
        if normal in seen_text:
            continue
        seen_text.add(normal)
        concepts = {name for name, terms in _CONCEPTS.items() if words & terms}
        shared_concepts = concepts & target_concepts
        specific = min(len(words), 22) / 5
        actions = min(len(_ACTION.findall(text)), 3)
        literal_relevance = min(len(words & target_words), 6) * 1.3
        relevance = len(shared_concepts) * 2.0 + literal_relevance
        factual_weight = 4 if status in {"DIRECT", "CURRENT_PROJECT", "VERIFIED"} else 3 if status == "USER_PROVIDED" else 1
        outcome = 2 if re.search(r"\b(?:so|after|before|while|without|rather than|into|ensured|prevent\w*)\b", text, re.I) else 0
        # A crisp action can be a useful laboratory/employment bullet even when
        # the source bank expresses it in just a few words.
        score = factual_weight + specific + actions * 1.6 + relevance + outcome
        if target_concepts & {"model", "automation", "integration"} and re.search(r"model.generated|deterministic|json.schema|idempotent|source.independent|model.planning backend", text, re.I):
            score += 5
        if re.search(r"ISO timestamps|non.TTY|virtual environment|HTTP GET requests|environment variables", text, re.I):
            score -= 3
        if _summary(record):
            score -= 4
        candidates.append({"record": record, "score": score, "words": words, "concepts": shared_concepts,
                           "group": _group(record), "summary": _summary(record), "position": position})
    if not candidates:
        return []
    group_count = len({candidate["group"] for candidate in candidates})
    # Cap repeated sources only when choosing an overall cross-source summary.
    group_cap = max(2, math.ceil(maximum / 2)) if group_count > 1 and maximum > 2 else maximum
    groups: Counter = Counter()
    concepts_seen: set[str] = set()
    selected = []
    summary_used = False
    while candidates and len(selected) < maximum:
        ranked = []
        for candidate in candidates:
            if candidate["summary"] and summary_used or groups[candidate["group"]] >= group_cap:
                continue
            similarity = max((len(candidate["words"] & item["words"]) / max(1, len(candidate["words"] | item["words"])) for item in selected), default=0)
            if similarity >= .82:
                continue
            novelty = len(candidate["concepts"] - concepts_seen) * 1.5
            adjusted = candidate["score"] + novelty - groups[candidate["group"]] * 3.5 - similarity * 5
            ranked.append((adjusted, -candidate["position"], candidate))
        if not ranked:
            break
        chosen = max(ranked, key=lambda entry: (entry[0], entry[1]))[2]
        selected.append(chosen)
        candidates.remove(chosen)
        groups[chosen["group"]] += 1
        concepts_seen.update(chosen["concepts"])
        summary_used = summary_used or chosen["summary"]
    return [deepcopy(item["record"]) for item in selected]
