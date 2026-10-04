"""Document/controller and scoring acceptance using synthetic candidate records."""
from copy import deepcopy

import pytest

from careerops.cv_document import (generation_packet, build_document, validate_document, review_packet,
                                  validate_review, apply_findings, _compose, DEFAULT_POSITIONING,
                                  DOCUMENT_VERSION, OutdatedDocumentError)
from careerops.cv_review import analysis, requirement_clauses
from careerops.materials import validate_draft
from careerops.policy import default_profile, default_settings
from careerops.professional import assess_candidacy


@pytest.fixture
def document():
    profile = default_profile()
    profile.update(name="Candidate Example", contact={"email": "private@example.test", "phone": "07700900001"},
        education=[
            {"record_id": "example_certificate", "qualification": "Postgraduate Certificate in Data Science", "institution": "Example College", "automation": "AUTO"},
            {"record_id": "example_degree", "qualification": "Bachelor of Science in Economics", "institution": "Example University", "classification": "First Class Honours", "automation": "AUTO"}],
        qualifications=[{"completed": True, "evidence_ids": ["certificate"]}, {"completed": True, "evidence_ids": ["degree"]}],
        skills=["Python", "SQL"], evidence=[
            {"id": "bank:work1", "status": "DIRECT", "text": "Handled customer calls and communicated clearly under pressure."},
            {"id": "bank:work2", "status": "DIRECT", "text": "Investigated customer issues and explained practical solutions."},
            {"id": "bank:old", "status": "DIRECT", "text": "Maintained accurate office records using SQL."},
            {"id": "bank:project", "status": "CURRENT_PROJECT", "text": "Built Python API integrations and tested data validation in a personal project."},
            {"id": "bank:team", "status": "CURRENT_PROJECT", "text": "Contributed Python tests to a team project; another member implemented the interface."},
            {"id": "bank:research", "status": "DIRECT", "text": "Conducted numerical statistics research and analysed scientific data."},
            {"id": "bank:future", "status": "CURRENT_PROJECT", "text": "Will implement automated job submission in a future feature."},
            {"id": "bank:verify", "status": "VERIFY", "text": "Led 200 commercial software engineers."},
            {"id": "bank:private", "status": "DIRECT", "text": "Private sentinel", "sensitive": True},
            {"id": "bank:certificate", "status": "DIRECT", "text": "Example College — Postgraduate Certificate in Data Science"},
            {"id": "bank:degree", "status": "DIRECT", "text": "Example University — BSc Economics, First Class Honours"}],
        employment=[
            {"record_id": "old", "title": "Office Clerk", "employer": "Example Office", "start": "2020", "end": "2021", "evidence_ids": ["old"]},
            {"record_id": "recent", "title": "Customer Service Adviser", "employer": "Example Service", "start": "2022", "end": "2023", "evidence_ids": ["work1", "work2"]}],
        projects=[{"record_id": "project", "name": "Example AI Integration", "period": "2025", "evidence_ids": ["project", "future"]},
                  {"record_id": "team", "name": "Team Research Tool", "period": "2024", "team_project": True, "evidence_ids": ["team"]}],
        research=[{"record_id": "research", "name": "Statistics research", "period": "2019", "evidence_ids": ["research"]}])
    job = {"title": "AI Implementation Engineer", "company": "Synthetic AI", "url": "https://example.test/job",
           "description": "Essential Skills:\nPython\nCustomer communication\nExperience with specialist index methodology\nUseful, but not essential:\nFinance knowledge is not required.", "requirements": []}
    packet = generation_packet(job, profile, {"id": 3, "structured": {"sections": {"profile": [], "employment": []}}})
    evidence = {r["id"]: r for r in packet["evidence"]}
    proposal = {"profile_statement_ids": ["technical", "communication"], "skill_names": ["Python", "SQL"],
                "direction_reason": "Lead with implementation and customer problem-solving.",
                "sections": [{"section_id": section["section_id"], "claims": [{"text": evidence[i]["text"], "evidence_ids": [i]} for i in section["evidence_ids"][:2]]}
                             for section in reversed(packet["available_sections"])]}
    material = build_document(job, profile, proposal, base_cv={"id": 3})
    return job, profile, proposal, material


def test_coherent_document_preserves_identity_history_team_scope_and_qualifications(document):
    job, profile, _, material = document
    assert material["name"] == profile["name"] and material["positioning"] == DEFAULT_POSITIONING
    headings = [s["heading"] for s in material["sections"]]
    assert headings[:3] == ["Profile", "Skills", "Experience"]
    assert "Selected projects" in headings and "Education" in headings
    assert "Relevant strengths" not in material["cv_text"]
    assert headings.index("Customer Service Adviser | Example Service") < headings.index("Office Clerk | Example Office")
    assert "Team project · 2024" in material["cv_text"] and "another member implemented the interface" in material["cv_text"]
    assert "Will implement" not in material["cv_text"] and "200 commercial" not in material["cv_text"]
    assert "Postgraduate Certificate" in material["cv_text"] and "First Class Honours" in material["cv_text"]
    assert len(material["sections"][0]["paragraphs"]) == 1
    assert material["sections"][0]["profile_statement_ids"][0] == "targeted"
    assert material["sections"][0]["profile_statement_ids"][1].startswith("example:")
    assert "Combines " in material["sections"][0]["paragraphs"][0]
    assert validate_draft(material, profile)["status"] == "passed"
    assert material["analysis"]["evidence_match"]["score"] is None


def test_generator_packets_exclude_private_identity_contacts_and_unapproved_sources(document):
    job, profile, _, material = document
    import json
    for packet in (generation_packet(job, profile), review_packet(job, profile, material)):
        text = json.dumps(packet)
        for excluded in (profile["name"], "private@example.test", "07700900001", "Private sentinel", "200 commercial", "Will implement"):
            assert excluded not in text
    assert generation_packet(job, profile, {"structured": {"sections": {"skills": [], "experience": []}}})["base_cv"]["section_order"] == ["skills", "experience"]


def test_confirmed_award_dates_reach_both_packets_workspace_and_new_cv_without_promoting_unverified_dates(document, tmp_path):
    from careerops.cv_review import workspace
    from careerops.store import Store
    job, profile, proposal, old_material = document
    profile["education"] = [
        {"record_id": "example_certificate", "qualification": "Postgraduate Certificate in Data Science", "institution": "Example College",
         "automation": "AUTO", "award_date": "2023-06-01", "source_refs": ["user_confirmed:education"]},
        {"record_id": "example_degree", "qualification": "Bachelor of Science in Economics", "institution": "Example University",
         "automation": "AUTO", "award_date": "2020-07-01", "source_refs": ["user_confirmed:education"]},
        {"record_id": "unverified", "qualification": "Unverified course", "automation": "AUTO", "award_date": "2099-01-01", "award_date_verified": False},
        {"record_id": "source_only", "qualification": "Uploaded course", "automation": "VERIFY", "award_date": "2098-01-01"}]
    before = deepcopy(profile)
    material = build_document(job, profile, proposal)
    for packet in (generation_packet(job, profile), review_packet(job, profile, material)):
        facts = packet["protected_facts"]["confirmed_qualifications"]
        assert [f["award_date"] for f in facts] == ["2023-06-01", "2020-07-01"]
        assert all(f["source_refs"] == ["user_confirmed:education"] for f in facts)
    assert "Awarded 1 June 2023" in material["cv_text"] and "Awarded 1 July 2020" in material["cv_text"]
    assert "2099" not in material["cv_text"] and "2098" not in material["cv_text"]
    assert validate_document(material, profile, job)["status"] == "passed"
    historical = deepcopy(material)
    historical["sections"][-1]["paragraphs"] = old_material["sections"][-1]["paragraphs"]
    assert validate_document(_compose(historical), profile, job)["status"] == "passed"
    altered = deepcopy(material)
    altered["sections"][-1]["paragraphs"][0] = altered["sections"][-1]["paragraphs"][0].replace("2023", "2025")
    with pytest.raises(ValueError, match="qualifications"):
        validate_document(_compose(altered), profile, job)
    store = Store(tmp_path / "synthetic-awards.sqlite3")
    store.update_profile(profile)
    saved = store.upsert_job(dict(job, url="https://example.test/award-check"))["job"]
    assert workspace(store, saved["id"])["confirmed_qualifications"] == facts
    assert profile == before


@pytest.mark.parametrize("kind", ["name", "title", "date", "qualification", "attribution", "chronology"])
def test_protected_facts_cannot_be_changed_even_with_plausible_evidence_ids(document, kind):
    job, profile, _, material = document
    changed = deepcopy(material)
    work = [s for s in changed["sections"] if s.get("category") == "employment"]
    if kind == "name":
        changed["name"] = "Invented Name"
    elif kind == "title":
        work[0]["heading"] = "Director | Example Service"
    elif kind == "date":
        work[0]["subheading"] = "2010 – Present"
    elif kind == "qualification":
        changed["sections"][-1]["paragraphs"] = ["MSc Data Science"]
    elif kind == "attribution":
        work[0].update(paragraphs=[profile["evidence"][3]["text"]], evidence_ids=["bank:project"])
    else:
        a, b = [changed["sections"].index(s) for s in work]
        changed["sections"][a], changed["sections"][b] = changed["sections"][b], changed["sections"][a]
    _compose(changed)
    with pytest.raises(ValueError):
        validate_document(changed, profile, job)


def test_unproved_model_rewrite_remains_visible_but_never_enters_the_cv(document):
    job, profile, proposal, _ = document
    proposal["sections"][0]["claims"][0]["text"] = "Led a commercial organisation of 200 engineers."
    result = build_document(job, profile, proposal, base_cv={"id": 3, "conflicts": [{"field": "qualification", "message": "Source says doctorate; retained verified diploma."}]})
    assert "200 engineers" not in result["cv_text"]
    assert result["requires_human_review"] and result["blocked_proposals"]
    assert result["base_cv_conflicts"][0]["field"] == "qualification"
    assert result["validation"]["status"] == "passed"


@pytest.mark.parametrize("emphasis,paragraph_count", [("balanced", 3), ("concise", 2)])
def test_all_eight_selected_statements_and_evidence_survive_paragraph_grouping(document, emphasis, paragraph_count):
    job, profile, proposal, _ = document
    records = [{"id": f"bank:statement{i}", "status": "CURRENT_PROJECT", "text": f"Tested Python integration scenario {i} with recorded inputs and expected outputs."}
               for i in range(8)]
    profile["evidence"].extend(records)
    profile["projects"][0]["evidence_ids"] = [r["id"] for r in records]
    selected = next(s for s in proposal["sections"] if s["section_id"] == "projects:project")
    selected["claims"] = [{"text": r["text"], "evidence_ids": [r["id"]]} for r in records]
    material = build_document(job, profile, proposal, direction={"emphasis": emphasis})
    section = next(s for s in material["sections"] if s.get("section_id") == "projects:project")
    assert len(section["paragraphs"]) == paragraph_count
    assert " ".join(section["paragraphs"]) == " ".join(r["text"] for r in records)
    assert section["evidence_ids"] == [r["id"] for r in records]
    assert all(r["id"] in material["provenance"] for r in records)
    assert not material["blocked_proposals"]
    assert validate_document(material, profile, job)["status"] == "passed"


def test_profile_targets_the_selected_advert_and_uses_a_concrete_evidence_example(document):
    job, profile, proposal, _ = document
    technical = build_document(dict(job, description="Python API integration and testing."), profile, proposal)
    customer = build_document(dict(job, description="Customer communication and practical problem-solving."), profile, proposal)
    assert technical["sections"][0]["paragraphs"] != customer["sections"][0]["paragraphs"]
    assert "API integration" in technical["sections"][0]["paragraphs"][0]
    assert "customer problem-solving" in customer["sections"][0]["paragraphs"][0]
    assert technical["direction"]["positioning"] == customer["direction"]["positioning"] == DEFAULT_POSITIONING


def test_explicit_absence_and_future_plans_never_become_positive_skills_or_fit(document):
    job, profile, proposal, material = document
    profile["skills"] += ["Swift", "Kotlin"]
    profile["evidence"] += [
        {"id": "negative", "status": "DIRECT", "text": "No direct experience with Swift."},
        {"id": "planned", "status": "DIRECT", "text": "Will learn Kotlin and build an application next year."}]
    packet = generation_packet(job, profile)
    assert not {"Swift", "Kotlin"}.intersection(packet["allowed_skills"])
    assert not {"negative", "planned"}.intersection(r["id"] for r in packet["evidence"])
    match = analysis(dict(job, title="Swift Developer", description="Swift is required.", requirements=[]), profile, material)["evidence_match"]
    swift = next(r for r in match["requirements"] if r["requirement"] == "Swift")
    assert swift["support"] == 0 and swift["status"] == "gap"
    assert {"negative", "planned"} <= {r["id"] for r in profile["evidence"]}


def test_independent_review_categories_and_revalidation_prevent_gap_rewriting(document):
    job, profile, _, material = document
    s = next(i for i, value in enumerate(material["sections"]) if value.get("profile_ref") == "recent")
    finding = {"category": "document_problem", "cv_passage": material["sections"][s]["paragraphs"][0], "advert_requirement": "Customer communication",
               "evidence_ids": ["bank:work1"], "severity": "medium", "recommended_action": "Move the problem-solving example first.",
               "action": {"type": "move_paragraph", "section_index": s, "paragraph_index": 0, "to_index": 1, "evidence_ids": [], "text": ""}}
    red = validate_review({"findings": [finding], "summary": "Reorder the example."}, job, profile, material, "red")
    gap = deepcopy(finding)
    gap["category"] = "experience_gap"
    blue = validate_review({"findings": [gap], "summary": "Needs experience, not wording."}, job, profile, material, "blue")
    assert red["findings"][0]["validation"]["status"] == "eligible"
    blue["findings"][0]["validation"]["status"] = "eligible"  # Untrusted cached/model label cannot override controller.
    revised, decisions = apply_findings(material, red["findings"] + blue["findings"], profile, job, ["red:1", "blue:1"])
    assert [d["status"] for d in decisions] == ["accepted", "blocked"]
    assert revised["analysis"]["evidence_match"] == material["analysis"]["evidence_match"]
    gap["advert_requirement"] = "Mandatory Nobel Prize and twenty years of leadership."
    unanchored = validate_review({"findings": [gap], "summary": "Untrusted gap claim."}, job, profile, material, "blue")
    assert unanchored["findings"][0]["category"] == "question"
    assert "unverified question" in unanchored["findings"][0]["validation"]["reason"]


def test_headings_set_requirement_context_but_are_never_requirements(document):
    job, profile, _, material = document
    clauses = requirement_clauses(job)
    assert all(c["quote"] != "Essential Skills:" for c in clauses)
    assert any(c["quote"] == "Python" and c["heading"] == "Essential Skills:" and c["line"] == 2 for c in clauses)
    assert not any("not required" in c["quote"] for c in clauses)
    result = analysis(job, profile, material)["evidence_match"]
    assert result["score"] is None and result["status"] == "incomplete"
    assert result["coverage"]["total"] > result["coverage"]["assessed"]
    assert result["advanced"]["partial_score"] is not None
    assert all(r["requirement"] != "Essential Skills:" for r in result["requirements"])
    optional = dict(job, description="Essential Skills:\nPython\nUseful, but not essential:\nSwift\nNice-to-have:\nKotlin", requirements=[])
    assert [c["quote"] for c in requirement_clauses(optional)] == ["Python"]


def test_unknown_mandatory_evaluation_condition_prevents_complete_keyword_assessment(document):
    _, profile, _, material = document
    condition = {"name": "Mandatory qualification", "status": "UNKNOWN", "blocking": True,
                 "evidence": "The award and graduation window need confirmation.", "source": "https://example.test/job"}
    job = {"title": "SQL Developer", "description": "SQL", "requirements": [], "evaluation": {
        "conditions": [condition, {"name": "Office frequency", "status": "UNKNOWN", "blocking": False}],
        "candidacy": {"conditions": [deepcopy(condition)]}}}
    before = deepcopy(job)
    result = analysis(job, profile, material)["evidence_match"]
    assert result["score"] is None and result["status"] == "incomplete"
    assert result["advanced"]["partial_score"] == 100
    assert result["coverage"]["assessed"] == 1 and result["coverage"]["total"] == 2
    assert result["coverage"]["unassessed_essential"] == 1
    assert result["eligibility_checks"]["pending_count"] == 1
    assert result["eligibility_checks"]["conditions"][0]["advert_quote"] is None
    assert "Mandatory qualification" in " ".join(result["gaps"])
    assert analysis(job, profile, dict(material, cv_text="SQL " * 50))["evidence_match"] == result
    assert job == before
    for value in [job["evaluation"]["conditions"][0], job["evaluation"]["candidacy"]["conditions"][0]]:
        value["status"] = "PASS"
    resolved = analysis(job, profile, material)["evidence_match"]
    assert resolved["coverage"]["total"] == result["coverage"]["total"]
    assert resolved["coverage"]["assessed"] == 2 and resolved["score"] == 100
    assert resolved["eligibility_checks"]["status"] == "assessed"


def test_candidacy_component_math_includes_actual_caps_without_changing_scores(document):
    _, profile, _, _ = document
    profile["skills"] += ["Python", "SQL"]
    role = {"title": "Scientific Research Engineer", "description": "Python numerical research. Communicate scientific findings.", "salary_min": 60000, "office_days": 1}
    legacy = {"components": {"london": True, "salary_state": "FAIL"}, "conditions": [{"name": "Work authorisation", "blocking": True, "status": "UNKNOWN"}], "blockers": []}
    result = assess_candidacy(role, profile, default_settings(), legacy)
    breakdown = result["score_breakdown"]
    assert sum(c["contribution"] for c in breakdown["components"]) == pytest.approx(breakdown["weighted_base"])
    assert round(breakdown["weighted_base"] + sum(a["amount"] for a in breakdown["adjustments"]), 1) == result["score"]
    assert breakdown["final_score"] == result["score"]
    assert {a["type"] for a in breakdown["adjustments"]} >= {"penalty", "band_cap"}
    assert any("mandatory eligibility" in a["reason"] for a in breakdown["adjustments"])


def test_cv_built_under_earlier_rules_asks_for_a_new_version_on_every_recheck(document, tmp_path):
    # The profile statement wording changed after coherent-cv-v1, so a stored v1
    # CV cannot be proved again. Each re-check names the cause and the remedy
    # instead of reporting unapproved profile prose.
    from careerops.cv_execution import CVExecution
    from careerops.cv_review import start_review
    from careerops.store import Store
    job, profile, _, material = document
    assert material["document_schema"] == DOCUMENT_VERSION != "coherent-cv-v1"
    assert validate_document(material, profile, job)["status"] == "passed"
    old = dict(deepcopy(material), document_schema="coherent-cv-v1")
    clear = r"earlier CV rules \(coherent-cv-v1\).*Create a new version of this CV"
    for recheck in (lambda: validate_document(old, profile, job), lambda: validate_draft(old, profile),
                    lambda: review_packet(job, profile, old), lambda: apply_findings(old, [], profile, job)):
        with pytest.raises(OutdatedDocumentError, match=clear):
            recheck()
    store = Store(tmp_path / "earlier-rules.sqlite3")
    store.update_profile(profile)
    saved = store.upsert_job(dict(job, url="https://example.test/earlier-rules"))["job"]
    stored = store.save_material(saved["id"], "earlier-rules-cv", {k: v for k, v in old.items() if k not in {"id", "job_id"}})
    with pytest.raises(OutdatedDocumentError, match=clear):
        start_review(store, stored["id"], explicit=True, job_id=saved["id"])
    runner = CVExecution(store, autostart=False)
    for action in ("review_again", "revise"):
        with pytest.raises(OutdatedDocumentError, match=clear):
            runner.start(saved["id"], {"action": action, "material_id": stored["id"]})
