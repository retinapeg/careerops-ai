"""CV review controller checks use a disposable profile/database, never private CVs."""
from copy import deepcopy
import json

import pytest

from careerops.cv_review import generate_cv, start_review, review_packet, submit_review, revise, workspace, STRUCTURE, RUBRIC_VERSION
from careerops.store import Store


@pytest.fixture
def cv(tmp_path):
    store = Store(tmp_path / "careerops.sqlite3")
    store.update_profile({"name": "Candidate Example", "contact": {"email": "synthetic@example.test"},
        "skills": ["Python"], "employment": [], "projects": [], "research": [],
        "qualifications": [{"completed": True, "evidence_ids": ["bank:degree"]}],
        "evidence": [
            {"id": "a", "text": "Built Python data validation tools for numerical research.", "status": "DIRECT"},
            {"id": "b", "text": "Explained quantitative findings to research collaborators.", "status": "TRANSFERABLE"},
            {"id": "c", "text": "Investigated data quality discrepancies and tested corrections.", "status": "CURRENT_PROJECT"},
            {"id": "verify", "text": "Led a team of 200 commercial engineers using Swift.", "status": "VERIFY"},
            {"id": "private", "text": "Private sentinel", "status": "DIRECT", "sensitive": True},
            {"id": "disabled", "text": "Restricted sentinel", "status": "DIRECT", "automatic_use_allowed": False},
            {"id": "bank:degree", "text": "Example University — BSc Statistics", "status": "DIRECT"}]})
    job = store.upsert_job({"title": "Python Research Engineer", "company": "Synthetic Research",
        "url": "https://example.test/cv-role", "location": "London", "country": "GB",
        "description": "Python research engineer in London. Analyse data quality and communicate findings. Swift is required."})["job"]
    before = deepcopy(store.get_job(job["id"])["application"])
    flow = generate_cv(store, job["id"])
    return store, job["id"], before, flow


def response(store, review_id, team, findings=None):
    packet = review_packet(store, review_id, team)
    value = packet["response_template"]
    value.update(reviewer=f"Synthetic {team} reviewer", findings=findings or [])
    return value


def finding(material, operation="move_paragraph", **patch):
    section = next(i for i, s in enumerate(material["sections"]) if s["heading"] == "Relevant strengths")
    value = {"cv_passage": material["sections"][section]["paragraphs"][0], "advert_requirement": "Python",
             "evidence_ids": material["sections"][section]["evidence_ids"], "severity": "medium",
             "recommended_action": "Prioritise the other verified example.",
             "action": {"type": operation, "section_index": section, "paragraph_index": 0, "to_index": 1}}
    value.update(patch)
    return value


def complete(store, job_id, flow, findings=None):
    review_id = flow["reviews"][-1]["id"]
    submit_review(store, review_id, "red", response(store, review_id, "red", findings), job_id=job_id)
    submit_review(store, review_id, "blue", response(store, review_id, "blue"), job_id=job_id)
    return review_id


def test_two_independent_reviews_create_version_diff_and_preserve_facts_and_application(cv):
    store, job_id, application, flow = cv
    original = flow["versions"][-1]
    review_id = flow["reviews"][-1]["id"]
    packet = review_packet(store, review_id, "red", job_id=job_id)
    assert {e["id"] for e in packet["evidence"]} == {"a", "b", "c", "bank:degree"}
    assert "Private sentinel" not in json.dumps(packet)
    assert "manual" in packet["notice"].lower()
    assert flow["reviewer_mode"] == "manual_exchange"
    with pytest.raises(ValueError, match="red review"):
        review_packet(store, review_id, "blue")
    review_id = complete(store, job_id, flow, [finding(original)])
    updated = revise(store, review_id, ["red:1"], job_id=job_id)
    final = updated["versions"][-1]
    assert final["id"] != original["id"] and final["parent_id"] == original["id"]
    assert final["version"] == original["version"] + 1
    assert final["cv_text"] != original["cv_text"]
    assert {d["type"] for d in final["diff"]} >= {"insert", "delete"}
    assert store.material(original["id"])["cv_text"] == original["cv_text"]
    assert final["analysis"]["evidence_match"] == original["analysis"]["evidence_match"]
    assert any("Swift remains a genuine" in g for g in final["analysis"]["evidence_match"]["gaps"])
    assert "heuristic" in final["analysis"]["ats_compatibility"]["method"]
    assert store.get_job(job_id)["application"] == application
    assert updated["reviews"][-1]["decisions"][0]["status"] == "accepted"
    assert revise(store, review_id, ["red:1"])["latest_material_id"] == final["id"]
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM reviews WHERE cache_key LIKE 'cv-review-history:%'").fetchone()[0] == 4


def test_verify_fabricated_rewrite_qualifications_and_missing_requirement_are_blocked(cv):
    store, job_id, _, flow = cv
    material = flow["versions"][-1]
    s = next(i for i, section in enumerate(material["sections"]) if section["heading"] == "Relevant strengths")
    education = next(i for i, section in enumerate(material["sections"]) if section["heading"] == "Education")
    bad = [
        finding(material, "add_evidence", cv_passage="", evidence_ids=["verify"], action={"type": "add_evidence", "section_index": s, "evidence_ids": ["verify"]}),
        finding(material, "replace_with_evidence", action={"type": "replace_with_evidence", "section_index": s, "paragraph_index": 0, "evidence_ids": ["a"], "text": "I am a senior engineering director."}),
        finding(material, "remove_paragraph", cv_passage=material["sections"][education]["paragraphs"][0], action={"type": "remove_paragraph", "section_index": education, "paragraph_index": 0}),
        finding(material, advert_requirement="Requires a PhD and 20 years of production leadership"),
        finding(material, "suggest_rewrite", action={"type": "suggest_rewrite", "text": "Led a large commercial team."}),
    ]
    review_id = complete(store, job_id, flow, bad)
    updated = revise(store, review_id, [f"red:{i}" for i in range(1, 6)])
    assert all(d["status"] == "blocked" for d in updated["reviews"][-1]["decisions"])
    assert updated["versions"][-1]["cv_text"] == material["cv_text"]
    assert "commercial team" not in updated["versions"][-1]["cv_text"]


def test_review_identity_stale_evidence_cross_job_and_response_replay_guards(cv):
    store, job_id, _, flow = cv
    review_id, material_id = flow["reviews"][-1]["id"], flow["latest_material_id"]
    red = response(store, review_id, "red")
    with pytest.raises(ValueError, match="different job"):
        start_review(store, material_id, job_id=job_id + 1)
    with pytest.raises(ValueError, match="different job"):
        submit_review(store, review_id, "red", red, job_id=job_id + 1)
    with pytest.raises(ValueError, match="identity"):
        submit_review(store, review_id, "red", dict(red, snapshot="wrong"))
    submit_review(store, review_id, "red", red)
    assert submit_review(store, review_id, "red", red)["reviews"][-1]["status"] == "awaiting_blue"
    with pytest.raises(ValueError, match="already recorded"):
        submit_review(store, review_id, "red", dict(red, reviewer="changed"))
    blue = response(store, review_id, "blue")
    profile = store.profile()
    profile["evidence"][0]["text"] += " Changed authoritative source."
    store.update_profile(profile)
    with pytest.raises(ValueError, match="changed"):
        submit_review(store, review_id, "blue", blue)


def test_auto_limit_explicit_review_again_and_reload_keep_versions(cv):
    store, job_id, _, flow = cv
    for _ in range(2):
        review_id = complete(store, job_id, flow)
        flow = revise(store, review_id, [])
        if len(flow["reviews"]) < 2:
            flow = start_review(store, flow["latest_material_id"])
    with pytest.raises(ValueError, match="limit"):
        start_review(store, flow["latest_material_id"])
    next_flow = start_review(store, flow["latest_material_id"], explicit=True)
    assert next_flow["reviews"][-1]["round"] == 3
    assert len(next_flow["versions"]) == 3
    reopened = Store(store.path)
    assert workspace(reopened, job_id)["reviews"] == next_flow["reviews"]


def test_old_stored_analysis_is_reassessed_from_frozen_run_without_writes(cv):
    store, job_id, _, flow = cv
    frozen_job = {"id": job_id, "title": "Python Developer", "description": "Python", "requirements": [],
                  "evaluation": {"conditions": [{"name": "Mandatory qualification", "blocking": True,
                      "status": "UNKNOWN", "evidence": "Graduation must be checked against the original award."}]}}
    snapshot = {"job": frozen_job, "profile": deepcopy(store.profile())}
    historical = deepcopy(flow["versions"][-1])
    historical.update(cv_run_id=7, analysis={"rubric_version": "cv-evidence-review-v2", "evidence_match": {
        "score": 100, "status": "assessed", "label": "Verified evidence fit", "coverage": {"assessed": 4, "total": 4}}})
    saved = store.save_material(job_id, "historical-run-material", historical)
    with store.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS cv_runs (id INTEGER PRIMARY KEY, job_id INTEGER, idempotency_key TEXT NOT NULL UNIQUE, data TEXT)")
        db.execute("INSERT INTO cv_runs(id,job_id,idempotency_key,data) VALUES (7,?,?,?)", (job_id, "frozen-analysis-test", json.dumps({"snapshot": snapshot})))
        before_material = db.execute("SELECT data FROM materials WHERE id=?", (saved["id"],)).fetchone()[0]
        before_run = db.execute("SELECT data FROM cv_runs WHERE id=7").fetchone()[0]
    # A later profile edit must not replace the evidence frozen for this version.
    store.update_profile(dict(store.profile(), evidence=[], skills=[]))
    shown = next(v for v in workspace(store, job_id)["versions"] if v["id"] == saved["id"])["analysis"]
    assert shown["rubric_version"] == RUBRIC_VERSION
    assert shown["evidence_match"]["score"] is None and shown["evidence_match"]["status"] == "incomplete"
    assert shown["evidence_match"]["advanced"]["partial_score"] == 100
    assert shown["evidence_match"]["coverage"]["assessed"] == 1 and shown["evidence_match"]["coverage"]["total"] == 2
    assert shown["reassessment"]["source"] == "frozen_cv_run"
    with store.connect() as db:
        assert db.execute("SELECT data FROM materials WHERE id=?", (saved["id"],)).fetchone()[0] == before_material
        assert db.execute("SELECT data FROM cv_runs WHERE id=7").fetchone()[0] == before_run


def test_old_analysis_without_snapshot_needs_reassessment_and_current_analysis_is_reused(cv, monkeypatch):
    store, job_id, _, flow = cv
    original = deepcopy(flow["versions"][-1])
    old = deepcopy(original)
    old["analysis"]["rubric_version"] = "cv-evidence-review-v2"
    historical = store.save_material(job_id, "legacy-old-analysis", old)
    current = store.save_material(job_id, "current-analysis", original)
    shown = {v["id"]: v for v in workspace(store, job_id)["versions"]}
    assert shown[historical["id"]]["analysis"]["evidence_match"]["score"] is None
    assert shown[historical["id"]]["analysis"]["evidence_match"]["status"] == "needs_review"
    assert shown[historical["id"]]["analysis"]["advanced"]["historical_analysis"] == old["analysis"]
    from careerops import cv_review
    monkeypatch.setattr(cv_review, "analysis", lambda *_: pytest.fail("Current stored analysis should be reused."))
    assert cv_review._version_analysis(store, store.get_job(job_id), store.profile(), current) == original["analysis"]


def test_literal_additions_and_blue_challenge_do_not_promote_facts(cv):
    store, job_id, _, flow = cv
    material = flow["versions"][-1]
    add = finding(material, cv_passage="", action={"type": "add_evidence", "section_index": 0, "evidence_ids": ["b"]})
    review_id = flow["reviews"][-1]["id"]
    submit_review(store, review_id, "red", response(store, review_id, "red", [add]))
    blue = response(store, review_id, "blue")
    blue["challenges"] = [{"finding_id": "red:1", "reason": "The transferable evidence is legitimate.", "evidence_ids": ["b"]}]
    submit_review(store, review_id, "blue", blue)
    result = revise(store, review_id, ["red:1"])
    assert result["reviews"][-1]["responses"]["blue"]["challenges"][0]["status"] == "reviewer_opinion_not_evidence"
    assert result["versions"][-1]["validation"]["status"] == "passed"
    assert store.profile()["evidence"][1]["status"] == "TRANSFERABLE"


def test_multiple_moves_in_same_section_cannot_be_falsely_marked_applied(cv):
    store, job_id, _, flow = cv
    material = flow["versions"][-1]
    first = finding(material)
    second = finding(material, cv_passage=material["sections"][0]["paragraphs"][1],
                     action={"type": "move_paragraph", "section_index": 0, "paragraph_index": 1, "to_index": 0})
    review_id = complete(store, job_id, flow, [first, second])
    result = revise(store, review_id, ["red:1", "red:2"])
    assert [d["status"] for d in result["reviews"][-1]["decisions"]] == ["accepted", "blocked"]


def test_domain_and_unassessed_mandatory_requirements_survive_rewriting(cv):
    store, job_id, _, _ = cv
    source = store.get_job(job_id)
    source["description"] += " Index methodology experience is required. Must hold a fictional specialist licence."
    store.upsert_job(source)
    flow = generate_cv(store, job_id)
    before = flow["versions"][-1]["analysis"]["evidence_match"]
    assert any("index methodology remains a genuine" in gap for gap in before["gaps"])
    assert any("fictional specialist licence" in gap for gap in before["gaps"])
    assert before["unassessed_requirements"] >= 1 and before["score"] is None and before["status"] == "incomplete"
    review_id = complete(store, job_id, flow, [finding(flow["versions"][-1])])
    after = revise(store, review_id, ["red:1"])["versions"][-1]["analysis"]["evidence_match"]
    assert before == after


@pytest.mark.parametrize("patch", [{"findings": "bad"}, {"findings": [{}]}, {"reviewer": ""}, {"challenges": [{}]}])
def test_untrusted_review_structure_is_validated(cv, patch):
    store, _, _, flow = cv
    review_id = flow["reviews"][-1]["id"]
    with pytest.raises(ValueError):
        submit_review(store, review_id, "red", {**response(store, review_id, "red"), **patch})


def test_unknown_json_types_return_validation_errors_not_internal_failures(cv):
    store, _, _, flow = cv
    review_id = flow["reviews"][-1]["id"]
    with pytest.raises(ValueError):
        review_packet(store, review_id, [])
    item = finding(flow["versions"][-1])
    item["action"]["type"] = []
    with pytest.raises(ValueError):
        submit_review(store, review_id, "red", response(store, review_id, "red", [item]))
    item["action"]["type"] = "flag"
    item["severity"] = []
    with pytest.raises(ValueError):
        submit_review(store, review_id, "red", response(store, review_id, "red", [item]))


def test_omitted_evidence_must_belong_to_the_same_authoritative_employment(cv):
    store, job_id, _, _ = cv
    profile = store.profile()
    profile["evidence"] += [
        {"id": "bank:employment1", "text": "Maintained archived records for Example Office.", "status": "DIRECT"},
        {"id": "bank:employment2", "text": "Prepared shift handover records for Example Office.", "status": "INDIRECT"},
        {"id": "bank:other", "text": "Built research experiments at Other Employer.", "status": "DIRECT"}]
    profile["employment"] = [{"record_id": "office", "title": "Records Assistant", "employer": "Example Office",
        "start": "2020", "end": "2021", "evidence_ids": ["employment1", "employment2"]}]
    store.update_profile(profile)
    flow = generate_cv(store, job_id)
    material = flow["versions"][-1]
    s = next(i for i, section in enumerate(material["sections"]) if section.get("profile_ref") == "office")
    # The selector excludes indirect evidence; the reviewer can restore it only
    # as its literal source claim with proven employer attribution.
    assert "bank:employment2" not in material["sections"][s]["evidence_ids"]
    def add(eid):
        return finding(material, cv_passage="", advert_requirement=STRUCTURE, evidence_ids=[eid],
                       action={"type": "add_evidence", "section_index": s, "evidence_ids": [eid]})
    review_id = complete(store, job_id, flow, [add("bank:employment2"), add("bank:other")])
    result = revise(store, review_id, ["red:1", "red:2"])
    assert [d["status"] for d in result["reviews"][-1]["decisions"]] == ["accepted", "blocked"]
    assert "Prepared shift handover records" in result["versions"][-1]["cv_text"]


@pytest.mark.parametrize("raw_profile", [False, True])
def test_review_again_cannot_certify_stale_employment_headers(cv, raw_profile):
    store, job_id, _, _ = cv
    profile = store.profile()
    profile["employment"] = [{"record_id": "office", "title": "Clerk", "employer": "Example",
                              "start": "2020", "end": "2021"}]
    if raw_profile:
        profile.pop("version", None)
        store.put_meta("profile", profile)
    else:
        store.update_profile(profile)
    flow = generate_cv(store, job_id)
    old_id = flow["latest_material_id"]
    profile = store.profile()
    profile["employment"][0].update(end="2024", title="Corrected title")
    if raw_profile:
        store.put_meta("profile", profile)
    else:
        store.update_profile(profile)
    with pytest.raises(ValueError, match="older candidate facts"):
        start_review(store, old_id, explicit=True, job_id=job_id)
    assert "2020 – 2021" in store.material(old_id)["cv_text"]
    fresh = generate_cv(store, job_id)
    assert fresh["latest_material_id"] != old_id
    assert "2020 – 2024" in fresh["versions"][-1]["cv_text"]
    assert "Corrected title" in fresh["versions"][-1]["cv_text"]


def test_generation_rules_invalidate_previous_cv_cache(cv, monkeypatch):
    from careerops import cv_review, materials
    store, job_id, _, flow = cv
    old_id = flow["latest_material_id"]
    review_id = flow["reviews"][-1]["id"]
    monkeypatch.setattr(materials, "PROMPT_VERSION", "synthetic-future-generation-rules")
    monkeypatch.setattr(cv_review, "PROMPT_VERSION", "synthetic-future-generation-rules")
    with pytest.raises(ValueError, match="generation rules"):
        start_review(store, old_id, explicit=True, job_id=job_id)
    with pytest.raises(ValueError, match="generation rules"):
        review_packet(store, review_id, "red", job_id=job_id)
    fresh = generate_cv(store, job_id)
    assert fresh["latest_material_id"] != old_id
    assert fresh["versions"][-1]["prompt_version"] == "synthetic-future-generation-rules"


def test_generated_cv_has_black_headings_and_no_title_border(cv, monkeypatch, tmp_path):
    import docx
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from careerops.materials import render_docx
    original_document = docx.Document
    def template_with_border():
        document = original_document()
        document.styles["Title"].element.get_or_add_pPr().append(OxmlElement("w:pBdr"))
        return document
    monkeypatch.setattr(docx, "Document", template_with_border)
    target = tmp_path / "generated-cv.docx"
    render_docx(cv[3]["versions"][-1], target)
    result = original_document(target)
    assert result.styles["Title"].element.pPr.find(qn("w:pBdr")) is None
    assert str(result.styles["Heading 1"].font.color.rgb) == "000000"
    assert str(result.styles["Heading 2"].font.color.rgb) == "000000"


def test_optional_and_negated_requirements_are_not_mandatory_gaps(cv):
    from careerops.cv_review import analysis
    store, job_id, _, flow = cv
    role = store.get_job(job_id)
    optional = ["Useful, but not essential.", "Previous experience in finance is not required.",
                "A licence is optional.", "Domain experience is nice-to-have.",
                "A qualification is not necessarily required."]
    role["description"] = "Python role. " + " ".join(optional) + " Index methodology is required. A specialist licence is required; finance experience is optional."
    # Even stale upstream flags cannot override a literal optional source quote.
    role["requirements"] = [{"mandatory": True, "evidence": quote} for quote in optional]
    role["evaluation"] = {}
    result = analysis(role, store.profile(), flow["versions"][-1])["evidence_match"]
    unassessed = [r["advert_quote"] for r in result["requirements"] if r["status"] == "unassessed"]
    assert unassessed == ["A specialist licence is required;"]
    assert any("index methodology remains a genuine" in gap for gap in result["gaps"])
    assert not any(quote in gap for quote in optional for gap in result["gaps"])
