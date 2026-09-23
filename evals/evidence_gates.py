"""Evidence gate evaluation over the frozen specification suite.

Runs the real CareerOps gates, offline, against the hand-written cases in
evals/cases/spec_suite_v1.json, a claim suite generated here from the three
fixture profiles, a seeded single-token edit property and the white-box
known-failure list. It writes two generated files:

    evals/results/evidence_gates_v1.json   every per-case outcome and every count
    evals/REPORT.md                        the report, rendered from that JSON

No number in either file is typed by hand. Re-running reproduces both files
byte for byte; tests/test_evidence_gate_eval.py checks that.

Usage (from the repository root):

    python -m evals.evidence_gates           # run and rewrite both files
    python -m evals.evidence_gates --check   # run and compare only

What counts as a reject (the "block type") is defined per gate in DEFINITIONS.
For every case the runner checks that the reject signal and the block the gate
records agree, and raises DetectorError if they do not, so a detector mistake
stops the run instead of silently changing a count.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from careerops.cv_document import (_compose, _evidence, build_document, generation_packet,  # noqa: E402
                                   validate_document, validate_review)
from careerops.materials import _cv_text, approved_evidence, make_draft, record_refs, validate_draft  # noqa: E402
from careerops.policy import default_profile  # noqa: E402
from careerops.store import digest  # noqa: E402

CASES = ROOT / "evals" / "cases"
FIXTURES = ROOT / "evals" / "fixtures"
SUITE = CASES / "spec_suite_v1.json"
SUITE_SHA = CASES / "spec_suite_v1.sha256"
KNOWN_FAILURES = CASES / "known_failures_v1.json"
RESULTS = ROOT / "evals" / "results" / "evidence_gates_v1.json"
REPORT = ROOT / "evals" / "REPORT.md"

SEED = 20260923
EDITS_PER_KIND = 2
Z95 = 1.959963984540054
G1_FIXTURE = "support_to_analytics"

CLAIM_REASON = "New wording or attribution is not proven by evidence IDs. Source wording was retained instead."
SECTION_REASON = "Unknown or repeated employment/project section was omitted."
SKILL_REASON = "Skills without approved evidence were omitted."
FABRICATED_SECTION = "employment:fabricated-employer"
CAPABILITIES = ["Python implementation", "API integration", "AI and language-model tools", "workflow automation",
                "data analysis", "quantitative research", "testing and validation", "customer problem-solving"]
FIXED_THEMES = ("technical", "quantitative", "communication")
EDIT_VOCABULARY = ["additional", "regional", "senior", "weekly", "independently", "successfully", "several", "annual"]
EDIT_KINDS = ("insert", "delete", "substitute", "number_change", "case_change")

CAVEAT = ("Synthetic, frozen case set: every case was written for this evaluation, or generated here from three "
          "fictional profiles. The rates describe these cases only; they are not a random sample of real claims "
          "and do not estimate how often the gates fail in use.")

DEFINITIONS = {
    "G1": "Reject: the record's id is absent from generation_packet(job, profile)['evidence'] after the record is "
          f"added to a copy of the '{G1_FIXTURE}' fixture profile. Consistency check: every fixture record admitted "
          "before the addition is still admitted after it.",
    "G2": "Claim-level reject: build_document records a blocked proposal whose section_id and proposed_text equal "
          "the claim's, with the claim reason. Section-level reject (fabricated section id): the section is absent "
          "from the built document and a block with the 'Unknown or repeated' reason is recorded. Consistency check: "
          "a rejected claim produces exactly that one block; an accepted claim produces no block at all and its text "
          "and evidence ids appear in the built section.",
    "G3": "Reject: the skill is absent from the Skills section's skill_names (or the document has no Skills section). "
          "Consistency check: a reject if and only if a block with the skills reason is recorded.",
    "G5": "Reject: validate_document raises ValueError on the altered, recomposed document. Any other exception is "
          "recorded as an exclusion, not as a reject.",
    "G4": "G4 does not reject; it writes statements. A generated targeted, technical, quantitative, communication or "
          "tools statement is unsupported if any item it names is labelled unsupported in the frozen case. Verbatim "
          "example statements are copies of admitted evidence and are counted separately, not scored.",
}


class DetectorError(RuntimeError):
    """The reject signal and the gate's recorded block disagree."""


# ---------------------------------------------------------------- statistics

def wilson(k, n):
    """Wilson score 95% interval for k successes in n, rounded to four places."""
    if n == 0:
        return None
    p = k / n
    denominator = 1 + Z95 ** 2 / n
    centre = (p + Z95 ** 2 / (2 * n)) / denominator
    half = Z95 * math.sqrt(p * (1 - p) / n + Z95 ** 2 / (4 * n ** 2)) / denominator
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def rate(k, n):
    return {"numerator": k, "denominator": n, "value": round(k / n, 4) if n else None, "wilson95": wilson(k, n)}


def confusion(outcomes):
    """outcomes: iterable of (expected, rejected). Positive class is 'reject'."""
    counts = {"TP": 0, "FN": 0, "FP": 0, "TN": 0}
    for expected, rejected in outcomes:
        if expected == "should_reject":
            counts["TP" if rejected else "FN"] += 1
        else:
            counts["FP" if rejected else "TN"] += 1
    return {"confusion": counts,
            "should_reject": counts["TP"] + counts["FN"], "should_accept": counts["FP"] + counts["TN"],
            "miss_rate": rate(counts["FN"], counts["TP"] + counts["FN"]),
            "false_rejection_rate": rate(counts["FP"], counts["FP"] + counts["TN"])}


def outcome_label(expected, rejected):
    return {("should_reject", True): "TP", ("should_reject", False): "FN",
            ("should_accept", True): "FP", ("should_accept", False): "TN"}[(expected, rejected)]


# ------------------------------------------------------------------ loading

def verify_suite():
    raw = SUITE.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    recorded = SUITE_SHA.read_text(encoding="utf-8").split()[0]
    if actual != recorded:
        raise SystemExit(f"{SUITE.name} does not match its recorded SHA-256 ({actual} != {recorded}); refusing to run.")
    return actual, json.loads(raw.decode("utf-8"))


def load_fixtures():
    profiles = {p["profile_id"]: p for p in json.loads((FIXTURES / "profiles.json").read_text(encoding="utf-8"))["profiles"]}
    documents = {d["profile_id"]: d for d in json.loads((FIXTURES / "reference_documents.json").read_text(encoding="utf-8"))["documents"]}
    for profile_id, document in documents.items():
        if digest(profiles[profile_id]["profile"]) != document["protected_profile_hash"]:
            raise SystemExit(f"{profile_id}: the stored profile does not match its protected_profile_hash; refusing to run.")
    return profiles, documents


class Fixture:
    """One fictional profile with its reference document."""

    def __init__(self, profile_entry, document):
        self.profile_id = profile_entry["profile_id"]
        self.profile = profile_entry["profile"]
        self.job = profile_entry["job"]
        self.document = document
        self.admitted = {r["id"]: r["text"] for r in document["admitted_evidence"]}
        self.all_texts = {r["id"]: r["text"] for r in self.profile["evidence"]}
        self.bank = {s["section_id"]: s for s in document["sections"] if "section_evidence_ids" in s}
        self.home = {}
        for sid, section in self.bank.items():
            for i in section["section_evidence_ids"]:
                self.home.setdefault(i, sid)


def fixtures_by_id():
    profiles, documents = load_fixtures()
    return {pid: Fixture(profiles[pid], documents[pid]) for pid in documents}


# -------------------------------------------------------------------- G1

def admitted_ids(job, profile):
    return [r["id"] for r in generation_packet(job, profile)["evidence"]]


def run_g1(cases, fixtures, exclusions):
    fixture = fixtures[G1_FIXTURE]
    baseline = admitted_ids(fixture.job, fixture.profile)
    results = []
    for case in cases:
        problem = g1_problem(case)
        if problem:
            exclusions.append({"gate": "G1", "case_id": case.get("case_id"), "reason": problem})
            continue
        profile = deepcopy(fixture.profile)
        profile["evidence"].append(deepcopy(case["record"]))
        ids = admitted_ids(fixture.job, profile)
        if [i for i in ids if i != case["record"]["id"]] != baseline:
            raise DetectorError(f"{case['case_id']}: adding one record changed the admission of other records.")
        rejected = case["record"]["id"] not in ids
        stage = None
        if rejected:
            in_approved = any(r["id"] == case["record"]["id"] for r in approved_evidence(profile))
            stage = "coherent_cv_future_or_absence_filter" if in_approved else "approved_evidence_filter"
        results.append({"case_id": case["case_id"], "pair_id": case["pair_id"], "category": case["category"],
                        "expected": case["expected"], "rejected": rejected, "rejected_by": stage,
                        "outcome": outcome_label(case["expected"], rejected)})
    by_category = {}
    for category in sorted({r["category"] for r in results}):
        rows = [r for r in results if r["category"] == category]
        by_category[category] = confusion((r["expected"], r["rejected"]) for r in rows)
    return {"definition": DEFINITIONS["G1"], "fixture_profile": G1_FIXTURE,
            **confusion((r["expected"], r["rejected"]) for r in results), "by_category": by_category, "cases": results}


def g1_problem(case):
    record = case.get("record") or {}
    if case.get("category") not in {"sensitive", "future", "absence"}:
        return "unknown category"
    if case.get("expected") not in {"should_reject", "should_accept"}:
        return "expected is neither should_reject nor should_accept"
    if not (record.get("id") and record.get("text") and record.get("status") == "DIRECT") or set(record) != {"id", "status", "text"}:
        return "record must have exactly id, status DIRECT and text"
    prefix = "claim: " if case["expected"] == "should_reject" else "harmless: "
    if not str(case.get("sense", "")).startswith(prefix):
        return f"sense does not start with '{prefix}' for a {case['expected']} case"
    return None


# -------------------------------------------------------------------- G2

def claim_trial(fixture, section_id, text, ids):
    """Build the reference proposal with one section's claims replaced by this claim."""
    proposal = deepcopy(fixture.document["proposal"])
    claim = {"text": text, "evidence_ids": list(ids)}
    entry = next((s for s in proposal["sections"] if s["section_id"] == section_id), None)
    if entry is None:
        proposal["sections"].append({"section_id": section_id, "claims": [claim]})
    else:
        entry["claims"] = [claim]
    material = build_document(fixture.job, fixture.profile, proposal)
    blocks = material["blocked_proposals"]
    built = {s["section_id"]: s for s in material["sections"]}
    if section_id in fixture.bank:
        matching = [b for b in blocks if b.get("section_id") == section_id and b.get("proposed_text") == text]
        rejected = bool(matching)
        if rejected:
            if len(blocks) != 1 or matching[0].get("reason") != CLAIM_REASON:
                raise DetectorError(f"{section_id}: a rejected claim did not produce exactly one claim block: {blocks}")
        else:
            section = built.get(section_id)
            if blocks or not section or text.strip() not in " ".join(section["paragraphs"]) or not set(ids) <= set(section["evidence_ids"]):
                raise DetectorError(f"{section_id}: a claim with no claim block is not in the built section: {blocks}")
        return {"rejected": rejected, "block": "claim" if rejected else None}
    section_blocks = [b for b in blocks if b.get("reason") == SECTION_REASON]
    absent = section_id not in built
    if absent != bool(section_blocks) or len(blocks) != len(section_blocks):
        raise DetectorError(f"{section_id}: section-level signal and blocks disagree: {blocks}")
    return {"rejected": absent, "block": "section" if absent else None}


def gate_order_reason(fixture, section_id, text, ids):
    """Which check a claim fails first, in the gate's order. Used for attribution, not detection."""
    if section_id not in fixture.bank:
        return "unknown_section"
    if any(i not in fixture.admitted for i in ids):
        return "not_admitted_G1"
    if not set(ids) <= set(fixture.bank[section_id]["section_evidence_ids"]):
        return "wrong_section"
    if text.strip() != " ".join(fixture.admitted[i].strip() for i in ids):
        return "not_verbatim"
    return "passes"


def _normalised(text):
    return " ".join(text.split()).casefold()


def ablations(fixture, section_id, text, ids):
    """Reject decisions of the two ablation baselines for one claim case, including one citing non-admitted records."""
    id_reject = (section_id not in fixture.bank or any(i not in fixture.admitted for i in ids)
                 or not set(ids) <= set(fixture.bank[section_id]["section_evidence_ids"]))
    normalised_reject = id_reject or _normalised(text) != _normalised(" ".join(fixture.admitted[i] for i in ids))
    return {"id_check_only": id_reject, "normalised_verbatim_and_id_check": normalised_reject}


def claim_case(fixture, case_id, family, section_id, text, ids, expected, extra=None):
    trial = claim_trial(fixture, section_id, text, ids)
    reason = gate_order_reason(fixture, section_id, text, ids)
    row = {"case_id": case_id, "profile_id": fixture.profile_id, "family": family, "section_id": section_id,
           "evidence_ids": list(ids), "expected": expected, "rejected": trial["rejected"], "block": trial["block"],
           "gate_order_reason": reason, "attribution_agrees": (reason != "passes") == trial["rejected"],
           "outcome": outcome_label(expected, trial["rejected"]), "ablations": ablations(fixture, section_id, text, ids)}
    row.update(extra or {})
    return row


def summarise_claims(rows):
    summary = {"gate": confusion((r["expected"], r["rejected"]) for r in rows)}
    for name in ("id_check_only", "normalised_verbatim_and_id_check"):
        summary[name] = confusion((r["expected"], r["ablations"][name]) for r in rows)
    reasons = {}
    for r in rows:
        if r["rejected"]:
            reasons[r["gate_order_reason"]] = reasons.get(r["gate_order_reason"], 0) + 1
    summary["rejections_by_first_failing_check"] = dict(sorted(reasons.items()))
    summary["attribution_disagreements"] = sorted(r["case_id"] for r in rows if not r["attribution_agrees"])
    return summary


def run_g2_hand(cases, fixtures, exclusions):
    rows = []
    for case in cases:
        problem = g2_problem(case, fixtures)
        if problem:
            exclusions.append({"gate": "G2", "case_id": case.get("case_id"), "reason": problem})
            continue
        fixture = fixtures[case["profile_id"]]
        rows.append(claim_case(fixture, case["case_id"], case["kind"], case["section_id"], case["claim"]["text"],
                               case["claim"]["evidence_ids"], case["expected"], {"truthful": case["truthful"]}))
    truthful = [r for r in rows if r["truthful"] and r["family"] != "verbatim_control"]
    result = {"definition": DEFINITIONS["G2"], **summarise_claims(rows),
              "truthful_non_verbatim_blocked": rate(sum(r["rejected"] for r in truthful), len(truthful)),
              "cases": rows}
    return result


def g2_problem(case, fixtures):
    if case.get("profile_id") not in fixtures:
        return "unknown profile_id"
    fixture = fixtures[case["profile_id"]]
    claim = case.get("claim") or {}
    if case.get("kind") not in {"paraphrase", "case_change", "reordered_clauses", "other_non_verbatim", "verbatim_control"}:
        return "unknown kind"
    if case.get("expected") not in {"should_reject", "should_accept"}:
        return "expected is neither should_reject nor should_accept"
    if case.get("section_id") not in fixture.bank:
        return "section_id is not an employment, project or research section of the reference document"
    ids = claim.get("evidence_ids") or []
    if not ids or not claim.get("text"):
        return "claim needs text and evidence_ids"
    if not set(ids) <= set(fixture.bank[case["section_id"]]["section_evidence_ids"]):
        return "cited ids are not all in the section's section_evidence_ids"
    verbatim = claim["text"] == " ".join(fixture.admitted[i] for i in ids)
    if (case["kind"] == "verbatim_control") != verbatim:
        return "kind verbatim_control does not match whether the text is verbatim"
    if (case["expected"] == "should_accept") != verbatim:
        return "label does not follow the verbatim specification"
    return None


def generate_g2_systematic(fixtures):
    """Generated claim cases: (fixture, case_id, family, section_id, text, ids, expected, extra)."""
    cases = []
    for pid, fixture in fixtures.items():
        count = {}

        def add(family, section_id, text, ids, expected, extra=None):
            count[family] = count.get(family, 0) + 1
            cases.append((fixture, f"G2S-{pid}-{family}-{count[family]:03d}", family, section_id, text, ids, expected, extra or {}))

        for x, text in fixture.admitted.items():
            for sid, section in fixture.bank.items():
                if x not in section["section_evidence_ids"]:
                    add("wrong_section", sid, text, [x], "should_reject", {"statement_home": fixture.home.get(x)})
        for x, text in fixture.admitted.items():
            for y in fixture.admitted:
                if y == x or y not in fixture.home or fixture.admitted[y].strip() == text.strip():
                    continue
                add("wrong_id", fixture.home[y], text, [y], "should_reject",
                    {"statement_home": fixture.home.get(x), "same_home": fixture.home.get(x) == fixture.home[y]})
        first_employment = next(sid for sid in fixture.bank if sid.startswith("employment:"))
        homes = {}
        for category in ("employment", "projects", "research"):
            for record in fixture.profile.get(category, []):
                sid = f"{category}:{record.get('record_id')}"
                for ref in record_refs(record):
                    homes.setdefault(ref, sid)
        for record in fixture.profile["evidence"]:
            if record["id"] in fixture.admitted:
                continue
            home = homes.get(record["id"])
            sid = home if home in fixture.bank else first_employment
            add("non_admitted_copied_exactly", sid, record["text"], [record["id"]], "should_reject",
                {"placed_under": "home section" if home in fixture.bank else "first employment section (no home section)"})
        for x, text in fixture.admitted.items():
            if x in fixture.home:
                add("control_correctly_cited", fixture.home[x], text, [x], "should_accept")
        for x, text in fixture.admitted.items():
            if x in fixture.home:
                add("fabricated_section", FABRICATED_SECTION, text, [x], "should_reject")
    return cases


def run_g2_systematic(fixtures):
    rows = [claim_case(*case) for case in generate_g2_systematic(fixtures)]
    families = {}
    for family in sorted({r["family"] for r in rows}):
        families[family] = summarise_claims([r for r in rows if r["family"] == family])
    admitted_only = [r for r in rows if r["gate_order_reason"] != "not_admitted_G1"]
    return {"definition": DEFINITIONS["G2"], "all": summarise_claims(rows),
            "claim_gate_admitted_citations_only": summarise_claims(admitted_only),
            "by_family": families, "cases": rows}


# ------------------------------------------------------- G2 edit property

def single_token_edit(text, kind, rng):
    tokens = text.split(" ")
    for _ in range(20):
        edited = list(tokens)
        if kind == "insert":
            word = rng.choice([w for w in EDIT_VOCABULARY if w not in {t.casefold() for t in tokens}])
            edited.insert(rng.randint(0, len(tokens)), word)
        elif kind == "delete":
            if len(tokens) < 2:
                return None
            edited.pop(rng.randrange(len(tokens)))
        elif kind == "substitute":
            index = rng.randrange(len(tokens))
            edited[index] = rng.choice([w for w in EDIT_VOCABULARY if w != tokens[index].casefold()])
        elif kind == "number_change":
            positions = [i for i, t in enumerate(tokens) if re.search(r"\d", t)]
            if not positions:
                return None
            index = rng.choice(positions)
            edited[index] = re.sub(r"\d+", lambda m: str(int(m.group()) + rng.randint(1, 9)), tokens[index], count=1)
        elif kind == "case_change":
            positions = [i for i, t in enumerate(tokens) if t.swapcase() != t]
            if not positions:
                return None
            index = rng.choice(positions)
            edited[index] = tokens[index].swapcase()
        result = " ".join(edited)
        if result.strip() != text.strip():
            return result
    raise DetectorError(f"could not make a {kind} edit that changes: {text}")


def run_g2_edits(fixtures):
    rng = random.Random(SEED)
    rows, not_applicable, no_section = [], {k: 0 for k in EDIT_KINDS}, []
    for pid, fixture in fixtures.items():
        for x, text in fixture.admitted.items():
            if x not in fixture.home:
                no_section.append(x)
                continue
            for kind in EDIT_KINDS:
                for n in range(EDITS_PER_KIND):
                    edited = single_token_edit(text, kind, rng)
                    if edited is None:
                        not_applicable[kind] += 1
                        break
                    row = claim_case(fixture, f"G2E-{pid}-{x.split(':')[-1]}-{kind}-{n + 1}", kind,
                                     fixture.home[x], edited, [x], "should_reject")
                    row["edited_text"] = edited
                    rows.append(row)
    by_kind = {}
    for kind in EDIT_KINDS:
        chosen = [r for r in rows if r["family"] == kind]
        by_kind[kind] = {"blocked": sum(r["rejected"] for r in chosen), "edits": len(chosen),
                         "normalised_verbatim_baseline_blocked": sum(r["ablations"]["normalised_verbatim_and_id_check"] for r in chosen),
                         "id_check_only_baseline_blocked": sum(r["ablations"]["id_check_only"] for r in chosen),
                         "statements_without_this_edit": not_applicable[kind]}
    return {"seed": SEED, "edits_per_kind_per_statement": EDITS_PER_KIND,
            "statements": sum(len([x for x in f.admitted if x in f.home]) for f in fixtures.values()),
            "statements_with_no_section": sorted(no_section),
            "blocked": sum(r["rejected"] for r in rows), "edits": len(rows), "by_kind": by_kind, "cases": rows}


# -------------------------------------------------------------------- G3

G3_JOB = {"title": "Analyst", "company": "Example Employer Ltd", "description": "General analyst role.", "requirements": []}


def skill_trial(skill, records):
    profile = default_profile()
    profile.update(name="Example Candidate", skills=[skill], evidence=deepcopy(records),
                   employment=[{"record_id": "role", "title": "Analyst", "employer": "Example Employer", "start": "2020",
                                "end": "2022", "evidence_ids": [r["id"] for r in records]}])
    proposal = {"profile_statement_ids": [], "skill_names": [skill], "direction_reason": "", "sections": []}
    material = build_document(G3_JOB, profile, proposal)
    skills = next((s for s in material["sections"] if s["section_id"] == "skills"), {"skill_names": []})
    rejected = skill not in skills["skill_names"]
    skill_blocks = [b for b in material["blocked_proposals"] if b.get("reason") == SKILL_REASON]
    if rejected != bool(skill_blocks):
        raise DetectorError(f"{skill}: skill signal and skills block disagree: {material['blocked_proposals']}")
    admitted = set(_evidence(profile))
    return rejected, {r["id"]: r["id"] in admitted for r in records}


def run_g3(cases, exclusions):
    results = []
    for case in cases:
        problem = g3_problem(case)
        if problem:
            exclusions.append({"gate": "G3", "case_id": case.get("case_id"), "reason": problem})
            continue
        rejected, admitted = skill_trial(case["skill"], case["evidence"])
        mention_admitted = any(admitted[r["id"]] and case["skill"].casefold() in r["text"].casefold() for r in case["evidence"])
        results.append({"case_id": case["case_id"], "skill": case["skill"], "category": case["category"],
                        "expected": case["expected"], "rejected": rejected, "records_admitted": admitted,
                        "skill_mention_admitted": mention_admitted,
                        "rejected_by": (None if not rejected else "skill_check" if mention_admitted else "admission_G1"),
                        "outcome": outcome_label(case["expected"], rejected)})
    by_category = {}
    for category in sorted({r["category"] for r in results}):
        rows = [r for r in results if r["category"] == category]
        by_category[category] = {"cases": len(rows), "rejected": sum(r["rejected"] for r in rows)}
    return {"definition": DEFINITIONS["G3"], **confusion((r["expected"], r["rejected"]) for r in results),
            "rejections_by_stage": {stage: sum(r["rejected_by"] == stage for r in results) for stage in ("admission_G1", "skill_check")},
            "skill_check_admitted_mentions_only": confusion((r["expected"], r["rejected"]) for r in results if r["skill_mention_admitted"]),
            "mention_not_admitted": confusion((r["expected"], r["rejected"]) for r in results if not r["skill_mention_admitted"]),
            "by_category": by_category, "cases": results}


def g3_problem(case):
    categories = {"positive_use", "negated", "homograph_or_substring", "to_learn", "someone_else"}
    if case.get("category") not in categories:
        return "unknown category"
    if (case["category"] == "positive_use") != (case.get("expected") == "should_accept"):
        return "expected does not follow the category"
    records = case.get("evidence") or []
    if not 1 <= len(records) <= 3 or any(set(r) != {"id", "status", "text"} or r["status"] != "DIRECT" for r in records):
        return "evidence must be one to three DIRECT records with only id, status and text"
    if not any(case["skill"].casefold() in r["text"].casefold() for r in records):
        return "the skill does not appear in any evidence record"
    return None


# -------------------------------------------------------------------- G4

def score_profile_statement(statement, labels):
    """Return (theme, named items, unsupported items) for one generated statement."""
    identity, text = statement["id"], statement["text"]
    if identity in FIXED_THEMES:
        return identity, [identity], [] if labels[identity] else [identity]
    if identity == "targeted":
        prefix, suffix = "Combines ", " to solve practical problems."
        if not (text.startswith(prefix) and text.endswith(suffix)):
            raise DetectorError(f"unrecognised targeted statement: {text}")
        middle = text[len(prefix):-len(suffix)]
        names = sorted((n for n in CAPABILITIES if n in middle), key=middle.index)
        rebuilt = ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else (names[0] if names else "")
        if rebuilt != middle:
            raise DetectorError(f"could not parse targeted statement: {text}")
        return identity, names, [n for n in names if not labels["targeted"][n]]
    if identity == "tools":
        prefix = "Practical technical skills include "
        if not (text.startswith(prefix) and text.endswith(".")):
            raise DetectorError(f"unrecognised tools statement: {text}")
        names = text[len(prefix):-1].split(", ")
        if any(n not in labels["tools"] for n in names):
            raise DetectorError(f"tools statement names a skill with no label: {text}")
        return identity, names, [n for n in names if not labels["tools"][n]]
    raise DetectorError(f"unknown profile statement id: {identity}")


def run_g4(cases, exclusions):
    statements, sets = [], []
    for case in cases:
        problem = g4_problem(case)
        if problem:
            exclusions.append({"gate": "G4", "case_id": case.get("case_id"), "reason": problem})
            continue
        profile = default_profile()
        profile.update(name="Example Candidate", skills=list(case["skills"]), evidence=deepcopy(case["evidence"]))
        job = {"requirements": [], **case["job"]}
        packet = generation_packet(job, profile)
        admitted = {r["id"] for r in packet["evidence"]}
        examples = 0
        scored = []
        for statement in packet["allowed_profile_statements"]:
            if statement["id"].startswith("example:"):
                examples += 1
                continue
            theme, named, unsupported = score_profile_statement(statement, case["labels"])
            scored.append({"case_id": case["case_id"], "has_technical_work": case["has_technical_work"], "theme": theme,
                           "text": statement["text"], "named": named, "unsupported_items": unsupported,
                           "unsupported": bool(unsupported)})
        statements += scored
        sets.append({"case_id": case["case_id"], "has_technical_work": case["has_technical_work"],
                     "records": len(case["evidence"]), "records_not_admitted": sorted(r["id"] for r in case["evidence"] if r["id"] not in admitted),
                     "example_statements": examples, "scored_statements": len(scored),
                     "unsupported_statements": sum(s["unsupported"] for s in scored)})

    def summary(rows):
        return rate(sum(r["unsupported"] for r in rows), len(rows))

    return {"definition": DEFINITIONS["G4"],
            "unsupported_statement_rate": summary(statements),
            "by_theme": {t: summary([s for s in statements if s["theme"] == t]) for t in ("targeted", *FIXED_THEMES, "tools")},
            "by_evidence_type": {"no_technical_work": summary([s for s in statements if not s["has_technical_work"]]),
                                 "technical_work": summary([s for s in statements if s["has_technical_work"]])},
            "evidence_sets_with_an_unsupported_statement": rate(sum(s["unsupported_statements"] > 0 for s in sets), len(sets)),
            "example_statements_not_scored": sum(s["example_statements"] for s in sets),
            "evidence_sets": sets, "statements": statements}


def g4_problem(case):
    labels = case.get("labels") or {}
    if any(not isinstance(labels.get(t), bool) for t in FIXED_THEMES):
        return "technical, quantitative and communication labels must be booleans"
    if sorted(labels.get("tools", {})) != sorted(case.get("skills", [])):
        return "tools labels do not cover exactly the listed skills"
    if sorted(labels.get("targeted", {})) != sorted(CAPABILITIES):
        return "targeted labels do not cover exactly the eight capabilities"
    if not 4 <= len(case.get("evidence", [])) <= 8:
        return "evidence must have four to eight records"
    return None


# -------------------------------------------------------------------- G5

def _section(sections, section_id):
    matches = [s for s in sections if s["section_id"] == section_id]
    if len(matches) != 1:
        raise ValueError(f"section {section_id} not found exactly once")
    return matches[0]


def _paragraph_index(section, index):
    if type(index) is not int or not 0 <= index < len(section["paragraphs"]):
        raise ValueError("paragraph_index out of range")
    return index


def apply_alteration(material, alteration, fixture):
    """Apply one schema alteration to material['sections'] in place. ValueError means a malformed case."""
    sections = material["sections"]
    kind = alteration.get("type")
    if kind == "unaltered":
        return
    if kind == "append_text":
        section = _section(sections, alteration["section_id"])
        section["paragraphs"][_paragraph_index(section, alteration["paragraph_index"])] += " " + alteration["text"]
    elif kind == "replace_paragraph":
        section = _section(sections, alteration["section_id"])
        section["paragraphs"][_paragraph_index(section, alteration["paragraph_index"])] = alteration["text"]
    elif kind == "replace_in_paragraph":
        section = _section(sections, alteration["section_id"])
        index = _paragraph_index(section, alteration["paragraph_index"])
        if section["paragraphs"][index].count(alteration["old"]) != 1:
            raise ValueError("'old' does not occur exactly once in the paragraph")
        section["paragraphs"][index] = section["paragraphs"][index].replace(alteration["old"], alteration["new"])
    elif kind == "set_heading":
        _section(sections, alteration["section_id"])["heading"] = alteration["heading"]
    elif kind == "set_subheading":
        _section(sections, alteration["section_id"])["subheading"] = alteration["subheading"]
    elif kind == "inject_skill":
        section = _section(sections, "skills")
        if alteration["skill"] in section["skill_names"]:
            raise ValueError("the skill is already listed")
        section["skill_names"].append(alteration["skill"])
        section["paragraphs"] = [" · ".join(section["skill_names"])]
    elif kind == "set_education_paragraphs":
        _section(sections, "education")["paragraphs"] = list(alteration["paragraphs"])
    elif kind == "move_evidence":
        text = fixture.admitted.get(alteration["evidence_id"])
        source = _section(sections, alteration["from_section_id"])
        target = _section(sections, alteration["to_section_id"])
        holders = [i for i, p in enumerate(source["paragraphs"]) if text and text in p]
        if len(holders) != 1 or source["paragraphs"][holders[0]].count(text) != 1 or alteration["evidence_id"] not in source["evidence_ids"]:
            raise ValueError("the statement is not printed exactly once in the source section")
        remaining = " ".join(source["paragraphs"][holders[0]].replace(text, "", 1).split())
        if remaining:
            source["paragraphs"][holders[0]] = remaining
        else:
            source["paragraphs"].pop(holders[0])
        source["evidence_ids"] = [i for i in source["evidence_ids"] if i != alteration["evidence_id"]]
        target["paragraphs"].append(text)
        target["evidence_ids"] = target["evidence_ids"] + [alteration["evidence_id"]]
    elif kind == "remove_paragraph":
        section = _section(sections, alteration["section_id"])
        removed = section["paragraphs"].pop(_paragraph_index(section, alteration["paragraph_index"]))
        section["evidence_ids"] = [i for i in section["evidence_ids"]
                                   if not (fixture.admitted[i] in removed and not any(fixture.admitted[i] in p for p in section["paragraphs"]))]
    elif kind == "reorder_paragraphs":
        section = _section(sections, alteration["section_id"])
        order = alteration["order"]
        if sorted(order) != list(range(len(section["paragraphs"]))):
            raise ValueError("order is not a permutation of the paragraph indices")
        section["paragraphs"] = [section["paragraphs"][i] for i in order]
    else:
        raise ValueError(f"unknown alteration type {kind!r}")


def document_trial(fixture, alter):
    """Return (rejected, message) for a reference document changed by alter(material)."""
    material = deepcopy(fixture.document["material"])
    alter(material)
    _compose(material)
    try:
        validate_document(material, fixture.profile, fixture.job)
    except ValueError as exc:
        return True, str(exc)
    return False, None


def run_g5(cases, fixtures, exclusions):
    results = []
    for case in cases:
        fixture = fixtures.get(case.get("profile_id"))
        if fixture is None or case.get("expected") not in {"should_reject", "should_accept"}:
            exclusions.append({"gate": "G5", "case_id": case.get("case_id"), "reason": "unknown profile_id or label"})
            continue
        try:
            apply_alteration(deepcopy(fixture.document["material"]), case["alteration"], fixture)
        except (ValueError, KeyError) as exc:
            exclusions.append({"gate": "G5", "case_id": case["case_id"], "reason": f"malformed alteration: {exc}"})
            continue
        try:
            rejected, message = document_trial(fixture, lambda m: apply_alteration(m, case["alteration"], fixture))
        except Exception as exc:  # noqa: BLE001 - any non-ValueError is a validator crash, reported not counted
            exclusions.append({"gate": "G5", "case_id": case["case_id"],
                               "reason": f"validator raised {type(exc).__name__}, not ValueError"})
            continue
        results.append({"case_id": case["case_id"], "profile_id": case["profile_id"], "alteration": case["alteration"]["type"],
                        "expected": case["expected"], "rejected": rejected, "validator_message": message,
                        "outcome": outcome_label(case["expected"], rejected)})
    by_type = {}
    for kind in sorted({r["alteration"] for r in results}):
        rows = [r for r in results if r["alteration"] == kind]
        by_type[kind] = {"cases": len(rows), "rejected": sum(r["rejected"] for r in rows)}
    return {"definition": DEFINITIONS["G5"], **confusion((r["expected"], r["rejected"]) for r in results),
            "by_alteration_type": by_type, "cases": results}


# ---------------------------------------------------------- known failures

def probe_known_failure(item, fixtures):
    """Run one white-box probe and return its observed behaviour as a short token."""
    probe = item["probe"]
    kind = probe["kind"]
    if kind == "admission":
        fixture = fixtures[G1_FIXTURE]
        profile = deepcopy(fixture.profile)
        profile["evidence"].append({"id": "bank:known-failure-1", "status": "DIRECT", "text": probe["text"]})
        return "admitted" if "bank:known-failure-1" in admitted_ids(fixture.job, profile) else "not_admitted"
    if kind == "skill":
        records = [{"id": f"bank:known-failure-{n}", "status": "DIRECT", "text": t} for n, t in enumerate(probe["evidence"], 1)]
        rejected, _ = skill_trial(probe["skill"], records)
        return "omitted" if rejected else "listed"
    if kind == "profile_statements":
        profile = default_profile()
        profile.update(name="Example Candidate", skills=list(probe.get("skills", [])),
                       evidence=[{"id": f"bank:known-failure-{n}", "status": "DIRECT", "text": t} for n, t in enumerate(probe["evidence"], 1)])
        ids = [s["id"] for s in generation_packet(probe["job"], profile)["allowed_profile_statements"]]
        return "generated" if probe["statement_id"] in ids else "not_generated"
    if kind in {"document_inner_splice", "document_duplicate_paragraph"}:
        fixture = fixtures[probe["profile_id"]]

        def alter(material):
            section = _section(material["sections"], probe["section_id"])
            if kind == "document_duplicate_paragraph":
                section["paragraphs"].append(section["paragraphs"][probe["paragraph_index"]])
                return
            outer, inner = fixture.admitted[probe["outer_id"]], fixture.admitted[probe["inner_id"]]
            # Insert the inner statement straight after a space in the outer one, so that removing the
            # inner statement leaves the outer statement exactly.
            cut = outer.index(" ", len(outer) // 2) + 1
            section["paragraphs"][probe["paragraph_index"]] = outer[:cut] + inner + outer[cut:]
        rejected, _ = document_trial(fixture, alter)
        return "rejected" if rejected else "passed"
    if kind == "advert_quote":
        fixture = fixtures[probe["profile_id"]]
        finding = {"category": "document_problem", "cv_passage": "", "advert_requirement": probe["quote"],
                   "evidence_ids": [], "severity": "medium", "recommended_action": "Reword the opening paragraph.",
                   "action": {"type": "suggest_rewrite", "section_index": 0, "paragraph_index": 0, "to_index": -1,
                              "evidence_ids": [], "text": "Reworded opening."}}
        review = validate_review({"findings": [finding], "summary": ""}, fixture.job, fixture.profile,
                                 fixture.document["material"], "red")
        return "anchored" if review["findings"][0]["category"] == "document_problem" else "demoted_to_question"
    if kind == "draft_heading":
        fixture = fixtures[probe["profile_id"]]
        draft = make_draft(fixture.job, fixture.profile)
        validate_draft(draft, fixture.profile)  # the unaltered draft must pass, or the probe proves nothing
        section = next(s for s in draft["sections"] if s.get("profile_ref") == probe["profile_ref"])
        section.update(heading=probe["heading"], subheading=probe["subheading"])
        draft["cv_text"] = _cv_text(draft)
        try:
            validate_draft(draft, fixture.profile)
        except ValueError:
            return "rejected"
        return "passed"
    raise DetectorError(f"unknown known-failure probe kind {kind!r}")


def run_known_failures(fixtures):
    data = json.loads(KNOWN_FAILURES.read_text(encoding="utf-8"))
    rows = []
    for item in data["cases"]:
        observed = probe_known_failure(item, fixtures)
        rows.append({"id": item["id"], "gate": item["gate"], "summary": item["summary"],
                     "correct_behaviour": item["correct_behaviour"], "documented_today": item["documented_today"],
                     "observed_today": observed, "matches_documentation": observed == item["documented_today"],
                     "failing_today": observed == item["failing_observation"]})
    return {"file": KNOWN_FAILURES.name, "note": data["note"], "cases": rows}


# -------------------------------------------------------------------- run

def run():
    sha, suite = verify_suite()
    fixtures = fixtures_by_id()
    files = suite["files"]
    exclusions = []
    results = {
        "suite": {"file": SUITE.name, "sha256": sha, "cases": sum(len(f["cases"]) for f in files.values())},
        "caveat": CAVEAT,
        "gates": {
            "G1": run_g1(files["g1_admission.json"]["cases"], fixtures, exclusions),
            "G2_hand": run_g2_hand(files["g2_claims.json"]["cases"], fixtures, exclusions),
            "G2_systematic": run_g2_systematic(fixtures),
            "G2_edit_property": run_g2_edits(fixtures),
            "G3": run_g3(files["g3_skills.json"]["cases"], exclusions),
            "G4": run_g4(files["g4_profile_statements.json"]["cases"], exclusions),
            "G5": run_g5(files["g5_altered_documents.json"]["cases"], fixtures, exclusions),
        },
        "exclusions": exclusions,
        "known_failures": run_known_failures(fixtures),
    }
    return results


def encode_results(results):
    return json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


# ------------------------------------------------------------------ report

def pct(value):
    return "n/a" if value is None else f"{100 * value:.1f}%"


def fmt_rate(r):
    if not r["denominator"]:
        return f"{r['numerator']}/{r['denominator']} (undefined)"
    lo, hi = r["wilson95"]
    return f"{r['numerator']}/{r['denominator']} = {pct(r['value'])} (95% CI {pct(lo)}–{pct(hi)})"


def fmt_confusion(c):
    k = c["confusion"]
    return f"TP {k['TP']}, FN {k['FN']}, FP {k['FP']}, TN {k['TN']}"


def render_report(results):
    g = results["gates"]
    systematic = g["G2_systematic"]
    edits = g["G2_edit_property"]
    lines = [
        "# Evidence gate evaluation",
        "",
        "Generated by `python -m evals.evidence_gates` from `evals/results/evidence_gates_v1.json`. Do not edit by hand;",
        "`tests/test_evidence_gate_eval.py` fails if this file differs from a fresh run.",
        "",
        f"**Caveat.** {results['caveat']}",
        "",
        f"Case file: `evals/cases/{results['suite']['file']}`, {results['suite']['cases']} cases, SHA-256 "
        f"`{results['suite']['sha256']}` (verified before the run).",
        "",
        "Positive class is \"reject\". Miss rate = FN / (TP + FN), the share of should-reject cases the gate let through.",
        "False-rejection rate = FP / (FP + TN), the share of should-accept cases the gate blocked. Intervals are Wilson",
        "95% intervals. There is deliberately no score combined across gates: each gate has a different unit.",
        "",
        "## Summary",
        "",
        "| Gate | Unit | Cases | Miss rate | False-rejection rate |",
        "|---|---|---|---|---|",
    ]
    rows = [
        ("G1 admission filter", "evidence record", g["G1"]),
        ("G2 claim gate, hand-written non-verbatim claims", "proposed claim", g["G2_hand"]["gate"]),
        ("G2 claim gate, generated verbatim-but-wrong claims (admitted citations only)", "proposed claim",
         systematic["claim_gate_admitted_citations_only"]["gate"]),
        ("G2 generated claims citing non-admitted records (rejected by G1, not the claim gate)", "proposed claim",
         systematic["by_family"]["non_admitted_copied_exactly"]["gate"]),
        ("G3 skill gate, all cases", "listed skill", g["G3"]),
        ("G3 skill check alone (cases whose skill mention was admitted)", "listed skill", g["G3"]["skill_check_admitted_mentions_only"]),
        ("G3 cases whose skill mention was not admitted (rejected by G1, not the skill check)", "listed skill", g["G3"]["mention_not_admitted"]),
        ("G5 whole-document validator", "altered document", g["G5"]),
    ]
    for name, unit, c in rows:
        lines.append(f"| {name} | {unit} | {c['should_reject'] + c['should_accept']} | {fmt_rate(c['miss_rate'])} | "
                     f"{fmt_rate(c['false_rejection_rate'])} |")
    lines += [
        f"| G2 edited text (single-token edits) | proposed claim | {edits['edits']} | "
        f"{edits['blocked']}/{edits['edits']} blocked by construction (exact-match gate) | not applicable |",
        f"| G4 profile statements (writes claims, does not reject) | generated statement | "
        f"{g['G4']['unsupported_statement_rate']['denominator']} | unsupported-statement rate "
        f"{fmt_rate(g['G4']['unsupported_statement_rate'])} | not applicable |",
        "",
        "## What counts as a reject",
        "",
    ]
    for gate in ("G1", "G2", "G3", "G5", "G4"):
        lines.append(f"- **{gate}.** {DEFINITIONS[gate]}")
    lines += ["", "## G1 admission filter", "", f"Confusion: {fmt_confusion(g['G1'])}.", "",
              "| Category | Should reject | Should accept | Miss rate | False-rejection rate |", "|---|---|---|---|---|"]
    for category, c in g["G1"]["by_category"].items():
        lines.append(f"| {category} | {c['should_reject']} | {c['should_accept']} | {fmt_rate(c['miss_rate'])} | {fmt_rate(c['false_rejection_rate'])} |")
    misses = [r["case_id"] for r in g["G1"]["cases"] if r["outcome"] == "FN"]
    false_rejections = [r["case_id"] for r in g["G1"]["cases"] if r["outcome"] == "FP"]
    lines += ["", f"Missed (admitted although they should not be): {', '.join(misses) or 'none'}.",
              f"Falsely rejected (harmless records kept out): {', '.join(false_rejections) or 'none'}."]

    hand = g["G2_hand"]
    lines += ["", "## G2 claim gate", "",
              "The gate accepts a claim only if its cited ids belong to the section and its text is an exact copy of the",
              "cited statements. Edited text therefore cannot pass, so edits are reported as a property below, not as a rate.",
              "",
              "### Hand-written non-verbatim claims (frozen suite)", "",
              f"Confusion: {fmt_confusion(hand['gate'])}. Labels follow the specification \"claims must be verbatim copies",
              "of approved evidence\", so a truthful paraphrase is labelled should-reject.",
              f"Usability cost: truthful non-verbatim claims blocked {fmt_rate(hand['truthful_non_verbatim_blocked'])}.",
              "", "### Generated claim suite", "",
              "Generated from the three fixture profiles, one build per claim: every admitted statement cited under every",
              "section it does not belong to; every statement cited with every other statement's id (placed in that id's",
              "section, so only the text-to-id link is wrong); every non-admitted record copied exactly; every statement cited",
              f"under a fabricated section id; and every statement correctly cited as a control. Total {len(systematic['cases'])} claims.",
              "", "| Family | Cases | Rejected | Miss rate | False-rejection rate |", "|---|---|---|---|---|"]
    for family, s in systematic["by_family"].items():
        c = s["gate"]
        total = c["should_reject"] + c["should_accept"]
        rejected = c["confusion"]["TP"] + c["confusion"]["FP"]
        lines.append(f"| {family} | {total} | {rejected} | {fmt_rate(c['miss_rate'])} | {fmt_rate(c['false_rejection_rate'])} |")
    reasons = systematic["all"]["rejections_by_first_failing_check"]
    lines += ["", "Rejections by the first check the claim fails, in the gate's order: " +
              "; ".join(f"{k} {v}" for k, v in reasons.items()) + ".",
              f"Rejections attributed to the admission filter (G1) rather than the claim gate: {reasons.get('not_admitted_G1', 0)}. "
              "These are the non-admitted records; they are excluded from the claim gate's row in the summary.",
              f"Cases where the gate's decision disagrees with that attribution: {len(systematic['all']['attribution_disagreements'])}.",
              "", "### Edited-text property", "",
              f"Seeded (seed {edits['seed']}) single-token edits, {edits['edits_per_kind_per_statement']} per kind, on each of the "
              f"{edits['statements']} admitted statements that belong to a section, cited correctly under that section.",
              f"Result: {edits['blocked']}/{edits['edits']} blocked by construction (exact-match gate).", "",
              "| Edit | Blocked by the gate | Accepted by the normalised-verbatim baseline | Statements with no such token |",
              "|---|---|---|---|"]
    for kind, k in edits["by_kind"].items():
        lines.append(f"| {kind} | {k['blocked']}/{k['edits']} blocked by construction | "
                     f"{k['edits'] - k['normalised_verbatim_baseline_blocked']}/{k['edits']} | {k['statements_without_this_edit']} |")
    lines += ["", "Case changes are blocked by design: the specification requires verbatim copies, so a change of case is "
              "treated as new wording even when the meaning is unchanged.",
              f"Statements that belong to no section (education records) were not edited: {', '.join(edits['statements_with_no_section'])}.",
              "", "### Ablation baselines on every claim case, including those citing non-admitted records", "",
              "Each row counts every case in its corpus. The generated should-reject cases therefore include the claims "
              "citing non-admitted records, which the summary attributes to the admission filter (G1) and leaves out of "
              "the claim gate's row.", "",
              "(a) id check only: cited ids are admitted and belong to the section; text ignored.",
              "(b) normalised verbatim and id check: (a), plus text equal to the cited statements after collapsing whitespace and case folding.",
              "", "| Corpus | Rule | Confusion | Miss rate | False-rejection rate |", "|---|---|---|---|---|"]
    for corpus, s in (("hand-written", hand), ("generated", systematic["all"])):
        for label, key in (("gate", "gate"), ("(a) id check only", "id_check_only"), ("(b) normalised verbatim + id", "normalised_verbatim_and_id_check")):
            c = s[key]
            lines.append(f"| {corpus} | {label} | {fmt_confusion(c)} | {fmt_rate(c['miss_rate'])} | {fmt_rate(c['false_rejection_rate'])} |")
    edit_rows = edits["cases"]
    for label, key in (("(a) id check only", "id_check_only"), ("(b) normalised verbatim + id", "normalised_verbatim_and_id_check")):
        blocked = sum(r["ablations"][key] for r in edit_rows)
        lines.append(f"| edited text | {label} | blocked {blocked}/{len(edit_rows)} | {len(edit_rows) - blocked}/{len(edit_rows)} accepted | not applicable |")

    g3 = g["G3"]
    lines += ["", "## G3 skill gate", "", f"Confusion: {fmt_confusion(g3)}.",
              f"Rejections by stage: admission filter (G1) {g3['rejections_by_stage']['admission_G1']}, skill check "
              f"{g3['rejections_by_stage']['skill_check']}. A rejection is attributed to the admission filter when no record "
              "that mentions the skill was admitted, so the skill check never saw a mention.",
              "", "| Category | Cases | Rejected |", "|---|---|---|"]
    for category, c in g3["by_category"].items():
        lines.append(f"| {category} | {c['cases']} | {c['rejected']} |")
    lines += ["", "Missed: " + (", ".join(f"{r['case_id']} ({r['skill']}, {r['category']})" for r in g3["cases"] if r["outcome"] == "FN") or "none") + ".",
              "Falsely rejected: " + (", ".join(f"{r['case_id']} ({r['skill']})" for r in g3["cases"] if r["outcome"] == "FP") or "none") + "."]

    g5 = g["G5"]
    lines += ["", "## G5 whole-document validator", "", f"Confusion: {fmt_confusion(g5)}.", "",
              "| Alteration | Cases | Rejected |", "|---|---|---|"]
    for kind, c in g5["by_alteration_type"].items():
        lines.append(f"| {kind} | {c['cases']} | {c['rejected']} |")
    lines += ["", "Missed: " + (", ".join(r["case_id"] for r in g5["cases"] if r["outcome"] == "FN") or "none") + ".",
              "Falsely rejected: " + (", ".join(r["case_id"] for r in g5["cases"] if r["outcome"] == "FP") or "none") + "."]

    g4 = g["G4"]
    lines += ["", "## G4 profile statements", "",
              "G4 writes profile statements from keyword patterns. For each frozen evidence set the real generator was run",
              "and each statement was scored against labels written before any output was seen.",
              f"Unsupported-statement rate: {fmt_rate(g4['unsupported_statement_rate'])}.",
              f"Evidence sets with at least one unsupported statement: {fmt_rate(g4['evidence_sets_with_an_unsupported_statement'])}.",
              f"Verbatim example statements generated but not scored: {g4['example_statements_not_scored']}.", "",
              "| Split | Unsupported statements |", "|---|---|"]
    for theme, r in g4["by_theme"].items():
        lines.append(f"| theme: {theme} | {fmt_rate(r)} |")
    for split, r in g4["by_evidence_type"].items():
        lines.append(f"| evidence: {split.replace('_', ' ')} | {fmt_rate(r)} |")
    never = [t for t, r in g4["by_theme"].items() if not r["denominator"]]
    if never:
        lines += ["", f"Themes never generated for these evidence sets, so nothing to score: {', '.join(never)}."]
    lines += ["", "Unsupported statements:", ""]
    for s in g4["statements"]:
        if s["unsupported"]:
            lines.append(f"- {s['case_id']} ({s['theme']}): \"{s['text']}\" — unsupported: {', '.join(s['unsupported_items'])}")

    known = results["known_failures"]
    lines += ["", "## Known failures (white-box regression cases)", "",
              f"{known['note']}", "",
              "| Id | Gate | Case | Correct behaviour | Documented | Observed today | Today |", "|---|---|---|---|---|---|---|"]
    for k in known["cases"]:
        today = "FAIL" if k["failing_today"] else "pass"
        if not k["matches_documentation"]:
            today += " (differs from the documented behaviour: update known_failures_v1.json)"
        lines.append(f"| {k['id']} | {k['gate']} | {k['summary']} | {k['correct_behaviour']} | {k['documented_today']} | "
                     f"{k['observed_today']} | {today} |")

    lines += ["", "## Exclusions", ""]
    if results["exclusions"]:
        lines += [f"- {e['gate']} {e['case_id']}: {e['reason']}" for e in results["exclusions"]]
    else:
        lines.append("No case in the frozen suite was excluded.")
    lines += ["", "## Reviewer output", "",
              "Review quality is not measured here: that would need a live model. "
              "`tests/test_evidence_gate_eval.py::test_reviewer_output_validation_plumbing` uses a scripted reviewer, which "
              "catches 100% of planted errors by construction, so it tests the validation plumbing only.", ""]
    return "\n".join(lines)


def main(argv):
    results = run()
    encoded, report = encode_results(results), render_report(results)
    if "--check" in argv:
        same = RESULTS.exists() and REPORT.exists() and RESULTS.read_text(encoding="utf-8") == encoded \
            and REPORT.read_text(encoding="utf-8") == report
        print("results and report match a fresh run" if same else "results or report differ from a fresh run")
        return 0 if same else 1
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(encoded, encoding="utf-8")
    REPORT.write_text(report, encoding="utf-8")
    print(f"wrote {RESULTS.relative_to(ROOT)} and {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
