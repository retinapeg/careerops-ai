"""Build the reference documents used by the evidence gate evaluation.

For each fictional profile in profiles.json this runs the real document
builder on a trivial proposal that copies every admitted evidence statement
verbatim, one claim per statement, in the order the section offers them. It
then writes reference_documents.json so that case writers can describe
alterations and claims by section and evidence identifiers without reading
the implementation.

Usage (from the repository root):

    python evals/fixtures/build_reference_documents.py          # rewrite the file
    python evals/fixtures/build_reference_documents.py --check  # compare only

Offline and deterministic apart from timestamps and the list of export formats,
which depends on the local LibreOffice installation; --check ignores both.
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from careerops.cv_document import build_document, generation_packet, validate_document  # noqa: E402
from careerops.store import digest  # noqa: E402

HERE = Path(__file__).resolve().parent
PROFILES = HERE / "profiles.json"
OUTPUT = HERE / "reference_documents.json"
# Timestamps, and the export formats, which depend on whether LibreOffice is installed.
VOLATILE = (("material", "created_at"), ("material", "validation", "checked_at"), ("material", "formats"))


def trivial_proposal(packet):
    """Every admitted statement, verbatim, one claim each, in bank order."""
    evidence = {r["id"]: r for r in packet["evidence"]}
    return {
        "profile_statement_ids": [s["id"] for s in packet["allowed_profile_statements"]][:2],
        "skill_names": list(packet["allowed_skills"]),
        "direction_reason": "Reference document: every admitted statement copied verbatim.",
        "sections": [{"section_id": s["section_id"],
                      "claims": [{"text": evidence[i]["text"], "evidence_ids": [i]} for i in s["evidence_ids"]]}
                     for s in packet["available_sections"]],
    }


def held_back_reason(record):
    """Describe, from visible fields only, why a record is not admitted."""
    reasons = []
    if str(record.get("status", "")).upper() == "VERIFY":
        reasons.append("status VERIFY")
    if record.get("sensitive"):
        reasons.append("sensitive flag set")
    if record.get("automatic_use_allowed") is False:
        reasons.append("automatic_use_allowed is false")
    return "; ".join(reasons) or "not admitted"


def reference_entry(item):
    job, profile = item["job"], item["profile"]
    before = deepcopy(profile)
    packet = generation_packet(job, profile)
    proposal = trivial_proposal(packet)
    material = build_document(job, profile, proposal)
    if profile != before:
        raise SystemExit(f"{item['profile_id']}: the builder modified the profile")
    if material["blocked_proposals"] or material["requires_human_review"]:
        raise SystemExit(f"{item['profile_id']}: the verbatim proposal was not accepted in full: {material['blocked_proposals']}")
    if validate_document(material, profile, job)["status"] != "passed":
        raise SystemExit(f"{item['profile_id']}: the reference document did not validate")
    admitted = {r["id"]: r for r in packet["evidence"]}
    bank = {s["section_id"]: s for s in packet["available_sections"]}
    sections = []
    for section in material["sections"]:
        summary = {k: section[k] for k in ("section_id", "heading") if k in section}
        for key in ("subheading", "category", "profile_ref", "profile_statement_ids", "skill_names"):
            if key in section:
                summary[key] = section[key]
        summary["evidence_ids"] = section["evidence_ids"]
        if section["section_id"] in bank:
            summary["section_evidence_ids"] = bank[section["section_id"]]["evidence_ids"]
        summary["paragraphs"] = section["paragraphs"]
        sections.append(summary)
    skill_section = next((s for s in material["sections"] if s["section_id"] == "skills"), {"skill_names": []})
    listed = [s if isinstance(s, str) else s.get("name", "") for s in profile.get("skills", [])]
    return {
        "profile_id": item["profile_id"],
        "job": job,
        "protected_profile_hash": digest(profile),
        "admitted_evidence": [{"id": r["id"], "status": r["status"], "text": r["text"]} for r in packet["evidence"]],
        "held_back_evidence": [{"id": r["id"], "status": r.get("status"), "text": r["text"], "reason": held_back_reason(r)}
                               for r in profile["evidence"] if r["id"] not in admitted],
        "skills_in_document": skill_section["skill_names"],
        "listed_skills_not_in_document": [s for s in listed if s not in skill_section["skill_names"]],
        "sections": sections,
        "cv_text": material["cv_text"],
        "proposal": proposal,
        "material": material,
    }


def build():
    data = json.loads(PROFILES.read_text(encoding="utf-8"))
    return {
        "description": "Reference documents produced by build_document from a trivial all-verbatim proposal for each "
                       "profile in profiles.json. Regenerate with build_reference_documents.py; do not edit by hand.",
        "documents": [reference_entry(item) for item in data["profiles"]],
    }


def _without_volatile(value):
    value = deepcopy(value)
    for document in value["documents"]:
        for path in VOLATILE:
            target = document
            for key in path[:-1]:
                target = target[key]
            target.pop(path[-1], None)
    return value


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def check():
    """Return True when the stored file matches a fresh build, timestamps aside."""
    stored = json.loads(OUTPUT.read_text(encoding="utf-8"))
    fresh = json.loads(encoded(build()))
    return _without_volatile(stored) == _without_volatile(fresh)


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        same = check()
        print("reference_documents.json matches a fresh build" if same else "reference_documents.json is out of date")
        raise SystemExit(0 if same else 1)
    OUTPUT.write_text(encoded(build()), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
