"""The evaluation fixtures stay consistent with the real document builder."""
import importlib.util
import json
from pathlib import Path

from careerops.cv_document import validate_document
from careerops.store import digest

FIXTURES = Path(__file__).resolve().parents[1] / "evals" / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_reference_documents_validate_against_the_stored_profiles():
    profiles = {p["profile_id"]: p for p in _load("profiles.json")["profiles"]}
    documents = _load("reference_documents.json")["documents"]
    assert len(profiles) >= 3 and sorted(profiles) == sorted(d["profile_id"] for d in documents)
    for document in documents:
        source = profiles[document["profile_id"]]
        assert document["protected_profile_hash"] == digest(source["profile"]) == document["material"]["protected_profile_hash"]
        assert document["job"] == source["job"]
        assert validate_document(document["material"], source["profile"], source["job"])["status"] == "passed"
        assert document["material"]["blocked_proposals"] == [] and document["held_back_evidence"]
        assert document["listed_skills_not_in_document"] and document["skills_in_document"]


def test_reference_documents_match_a_fresh_build():
    spec = importlib.util.spec_from_file_location("build_reference_documents", FIXTURES / "build_reference_documents.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check()
