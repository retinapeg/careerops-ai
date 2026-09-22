"""Synthetic checks for grounded purple synthesis, retry and exact-version exports."""
from copy import deepcopy
import json

import pytest
from docx import Document

from careerops import application_pack as packs
from careerops import model_connections
from careerops.cv_execution import CVExecution
from careerops.store import Store


@pytest.fixture
def profile():
    return {"name": "Example Candidate", "location": "Example City",
            "contact": {"email": "test@example.invalid", "phone": "0000000000", "linkedin_url": "https://example.invalid/profile"}}


@pytest.fixture
def packet(profile):
    return packs.synthesis_packet(
        {"title": "Data Engineer", "company": "Example Company", "description": "Use Python.",
         "evaluation": {"blockers": ["Work permission needs confirmation."]}},
        {"id": 7, "cv_text": profile["name"] + "\n" + profile["contact"]["email"] + "\nBuilt Python reports.", "warnings": [], "analysis": {}},
        {"cv": {"cv_text": "Built Python reports.", "sections": []},
         "evidence": [{"id": "e1", "text": "Built Python reports.", "status": "verified"},
                      {"id": "e2", "text": "Explained results to customers.", "status": "verified"}]},
        [{"team": "red", "findings": []}, {"team": "blue", "findings": []}],
        [{"id": "r0:red:1", "category": "question", "text": "Confirm work permission.",
          "validation": {"status": "needs_verification"}}])


def proposal(packet):
    return {"summary": "The strongest relevant evidence is the Python work.",
            "decisions": [{"finding_id": finding["id"], "decision": "disagree", "reason": "Advisory disagreement only."}
                          for finding in packet["findings"]],
            "letter_paragraphs": [{"evidence_ids": [packet["evidence"][0]["id"]]}]}


def test_letter_uses_literal_evidence_and_disagreement_cannot_waive_questions(packet, profile):
    pack = packs.build_pack(proposal(packet), packet, profile, mocked=True)
    assert "Built Python reports." in pack["cover_letter"]["text"]
    assert "Example Company" in pack["cover_letter"]["text"]
    assert "Explained results" not in pack["cover_letter"]["text"]
    assert pack["material_id"] == 7 and pack["mocked"]
    assert "Confirm work permission." in pack["outstanding_questions"]
    assert "Work permission needs confirmation." in pack["outstanding_questions"]


@pytest.mark.parametrize("mutation", ["unknown_evidence", "duplicate_evidence", "missing_finding", "invented_finding", "free_prose"])
def test_purple_rejects_unanchored_output(packet, profile, mutation):
    value = proposal(packet)
    if mutation == "unknown_evidence":
        value["letter_paragraphs"][0]["evidence_ids"] = ["invented"]
    elif mutation == "duplicate_evidence":
        value["letter_paragraphs"].append({"evidence_ids": ["e1"]})
    elif mutation == "missing_finding":
        value["decisions"] = []
    elif mutation == "invented_finding":
        value["decisions"][0]["finding_id"] = "invented"
    else:
        value["letter_paragraphs"][0]["text"] = "Invented commercial achievement."
    with pytest.raises(ValueError):
        packs.build_pack(value, packet, profile)


def test_saved_letter_export_does_not_use_changed_profile_or_other_version(tmp_path, packet, profile):
    store = Store(tmp_path / "private.sqlite3")
    pack = packs.build_pack(proposal(packet), packet, profile, mocked=True)
    with store.connect() as db:
        run_id = db.execute("INSERT INTO cv_runs(job_id,idempotency_key,data) VALUES (?,?,?)",
                            (1, "synthetic-letter", json.dumps({"application_packs": {"7": pack}}))).lastrowid
    store.update_profile({"name": "Changed Candidate"})
    text = packs.export_application_pack(store, run_id, 7, "txt").read_text()
    docx = packs.export_application_pack(store, run_id, 7, "docx")
    assert "Example Candidate" in text and "Changed Candidate" not in text
    assert "Built Python reports." in "\n".join(paragraph.text for paragraph in Document(docx).paragraphs)
    with pytest.raises(KeyError):
        packs.export_application_pack(store, run_id, 8, "txt")
    with pytest.raises(ValueError):
        packs.export_application_pack(store, run_id, 7, "../../private")


def test_identity_and_contact_stay_out_of_provider_packet_but_remain_in_local_pack(packet, profile):
    serialized = json.dumps(packet)
    assert "candidate" not in packet
    for private in (profile["name"], profile["location"], *profile["contact"].values()):
        assert private not in serialized
    assert packet["cv"]["body"]["cv_text"] == "Built Python reports."
    pack = packs.build_pack(proposal(packet), packet, profile)
    assert profile["name"] in pack["cover_letter"]["text"]
    assert profile["contact"]["email"] in pack["candidate"]["contact_text"]


def test_real_controller_calls_purple_and_reuses_completed_reviews_after_failure(tmp_path, monkeypatch):
    store = Store(tmp_path / "workflow.sqlite3")
    store.update_profile({"name": "Example Candidate", "contact": {"email": "candidate@example.invalid"},
        "skills": ["Python"], "work_authorisation": {"GB": True},
        "employment": [{"record_id": "work", "title": "Analyst", "employer": "Example Company",
                        "start": "2020", "end": "2022", "direct_evidence_ids": ["e1"]}],
        "evidence": [{"id": "e1", "text": "Built Python reports and explained the results to customers.", "status": "verified"}]})
    job = store.upsert_job({"title": "Python Engineer", "company": "Example Hiring Company",
        "description": "Build Python reports.", "url": "https://example.invalid/job", "location": "London, UK"})["job"]
    config = {role: {"provider": "codex_cli", "model": "test-model", "effort": "high"}
              for role in model_connections.ROLES}
    config.update(timeout_seconds=30, max_concurrent_runs=1)
    monkeypatch.setattr(model_connections, "status", lambda store: {"ready": True, "setup_blockers": [], "configuration": config})
    monkeypatch.setattr(CVExecution, "_base", lambda self: {"id": 1, "filename": "synthetic.docx", "sha256": "synthetic"})
    monkeypatch.setattr(CVExecution, "_check_exports", lambda *args: None)
    calls, payloads, fail = [], [], {"purple": True}

    def execute(connection, payload, schema, role, **kwargs):
        calls.append(role)
        payloads.append(deepcopy(payload))
        if role == "purple":
            if fail["purple"]:
                raise model_connections.ConnectionError("Synthetic pre-dispatch failure.")
            response = proposal(payload)
        elif role == "generator":
            evidence = {record["id"]: record["text"] for record in payload["evidence"]}
            response = {"profile_statement_ids": [record["id"] for record in payload["allowed_profile_statements"][:2]],
                "skill_names": payload["allowed_skills"][:2], "direction_reason": "Synthetic role-specific evidence.",
                "sections": [{"section_id": section["section_id"], "claims": [
                    {"text": evidence[key], "evidence_ids": [key]} for key in section["evidence_ids"]]}
                    for section in payload["available_sections"]]}
        else:
            response = {"summary": "Synthetic independent review.", "findings": []}
        return {"response": response, "mocked": True, "provider": "codex_cli", "actual_model": "test-model", "tool_calls": 0}

    monkeypatch.setattr(model_connections, "execute", execute)
    runner = CVExecution(store, autostart=False)
    run = runner.start(job["id"])
    assert run["workflow_roles"] == ["generator", "red", "blue", "purple"]
    runner._run(run["id"])
    assert runner.get(run["id"])["status"] == "failed"
    assert calls == ["generator", "red", "blue", "purple"]
    assert payloads[1] == payloads[2]
    purple_packet = deepcopy(payloads[3])
    fail["purple"] = False
    store.update_profile({"name": "Changed after dispatch"})
    runner.retry(run["id"])
    runner._run(run["id"])
    result = runner.get(run["id"])
    assert result["status"] in {"ready", "needs_answer"}
    assert calls == ["generator", "red", "blue", "purple", "purple"]
    assert payloads[-1] == purple_packet
    assert len(store.get_job(job["id"])["materials"]) == 1
    pack = result["application_packs"][str(result["selected_material_id"])]
    assert "Example Candidate" in pack["cover_letter"]["text"]
    assert pack["mocked"] and not store.meta("cv_model_live_receipts")
    assert store.get_job(job["id"])["application"]["stage"] == "not_started"


def test_role_instructions_are_distinct_and_grounded():
    instructions = model_connections.ROLE_INSTRUCTIONS
    assert len({instructions[role] for role in ("red", "blue", "purple")}) == 3
    assert "unsupported" in instructions["red"]
    assert "strongest truthful" in instructions["blue"]
    assert "Synthesize" in instructions["purple"]


def test_empty_profile_stays_in_setup_even_with_base_and_models(tmp_path, monkeypatch):
    store = Store(tmp_path / "empty.sqlite3")
    job = store.upsert_job({"title": "Analyst", "company": "Example Company",
                           "url": "https://example.invalid/empty", "description": "Analyse data."})["job"]
    config = {role: {"provider": "codex_cli", "model": "test-model", "effort": "high"}
              for role in model_connections.ROLES}
    config.update(timeout_seconds=30, max_concurrent_runs=1)
    monkeypatch.setattr(model_connections, "status", lambda store: {"ready": True, "setup_blockers": [], "configuration": config})
    monkeypatch.setattr(CVExecution, "_base", lambda self: {"id": 1, "filename": "synthetic.docx", "sha256": "synthetic"})
    calls = []
    monkeypatch.setattr(model_connections, "execute", lambda *args, **kwargs: calls.append(args))
    runner = CVExecution(store, autostart=False)
    assert runner.workspace(job["id"])["evidence_ready"] is False
    run = runner.start(job["id"])
    runner._run(run["id"])
    assert runner.get(run["id"])["status"] == "needs_setup"
    assert "Add supported experience" in run["needs_attention"][0]
    assert not calls and not store.get_job(job["id"])["materials"]
