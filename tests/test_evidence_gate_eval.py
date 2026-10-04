"""The evidence gate evaluation is reproducible and its documented failures are current."""
import hashlib
from copy import deepcopy

import pytest

from careerops.cv_document import apply_findings, validate_document, validate_review
from evals import evidence_gates as eg


@pytest.fixture(scope="module")
def results():
    return eg.run()


def test_frozen_case_file_matches_its_recorded_sha256():
    recorded = eg.SUITE_SHA.read_text(encoding="utf-8").split()
    assert recorded[1] == eg.SUITE.name
    assert hashlib.sha256(eg.SUITE.read_bytes()).hexdigest() == recorded[0]


def test_runner_refuses_a_case_file_that_does_not_match_its_digest(tmp_path, monkeypatch):
    altered = tmp_path / eg.SUITE.name
    altered.write_bytes(eg.SUITE.read_bytes().replace(b"should_reject", b"should_accept", 1))
    monkeypatch.setattr(eg, "SUITE", altered)
    with pytest.raises(SystemExit):
        eg.verify_suite()


def test_rerunning_the_evaluation_reproduces_the_committed_results_and_report(results):
    # Byte comparison, so no number in either committed file can be edited by hand.
    assert eg.encode_results(results) == eg.RESULTS.read_text(encoding="utf-8")
    assert eg.render_report(results) == eg.REPORT.read_text(encoding="utf-8")


def test_results_cover_every_frozen_case_or_record_an_exclusion(results):
    gates = results["gates"]
    counted = (len(gates["G1"]["cases"]) + len(gates["G2_hand"]["cases"]) + len(gates["G3"]["cases"])
               + len(gates["G4"]["evidence_sets"]) + len(gates["G5"]["cases"]))
    assert counted + len(results["exclusions"]) == results["suite"]["cases"]
    assert "not a random sample of real claims" in results["caveat"]


def test_each_known_failure_still_behaves_as_documented(results):
    # If a gate is fixed, update evals/cases/known_failures_v1.json and regenerate the report.
    rows = results["known_failures"]["cases"]
    assert rows, "the known-failure list is empty"
    changed = [(k["id"], k["documented_today"], k["observed_today"]) for k in rows if not k["matches_documentation"]]
    assert changed == []
    assert all(k["failing_today"] for k in rows)


def test_wilson_interval_matches_known_values():
    assert eg.wilson(8, 8) == [0.6756, 1.0]
    assert eg.wilson(0, 14) == [0.0, 0.2153]
    assert eg.wilson(0, 0) is None


def _finding(section_index, paragraph_index, action_type, advert_requirement, cv_passage="", evidence_ids=(), text=""):
    return {"category": "document_problem", "cv_passage": cv_passage, "advert_requirement": advert_requirement,
            "evidence_ids": list(evidence_ids), "severity": "medium", "recommended_action": "Scripted reviewer action.",
            "action": {"type": action_type, "section_index": section_index, "paragraph_index": paragraph_index,
                       "to_index": -1, "evidence_ids": list(evidence_ids), "text": text}}


def test_reviewer_output_validation_plumbing():
    """Reviewer-output validation plumbing, driven by a scripted reviewer.

    A scripted reviewer catches 100% of planted errors by construction: its
    findings are written into this test. This therefore tests the plumbing that
    validates and applies reviewer output, not review quality, and says nothing
    about any model's ability to find errors.
    """
    fixture = eg.fixtures_by_id()["support_to_analytics"]
    job, profile = fixture.job, fixture.profile
    material = deepcopy(fixture.document["material"])
    index = {s["section_id"]: n for n, s in enumerate(material["sections"])}
    rep, desk = index["employment:rep"], index["employment:desk"]
    quote = "SQL and Power BI"
    assert quote in job["description"]
    rewrite = "Led a data transformation programme across all 42 stores."
    response = {"summary": "Scripted review.", "findings": [
        # 1. Unanchored: the advert quote does not occur in the advert.
        _finding(rep, 0, "remove_paragraph", "Ten years of Kubernetes leadership", material["sections"][rep]["paragraphs"][0]),
        # 2. Free wording: an anchored rewrite that invents a claim.
        _finding(rep, 0, "suggest_rewrite", quote, material["sections"][rep]["paragraphs"][0], text=rewrite),
        # 3. Wrong employer: evidence from the housing trust added under the retailer.
        _finding(rep, -1, "add_evidence", quote, "", ["bank:ops1"]),
        # 4. Control: removing a supported paragraph is a valid structural edit.
        _finding(desk, 2, "remove_paragraph", quote, material["sections"][desk]["paragraphs"][2]),
    ]}
    review = validate_review(response, job, profile, material, "red")
    unanchored, free, wrong_employer, control = review["findings"]

    assert unanchored["category"] == "question" and unanchored["validation"]["status"] == "needs_verification"
    assert free["category"] == "document_problem" and free["validation"]["status"] == "needs_verification"
    assert wrong_employer["validation"] == {"status": "needs_verification", "reason":
        "Cannot attribute evidence to a different employment/project section. Add a truthful strength instead."}
    assert control["validation"]["status"] == "eligible"

    # Select every finding explicitly, so each one reaches the controller's checks rather than "Not selected".
    result, decisions = apply_findings(material, review["findings"], profile, job,
                                       accepted_ids=[f["id"] for f in review["findings"]])
    by_id = {d["finding_id"]: d for d in decisions}
    for blocked in (unanchored, free, wrong_employer):
        assert by_id[blocked["id"]] == {"finding_id": blocked["id"], "status": "blocked",
                                        "reason": "This proposal needs factual confirmation."}
    assert by_id[control["id"]]["status"] == "accepted"

    assert rewrite not in result["cv_text"]
    assert "bank:ops1" not in result["sections"][rep]["evidence_ids"]
    assert result["sections"][rep] == material["sections"][rep]
    assert len(result["sections"][desk]["paragraphs"]) == len(material["sections"][desk]["paragraphs"]) - 1
    assert validate_document(result, profile, job)["status"] == "passed"
