"""Real Chromium UI check with temporary data and explicitly mocked model calls.

Run: PYTHONPATH=src python tests/browser_smoke.py [artifact_directory]
The default artifact directory is ignored local_data/validation/browser.
"""
import io
import json
import re
import sys
import tempfile
import threading
import time
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from docx import Document
from playwright.sync_api import expect, sync_playwright

from careerops import model_connections
from careerops.server import Application, make_server


def main():
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "local_data/validation/browser")
    output.mkdir(parents=True, exist_ok=True)
    calls, review_packets, errors, external = [], [], [], []

    def status(store):
        config = model_connections.defaults(store.settings())
        return {"ready": True, "setup_blockers": [], "configuration": config,
                "roles": {role: dict(config[role], ready=True, live_verified=False)
                          for role in model_connections.ROLES}, "detected": {}}

    def execute(connection, payload, schema, role, **kwargs):
        calls.append(role)
        if role == "generator":
            evidence = {item["id"]: item["text"] for item in payload["evidence"]}
            response = {
                "profile_statement_ids": [item["id"] for item in payload["allowed_profile_statements"][:2]],
                "skill_names": payload["allowed_skills"][:2],
                "direction_reason": "Synthetic browser validation with no external model call.",
                "sections": [{"section_id": section["section_id"], "claims": [
                    {"text": evidence[key], "evidence_ids": [key]}
                    for key in section["evidence_ids"][:2]]}
                    for section in payload["available_sections"]],
            }
        elif role == "purple":
            response = {"summary": "Synthetic purple synthesis; supported facts retained.",
                        "decisions": [{"finding_id": f["id"], "decision": "agree", "reason": "Supported by frozen evidence."} for f in payload["findings"]],
                        "letter_paragraphs": [{"evidence_ids": [payload["evidence"][0]["id"]]}]}
        else:
            review_packets.append(deepcopy(payload))
            response = {"summary": "Synthetic independent review; no external model was called.", "findings": []}
        return {"response": response, "provider": connection["provider"],
                "actual_model": "synthetic-mock", "mocked": True, "tool_calls": 0}

    with tempfile.TemporaryDirectory(prefix="careerops-ui-test-") as temporary, \
            patch.object(model_connections, "status", status), \
            patch.object(model_connections, "execute", execute):
        app = Application(Path(temporary) / "synthetic.sqlite3")
        assert app.store.profile()["name"] == ""
        assert not app.store.profile()["evidence"]
        assert not app.store.jobs()
        server = make_server(app, 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1365, "height": 1000})
                page.set_default_timeout(10000)
                page.on("pageerror", lambda error: errors.append(str(error)))

                def route(request):
                    if request.request.url.startswith(origin + "/"):
                        request.continue_()
                    else:
                        external.append(request.request.url)
                        request.abort()

                page.route("**/*", route)
                page.goto(origin + "/#your-cv")
                form = page.locator("#profile-form")
                expect(form.get_by_label("Name", exact=True)).to_have_value("")
                form.get_by_label("Name", exact=True).fill("Synthetic Test Candidate")
                form.get_by_label("Home location", exact=True).fill("London, UK")
                form.get_by_label("Skills").fill("Python\nSQL")

                def section(label):
                    block = form.locator("details").filter(
                        has=page.locator("summary", has_text=re.compile("^" + re.escape(label) + "$"))).last
                    block.locator("summary").click()
                    return block

                contact = section("Contact details")
                contact.get_by_label("Email", exact=True).fill("candidate@example.invalid")
                form.get_by_text("Experience & qualifications", exact=True).click()
                employment = section("Employment")
                employment.get_by_role("button", name="＋ Add entry", exact=True).click()
                for label, value in {"Role": "Data Analyst", "Employer": "Synthetic Example Company",
                                     "Start date": "2020", "End date or Present": "2022",
                                     "Supporting evidence IDs": "test:work"}.items():
                    employment.get_by_label(re.compile("^" + re.escape(label))).fill(value)
                evidence = section("Evidence records")
                evidence.get_by_role("button", name="＋ Add entry", exact=True).click()
                evidence.get_by_label("Evidence ID", exact=True).fill("test:work")
                evidence.get_by_label("Supported fact", exact=True).fill(
                    "Developed Python integrations, analysed data with SQL and explained technical results to customers.")
                evidence.get_by_label("Source or document reference", exact=True).fill("Synthetic test fixture")
                evidence.get_by_label("Evidence status").select_option("verified")
                form.get_by_role("button", name="Save profile", exact=True).click()
                expect(page.locator("#profile-save-status")).to_have_text("Profile version saved")
                profile = app.store.profile()
                assert profile["contact"]["email"] == "candidate@example.invalid"
                assert profile["employment"][0]["direct_evidence_ids"] == ["test:work"]
                page.reload()
                expect(form.get_by_label("Name", exact=True)).to_have_value("Synthetic Test Candidate")

                document, content = Document(), io.BytesIO()
                for line in ("Synthetic Test Candidate", "candidate@example.invalid", "Experience",
                             "Data Analyst at Synthetic Example Company", "2020–2022", "Skills", "Python and SQL"):
                    document.add_paragraph(line)
                document.save(content)
                page.goto(origin + "/#your-cv")
                page.locator('#base-cv-body input[type="file"]').set_input_files({
                    "name": "synthetic-base.docx", "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "buffer": content.getvalue()})
                page.locator("#base-cv-body").get_by_role("button", name="Upload base CV", exact=True).click()
                expect(page.locator("#base-cv-body")).to_contain_text("Selected base CV")
                job = app.store.upsert_job({
                    "title": "Junior Python Engineer", "company": "Synthetic Hiring Company",
                    "location": "London, UK", "url": "https://example.invalid/jobs/synthetic",
                    "salary_min": 65000, "salary_currency": "GBP", "salary_period": "year",
                    "salary_type": "base", "office_days": 2,
                    "description": "Develop Python integrations and analyse data using SQL. Explain technical results clearly.",
                })["job"]
                page.goto(origin + "/#all")
                card = page.locator(f'[data-job-id="{job["id"]}"]')
                expect(card).to_be_visible()
                card.locator("[data-bookmark-id]").click()
                expect(card.locator("[data-bookmark-id]")).to_have_attribute("aria-pressed", "true")
                card.get_by_role("button", name="Prepare application", exact=True).click()
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    runs = app.cv_execution.list(job["id"])
                    if runs and runs[0]["status"] not in {"queued", "running"}:
                        break
                    page.wait_for_timeout(100)
                run = app.cv_execution.list(job["id"])[0]
                assert run["status"] in {"ready", "needs_answer"}, (run["status"], run.get("error"))
                assert calls == ["generator", "red", "blue", "purple"], calls
                assert len(review_packets) == 2 and review_packets[0] == review_packets[1]
                pack = run["application_packs"][str(run["selected_material_id"])]
                assert "Dear Hiring Team" in pack["cover_letter"]["text"]
                assert pack["mocked"] is True
                assert run["document_checks"]
                for check in run["document_checks"].values():
                    assert check["docx_available"]
                    assert check.get("pdf_text_extractable") or check.get("pdf_available") is False
                expect(page.locator("#cv-workflow-body")).to_contain_text("Test run", timeout=10000)
                expect(page.locator(".cv-live-status.active")).to_have_count(0)
                expect(page.locator("#cv-workflow-body")).to_contain_text("Your cover letter")
                for fmt in ("txt", "docx"):
                    response = page.request.get(f"{origin}/api/cv-runs/{run['id']}/application-pack/{pack['material_id']}/{fmt}")
                    assert response.status == 200 and len(response.body()) > 50
                page.screenshot(path=str(output / "cv-workflow.png"), full_page=True)
                page.goto(origin + "/#saved")
                expect(page.locator(f'[data-job-id="{job["id"]}"]')).to_be_visible()
                assert app.store.get_job(job["id"])["application"]["stage"] == "not_started"
                assert not app.store.meta("cv_model_live_receipts")
                assert not app.store.settings().get("schedules_enabled")
                assert not errors, errors
                assert not external, external
                receipt = {"blank_profile_edit_save_reload": "passed", "base_cv_upload": "passed",
                           "saved_jobs": "passed", "mocked_model_calls": calls,
                           "independent_review_inputs_identical": True, "run_status": run["status"],
                           "document_checks": run["document_checks"], "application_submitted": False, "cover_letter_downloads": ["txt", "docx"],
                           "browser_errors": errors, "external_browser_requests": external}
                (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
                print(json.dumps(receipt, indent=2))
                browser.close()
        finally:
            app.cv_execution.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    main()
