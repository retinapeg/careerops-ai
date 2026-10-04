"""Offline demonstration of the CV document workflow on synthetic data.

Runs the real controller graph in ``cv_execution`` (generator, red and blue
review, deterministic revision, purple synthesis and export checks) with a
scripted function in place of the model CLIs. No model is called, no process is
started and no network connection is opened; an offline guard makes any such
attempt raise. Every input is fictional and labelled SYNTHETIC.

    PYTHONPATH=src python -m careerops.demo --out local_data/demo

The run writes ``transcript.json`` and ``transcript.md`` to the output folder.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from unittest.mock import patch
import zipfile

from careerops import application_pack, base_cv, cv_document, materials, model_connections
from careerops.cv_execution import CVExecution, _hash
from careerops.cv_review import STRUCTURE
from careerops.store import Store

GENERATED_BY = "python -m careerops.demo"
PROVIDER = "scripted-offline"
SCRIPT_VERSION = "careerops-demo-script-v1"
HOW_REPLIES_WERE_MADE = (
    "Every generator, reviewer and synthesis reply was produced by a deterministic scripted "
    "function in careerops/demo.py and checked against the same pydantic schema the workflow "
    "applies. The controller, validators, revision logic, synthesis checks and DOCX export are the "
    "project's own code; only the model calls and LibreOffice detection are replaced, so PDF export "
    "is deliberately switched off rather than missing.")
NO_MODEL_STATEMENT = f"No model was called. {HOW_REPLIES_WERE_MADE} All data is synthetic."
MARKER = ".careerops-demo"
OWNED = ("demo.sqlite3", "demo.sqlite3-wal", "demo.sqlite3-shm", "demo.sqlite3-journal",
         "materials", "base_documents", "transcript.json", "transcript.md")
VOLATILE = {"created_at", "updated_at", "completed_at", "started_at", "checked_at", "uploaded_at",
            "finished_at", "elapsed_seconds", "snapshot_hash", "intent", "cv_run_snapshot", "stable_sha256"}
GUARDED = {"subprocess.Popen": (subprocess, "Popen"), "subprocess.run": (subprocess, "run"),
           "socket.socket.connect": (socket.socket, "connect"),
           "socket.create_connection": (socket, "create_connection"), "shutil.which": (shutil, "which")}

# Evidence ids use the "bank:" prefix. Plain ids are accepted by the section
# bank but not by the attribution check in cv_review._validate_finding, which
# compares against materials.record_refs (always "bank:"-prefixed).
EVIDENCE = [
    {"id": "bank:demo-e1", "text": "Built Python reports for weekly operations reviews.", "status": "verified"},
    {"id": "bank:demo-e2", "text": "Wrote SQL queries to reconcile customer billing records.", "status": "verified"},
    {"id": "bank:demo-e3", "text": "Automated a data validation workflow that tested incoming files.", "status": "verified"},
]
PROFILE = {
    "name": "Alex Example", "location": "Example City",
    "contact": {"email": "alex@example.invalid"},
    "skills": ["Python", "SQL"], "work_authorisation": {"GB": True},
    "employment": [{"record_id": "demo-job-1", "title": "Data Analyst", "employer": "Example Analytics Ltd",
                    "start": "2021", "end": "2024", "direct_evidence_ids": [e["id"] for e in EVIDENCE]}],
    "evidence": EVIDENCE,
}
GAP_QUOTE = "You must hold a Kubernetes certification."
UNANCHORED_QUOTE = "Ten years of Rust experience is essential."  # deliberately absent from the advert
JOB = {
    "title": "Python Data Engineer", "company": "Synthetic Hiring Co", "location": "London, UK",
    "url": "https://example.invalid/jobs/demo-1",
    "description": "SYNTHETIC ADVERT for the offline demo.\nBuild Python data pipelines.\n"
                   "Experience with SQL is required.\n" + GAP_QUOTE,
}
BASE_CV_LINES = ("Alex Example", "SYNTHETIC BASE CV for the offline demo",
                 "Data Analyst at Example Analytics Ltd 2021 - 2024",
                 "Built Python reports for weekly operations reviews.")
REPLY_MODELS = {"generator": cv_document.Generation, "red": cv_document.Review,
                "blue": cv_document.Review, "purple": application_pack.Synthesis}


class OfflineViolation(RuntimeError):
    """Raised when the demo attempts to start a process or open a connection."""


@contextmanager
def offline_guard():
    """Replace process and network entry points with a function that records and raises."""
    state = {"attempts": []}

    def refuse(*args, **kwargs):
        state["attempts"].append(repr(args[:1])[:200])
        raise OfflineViolation("The offline demo attempted to start a process or open a network connection.")

    state["refuse"] = refuse
    with ExitStack() as stack:
        for target, name in GUARDED.values():
            stack.enter_context(patch.object(target, name, refuse))
        yield state


def _guard_active(state):
    return all(getattr(target, name) is state["refuse"] for target, name in GUARDED.values())


def scripted_response(role, payload):
    """A pure function of the packet. The reply is checked against its schema before use."""
    if role == "generator":
        text = {record["id"]: record["text"] for record in payload["evidence"]}
        response = {
            "profile_statement_ids": [s["id"] for s in payload["allowed_profile_statements"][:2]],
            "skill_names": payload["allowed_skills"][:2],
            "direction_reason": "Scripted: the first two evidence statements for each section.",
            "sections": [{"section_id": s["section_id"],
                          "claims": [{"text": text[i], "evidence_ids": [i]} for i in s["evidence_ids"][:2]]}
                         for s in payload["available_sections"]]}
    elif role in {"red", "blue"}:
        findings = []
        if role == "red":
            for index, section in enumerate(payload["cv"]["sections"]):
                if not str(section.get("section_id", "")).startswith("employment:"):
                    continue
                shown = " ".join(section["paragraphs"])
                missing = [r["id"] for r in payload["evidence"] if r["text"] not in shown]
                if missing:
                    findings.append({
                        "category": "document_problem", "cv_passage": "", "advert_requirement": STRUCTURE,
                        "evidence_ids": missing[:1], "severity": "medium",
                        "recommended_action": "Add the omitted approved evidence to this role.",
                        "action": {"type": "add_evidence", "section_index": index, "paragraph_index": -1,
                                   "to_index": -1, "evidence_ids": missing[:1], "text": ""}})
                break
            findings.append({
                "category": "document_problem", "cv_passage": "", "advert_requirement": UNANCHORED_QUOTE,
                "evidence_ids": [], "severity": "high",
                "recommended_action": "Scripted unanchored observation: its advert quote does not appear in the advert.",
                "action": {"type": "flag", "section_index": -1, "paragraph_index": -1, "to_index": -1,
                           "evidence_ids": [], "text": ""}})
        else:
            findings.append({
                "category": "experience_gap", "cv_passage": "", "advert_requirement": GAP_QUOTE,
                "evidence_ids": [], "severity": "high",
                "recommended_action": "Confirm whether you hold this certification.",
                "action": {"type": "flag", "section_index": -1, "paragraph_index": -1, "to_index": -1,
                           "evidence_ids": [], "text": ""}})
        response = {"summary": f"Scripted {role} review.", "findings": findings}
    elif role == "purple":
        response = {
            "summary": "Scripted synthesis of the final red and blue reviews.",
            "decisions": [{"finding_id": f["id"],
                           "decision": "agree" if f["category"] == "document_problem" else "needs_answer",
                           "reason": "Scripted: only the candidate can answer this." if f["category"] != "document_problem"
                           else "Scripted: agree with this document observation."} for f in payload["findings"]],
            "letter_paragraphs": [{"evidence_ids": [record["id"]]} for record in payload["evidence"][:2]]}
    else:
        raise ValueError(f"The demo has no scripted reply for role {role!r}.")
    try:
        REPLY_MODELS[role].model_validate(response)
    except Exception as error:
        raise RuntimeError(f"The scripted {role} reply no longer matches its schema: {error}") from error
    return response


def scripted_status(store_or_settings, *, refresh=False):
    configuration = {role: {"provider": PROVIDER, "model": SCRIPT_VERSION, "effort": "none"}
                     for role in model_connections.ROLES}
    configuration.update(timeout_seconds=30, max_concurrent_runs=1)
    return {"ready": True, "setup_blockers": [], "configuration": configuration}


def _synthetic_base_cv():
    """python-docx stamps zip entries with the current time; fix it so the bytes are reproducible."""
    from docx import Document
    document = Document()
    for line in BASE_CV_LINES:
        document.add_paragraph(line)
    raw, fixed = io.BytesIO(), io.BytesIO()
    document.save(raw)
    with zipfile.ZipFile(raw) as source, zipfile.ZipFile(fixed, "w") as target:
        for item in source.infolist():
            info = zipfile.ZipInfo(item.filename, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, source.read(item.filename))
    return fixed.getvalue()


def _prepare(out):
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()) and not (out / MARKER).is_file():
        raise ValueError(f"{out} is not empty and was not created by this demo. Choose an empty --out folder.")
    for name in OWNED:
        path = out / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    (out / MARKER).write_text("Created by python -m careerops.demo. Safe to delete.\n", encoding="utf-8")


def stable_view(value):
    if isinstance(value, dict):
        return {key: stable_view(item) for key, item in value.items() if key not in VOLATILE}
    if isinstance(value, list):
        return [stable_view(item) for item in value]
    return value


def run_demo(out_dir):
    """Run one synthetic job through the workflow and return the transcript dict."""
    out = Path(out_dir).resolve()
    _prepare(out)
    calls, stages = [], []
    original_stage = CVExecution._stage

    with offline_guard() as guard:
        def scripted_execute(connection, payload, schema, role, **kwargs):
            response = scripted_response(role, payload)
            calls.append({"seq": len(calls) + 1, "role": role, "receipt_key": None,
                          "packet_sha256": _hash(payload), "schema_sha256": _hash(schema),
                          "schema_check": f"passed ({REPLY_MODELS[role].__name__})",
                          "offline_guard_active": _guard_active(guard),
                          "packet": deepcopy(payload), "response": deepcopy(response)})
            return {"response": response, "provider": PROVIDER, "requested_model": SCRIPT_VERSION,
                    "actual_model": SCRIPT_VERSION, "model_identity_source": "scripted", "usage": {},
                    "cli_version": None, "auth_method": None, "tool_calls": 0, "elapsed_seconds": 0.0,
                    "mocked": True}

        def recording_stage(self, run, key, label):
            stages.append({"stage": key, "label": label})
            return original_stage(self, run, key, label)

        store = Store(out / "demo.sqlite3")
        store.update_profile(deepcopy(PROFILE))
        job = store.upsert_job(deepcopy(JOB))["job"]
        base_cv.add(store, "synthetic-base-cv.docx", _synthetic_base_cv())
        with ExitStack() as stack:
            for target, name, value in (
                    (model_connections, "status", scripted_status),
                    (model_connections, "execute", scripted_execute),
                    (materials, "soffice_path", lambda: None),
                    (cv_document, "soffice_path", lambda: None),
                    (application_pack, "soffice_path", lambda: None),
                    (CVExecution, "_stage", recording_stage)):
                stack.enter_context(patch.object(target, name, value))
            runner = CVExecution(store, autostart=False)
            run = runner.start(job["id"])
            runner._run(run["id"])
            private = runner._read(run["id"])
        saved = [store.material(material_id) for material_id in private["material_ids"]]
        live_receipts = store.meta("cv_model_live_receipts")
        attempts = list(guard["attempts"])

    for call in calls:
        call["receipt_key"] = next((key for key, receipt in private["receipts"].items()
                                    if receipt.get("role") == call["role"]
                                    and receipt.get("packet_hash") == call["packet_sha256"]), None)
    transcript = {
        "schema_version": "careerops-offline-demo-v1", "generated_by": GENERATED_BY,
        "mocked": True, "synthetic_data": True, "provider": PROVIDER, "statement": NO_MODEL_STATEMENT,
        "offline_guard": {"guarded": list(GUARDED),
                          "attempts": attempts,
                          "pdf_export": "Disabled: soffice_path() returns no LibreOffice path during the demo."},
        "inputs": {"label": "SYNTHETIC", "profile": deepcopy(PROFILE), "job": deepcopy(JOB),
                   "base_cv": {"filename": "synthetic-base-cv.docx", "lines": list(BASE_CV_LINES)}},
        "stages": stages, "final_stage": private["stage"],
        "role_sequence": [call["role"] for call in calls],
        "calls": calls,
        "receipts": {key: {field: receipt.get(field) for field in ("role", "status", "provider", "actual_model",
                                                                   "packet_hash", "mocked")}
                     for key, receipt in private["receipts"].items()},
        "reviews": private["reviews"], "suggestions": private["suggestions"],
        "what_improved": private["what_improved"],
        "materials": [{"id": m["id"], "parent_id": m.get("parent_id"), "version": m.get("version"),
                       "cv_text": m.get("cv_text"), "blocked_proposals": m.get("blocked_proposals", [])}
                      for m in saved],
        "application_packs": private.get("application_packs", {}),
        "document_checks": private.get("document_checks", {}),
        "exported_files": sorted(str(p.relative_to(out)) for p in (out / "materials").rglob("*") if p.is_file()),
        "status": private["status"], "error": private["error"], "needs_attention": private["needs_attention"],
        "selected_material_id": private["selected_material_id"],
        "live_receipts_written": live_receipts is not None,
    }
    transcript["stable_sha256"] = _hash(stable_view(transcript))
    (out / "transcript.json").write_text(json.dumps(transcript, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "transcript.md").write_text(render_markdown(transcript), encoding="utf-8")
    return transcript


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(t):
    """A readable summary without packets, timestamps or local paths."""
    lines = [
        "# CareerOps offline demo transcript",
        "",
        f"Generated by `{t['generated_by']}`. All data is synthetic. No model was called.",
        "",
        HOW_REPLIES_WERE_MADE,
        "",
        f"- Final status: `{t['status']}` (stage `{t['final_stage']}`)",
        f"- Stages: {' -> '.join(s['stage'] for s in t['stages'])}",
        f"- Model-role calls (scripted): {' -> '.join(t['role_sequence'])}",
        f"- Live receipts written: {'yes' if t['live_receipts_written'] else 'no'}",
        f"- Process or network attempts blocked by the offline guard: {len(t['offline_guard']['attempts'])}",
        f"- stable_sha256: `{t['stable_sha256']}`",
        "",
        "## Synthetic inputs",
        "",
        f"- Candidate: {t['inputs']['profile']['name']} ({t['inputs']['profile']['contact']['email']})",
        f"- Advert: {t['inputs']['job']['title']} at {t['inputs']['job']['company']}",
        "",
        "```text",
        t["inputs"]["job"]["description"],
        "```",
        "",
        "Approved evidence:",
        "",
        *[f"- `{e['id']}`: {e['text']}" for e in t["inputs"]["profile"]["evidence"]],
        "",
        "## Calls",
        "",
        "| # | Receipt | Role | Packet sha256 | Schema check |",
        "|---|---|---|---|---|",
        *[f"| {c['seq']} | `{c['receipt_key']}` | {c['role']} | `{c['packet_sha256'][:12]}` | {c['schema_check']} |"
          for c in t["calls"]],
        "",
        "## Findings and what the controller did with them",
        "",
        "| Finding | Category | Validation | Status | Advert quote | Reason |",
        "|---|---|---|---|---|---|",
        *[f"| `{s['id']}` | {s['category']} | {s.get('validation', {}).get('status')} | {s['status']} "
          f"| {_cell(s['advert_requirement'])} | {_cell(s.get('validation', {}).get('reason', ''))} |"
          for s in t["suggestions"]],
        "",
        "What improved: " + ("; ".join(t["what_improved"]) or "nothing"),
        "",
        "## CV versions",
        "",
    ]
    for material in t["materials"]:
        parent = f", parent {material['parent_id']}" if material["parent_id"] else ""
        lines += [f"### Material {material['id']} (version {material['version']}{parent})", "",
                  "```text", material["cv_text"] or "", "```", ""]
    for material_id, pack in t["application_packs"].items():
        lines += [f"## Application pack for material {material_id}", "",
                  f"Mocked: {pack['mocked']}. Summary: {pack['summary']}", "",
                  "| Finding | Decision | Reason |", "|---|---|---|",
                  *[f"| `{d['finding_id']}` | {d['decision']} | {_cell(d['reason'])} |" for d in pack["decisions"]],
                  "", "Cover letter (body paragraphs copied verbatim from the selected evidence):", "",
                  "```text", pack["cover_letter"]["text"], "```", "",
                  "Outstanding questions:", "", *[f"- {q}" for q in pack["outstanding_questions"]], ""]
    lines += ["## Export checks", "", t["offline_guard"]["pdf_export"], ""]
    for material_id, check in t["document_checks"].items():
        lines.append(f"- Material {material_id}: " + ", ".join(f"{k}={v}" for k, v in check.items()))
    lines += ["", "## Needs your attention", "", *[f"- {item}" for item in t["needs_attention"]], ""]
    return "\n".join(lines)


def summary(t, out):
    applied = [s for s in t["suggestions"] if s["status"] == "applied"]
    demoted = [s for s in t["suggestions"] if s["category"] == "question"
               and s.get("validation", {}).get("status") == "needs_verification"]
    checks = next(iter(t["document_checks"].values()), {})
    versions = " -> ".join(str(m["id"]) for m in t["materials"])
    return "\n".join([
        "CareerOps offline demo: synthetic data, scripted replies, no model called.",
        f"Stages: {' -> '.join(s['stage'] for s in t['stages'])} -> {t['final_stage']}",
        f"Model-role calls (scripted): {', '.join(t['role_sequence'])}",
        f"CV versions: {versions}",
        f"Applied by the controller: {len(applied)} ({'; '.join(t['what_improved'])})",
        f"Unanchored findings demoted to questions: {len(demoted)}",
        f"Final status: {t['status']} ({len(t['needs_attention'])} items need attention)",
        f"DOCX check: {checks.get('status')}; PDF available: {checks.get('pdf_available')}",
        f"Live receipts written: {t['live_receipts_written']}; blocked process/network attempts: "
        f"{len(t['offline_guard']['attempts'])}",
        f"Transcript: {out / 'transcript.json'} and {out / 'transcript.md'}",
        f"stable_sha256: {t['stable_sha256']}",
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(prog=GENERATED_BY, description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default="local_data/demo", help="output folder (default: local_data/demo)")
    args = parser.parse_args(argv)
    out = Path(args.out)
    transcript = run_demo(out)
    print(summary(transcript, out))  # paths as given, so the summary does not expose the home folder
    return 0 if transcript["status"] in {"ready", "needs_answer"} else 1


if __name__ == "__main__":
    sys.exit(main())
