"""Fresh installations must never inherit a developer's candidate claims."""
from copy import deepcopy

import pytest

from careerops.materials import make_draft, validate_draft
from careerops.policy import default_profile
from careerops.server import Application


def test_fresh_workspace_has_no_candidate_data(tmp_path, monkeypatch):
    monkeypatch.setenv("CAREEROPS_LEGACY_ROOT", str(tmp_path / "another-workspace"))
    app = Application(tmp_path / "careerops.sqlite3")
    profile = app.store.profile()
    for key in ("name", "location", "contact", "qualifications", "education", "employment",
                "research", "projects", "skills", "domains", "evidence", "citizenships",
                "work_authorisation", "languages", "availability", "qualifications_not_held"):
        assert not profile[key], key
    assert profile["drives"] is None
    assert not app.store.jobs()
    assert not app.store.settings()["schedules_enabled"]
    assert app.store.meta("selected_base_cv_id") is None


def test_education_is_only_the_current_candidates_approved_evidence():
    profile = default_profile()
    profile.update(name="Candidate Example", qualifications=[
        {"name": "MSc Computing", "completed": True, "evidence_id": "degree"},
        {"name": "Unconfirmed degree", "completed": True, "evidence_id": "unconfirmed"},
        {"name": "Missing evidence", "completed": True, "evidence_id": "missing"},
    ], evidence=[
        {"id": "degree", "text": "Example University — MSc Computing.", "status": "verified"},
        {"id": "unconfirmed", "text": "Unconfirmed award.", "status": "unresolved"},
    ])
    draft = make_draft({"title": "Engineer", "company": "Example Company"}, profile)
    education = next(s for s in draft["sections"] if s["heading"] == "Education")
    assert education["paragraphs"] == ["Example University — MSc Computing."]
    assert validate_draft(draft, profile)["status"] == "passed"
    changed = deepcopy(draft)
    next(s for s in changed["sections"] if s["heading"] == "Education")["paragraphs"] = ["Invented doctorate"]
    with pytest.raises(ValueError):
        validate_draft(changed, profile)
    changed = deepcopy(draft)
    changed["cv_text"] += "\nInvented employment"
    with pytest.raises(ValueError):
        validate_draft(changed, profile)


def test_empty_profile_does_not_generate_qualifications():
    profile = default_profile()
    draft = make_draft({"title": "Engineer", "company": "Example Company"}, profile)
    education = next(s for s in draft["sections"] if s["heading"] == "Education")
    assert education["paragraphs"] == []
    assert draft["name"] == draft["location"] == ""
    assert validate_draft(draft, profile)["status"] == "passed"
