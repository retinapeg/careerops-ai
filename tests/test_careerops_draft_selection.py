from copy import deepcopy

import pytest

from careerops.draft_selection import choose_evidence


JOB = {"title": "AI Solutions Engineer", "description": "Python SQL LLM workflows, API integration, client communication and automation."}


def evidence(identifier, text, status="DIRECT", **extra):
    return {"id": identifier, "text": text, "status": status, "source": "Synthetic candidate evidence", **extra}


def test_specific_systems_and_client_work_beat_career_judgments_and_tool_fragments():
    records = [
        evidence("bank:profile:013", "Strong fit for roles combining technical systems with customer/client communication.", "TRANSFERABLE"),
        evidence("bank:profile:017", "Strong fit for technical/product support, implementation, onboarding, customer-success, and API-enabled workflow roles.", "TRANSFERABLE"),
        evidence("bank:marketdata:002", "Uses pandas."),
        evidence("bank:technical:003", "pandas."),
        evidence("bank:api_boundary:003", "Codex CLI model integration is live and working."),
        evidence("bank:application:032", "Kept model-generated edits behind deterministic local validation rather than granting unrestricted document-editing control.", "CURRENT_PROJECT"),
        evidence("bank:api:008", "Normalised source-specific JSON into a source-independent internal Job model.", "CURRENT_PROJECT"),
        evidence("bank:client:014", "Converted technical analysis into practical business recommendations."),
        evidence("bank:client:012", "Communicated findings to non-technical stakeholders."),
        evidence("bank:university:001", "Developed Python numerical simulations to investigate synthetic sensor measurements."),
    ]
    chosen = choose_evidence(records, JOB, 5)
    assert len(chosen) == 5
    assert all("Strong fit" not in item["text"] for item in chosen)
    assert all(item["text"] not in {"Uses pandas.", "pandas."} for item in chosen)
    assert any("stakeholders" in item["text"] or "recommendations" in item["text"] for item in chosen)
    assert any("deterministic" in item["text"] for item in chosen)
    assert all("is live and working" not in item["text"] for item in chosen)


def test_single_employer_subset_gets_substantive_bullets_including_healthcare():
    records = [
        evidence("bank:laboratory:001", "Worked in laboratory sample reception.", source_employer="Example Laboratory"),
        evidence("bank:laboratory:002", "Maintained specimen identifiers.", source_employer="Example Laboratory"),
        evidence("bank:laboratory:005", "Maintained record integrity.", source_employer="Example Laboratory"),
        evidence("bank:laboratory:010", "Escalated discrepancies and workflow issues.", source_employer="Example Laboratory"),
        evidence("bank:laboratory:014", "Data administration.", "TRANSFERABLE", source_employer="Example Laboratory"),
    ]
    chosen = choose_evidence(records, JOB, 3)
    assert len(chosen) == 3
    assert all(item["status"] == "DIRECT" for item in chosen)
    assert any("Escalated" in item["text"] for item in chosen)
    assert all(item["source_employer"] == "Example Laboratory" for item in chosen)


def test_preserves_every_claim_and_provenance_without_mutating_source():
    record = evidence("bank:api:001", "Integrated an external API into a Python application.", source_employer="Personal project", detail={"path": "private source", "line": 7})
    original = deepcopy(record)
    selected = choose_evidence([record], JOB, 3)
    assert selected == [original]
    selected[0]["detail"]["line"] = 99
    assert record == original


def test_excludes_unresolved_unsupported_and_explicitly_unapproved_records():
    records = [
        evidence("a", "Designed a Python API for customer workflows.", "VERIFY"),
        evidence("b", "Designed a Python API for customer workflows.", "unresolved"),
        evidence("c", "Designed a Python API for customer workflows.", automatic_use_allowed=False),
        evidence("d", "Onboarding relevance without claiming direct onboarding.", "TRANSFERABLE"),
        evidence("e", "Implemented validation of structured language-model outputs.", "CURRENT_PROJECT"),
    ]
    assert [item["id"] for item in choose_evidence(records, JOB, 5)] == ["e"]


def test_at_most_one_summary_and_no_duplicate_content():
    records = [
        evidence("bank:profile:001", "Practical experience integrating external APIs into Python applications."),
        evidence("bank:profile:002", "Experience working with authenticated third-party services."),
        evidence("bank:api:003", "Built authenticated API requests with validated JSON responses."),
        evidence("bank:api:004", "Built authenticated API requests with validated JSON responses!"),
        evidence("bank:client:001", "Investigated customer product issues and communicated findings."),
    ]
    chosen = choose_evidence(records, JOB, 5)
    assert sum(":profile:" in item["id"] for item in chosen) <= 1
    assert sum("Built authenticated" in item["text"] for item in chosen) == 1


def test_multiple_api_families_do_not_crowd_out_commercial_experience():
    records = [evidence(f"bank:{family}:{index}", f"Implemented Python API workflow number {index} with JSON data validation.", "CURRENT_PROJECT", source_employer="Example Integration Project") for index, family in enumerate(["application", "api", "mail_api", "api_skill"])]
    records += [evidence("bank:client:012", "Communicated findings to non-technical stakeholders.")]
    chosen = choose_evidence(records, JOB, 3)
    assert any(item["id"] == "bank:client:012" for item in chosen)


def test_parser_dictionary_shape_and_empty_inputs():
    records = [{"evidence_id": "bank:api:001", "text": "Integrated Python APIs with structured response validation.",
                "evidence_level": "CURRENT_PROJECT", "automatic_use_allowed": True, "source_kind": "candidate_evidence_master"}]
    assert choose_evidence(records, JOB, 1) == records
    assert choose_evidence(records, JOB, 0) == []
    assert choose_evidence([], JOB, 5) == []
    with pytest.raises(ValueError):
        choose_evidence(records, JOB, -1)


def test_role_relevance_changes_selection_without_rewriting_claims():
    statistics = evidence("bank:university:001", "Developed numerical simulations of simulated process behaviour and mathematical models.")
    support = evidence("bank:client:002", "Investigated customer product issues and communicated findings to stakeholders.")
    assert choose_evidence([statistics, support], {"title": "Statistical Researcher", "description": "numerical statistics simulations"}, 1) == [statistics]
    assert choose_evidence([statistics, support], {"title": "Client Solutions", "description": "Customer support and stakeholder communication"}, 1) == [support]
