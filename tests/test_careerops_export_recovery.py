"""Interrupted exports recover without submitting synthetic applications."""

import time

from docx import Document

from careerops import materials
from careerops.server import Application


def _wait_for_preparation(manager, batch_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        batch = manager.get(batch_id)
        if batch["status"] not in {"running", "cancelling"}:
            return batch
        time.sleep(0.01)
    raise AssertionError("The synthetic preparation batch did not stop within 10 seconds")


def test_batch_resume_recovers_interrupted_and_corrupt_docx_exports(tmp_path, monkeypatch):
    app = Application(tmp_path / "careerops.sqlite3")
    job = app.store.upsert_job(
        {
            "title": "Python Research Engineer — synthetic export fixture",
            "company": "Synthetic export recovery employer",
            "description": (
                "Python numerical modelling research in Athens, Greece. "
                "Statistics research and transferable Python experience welcome."
            ),
            "location": "Athens, Greece",
            "country": "GR",
            "url": "https://example.org/synthetic-export-recovery",
            "sample": True,
        },
        refresh=False,
    )["job"]
    original_renderer = materials.render_docx

    def interrupted_renderer(material, target):
        target.write_bytes(b"synthetic partial DOCX archive")
        raise ValueError("Synthetic interrupted document render")

    monkeypatch.setattr(materials, "render_docx", interrupted_renderer)
    preview = app.preparation.preview({"job_ids": [job["id"]]})
    batch = app.preparation.start(preview["preview_id"])
    failed = _wait_for_preparation(app.preparation, batch["id"])

    assert failed["status"] == "completed_with_errors"
    assert failed["completed"] == 0 and failed["failed"] == 1
    assert "Synthetic interrupted" in failed["items"][0]["error"]
    assert not list(tmp_path.glob("materials/*/application-draft.docx"))
    assert not list(tmp_path.glob("materials/*/.export-*"))

    # Also exercise recovery of a bad final cache file left by an older release.
    original_material = app.store.get_job(job["id"])["materials"][0]
    cached_export = (
        tmp_path / "materials" / str(original_material["id"]) / "application-draft.docx"
    )
    cached_export.write_bytes(b"synthetic preexisting corrupt DOCX cache")
    monkeypatch.setattr(materials, "render_docx", original_renderer)

    app.preparation.resume(batch["id"])
    recovered = _wait_for_preparation(app.preparation, batch["id"])

    assert recovered["status"] == "completed"
    assert recovered["completed"] == 1 and recovered["failed"] == 0
    assert recovered["items"][0]["material_id"] == original_material["id"]
    assert recovered["items"][0]["document_validated"] is True
    assert Document(cached_export).paragraphs
    assert not list(tmp_path.glob("materials/*/.export-*"))

    current = app.store.get_job(job["id"])
    assert len(current["materials"]) == 1
    assert current["status"] == "materials_ready"
    assert not current.get("applied_at")
    assert not current.get("application_opened_at")
