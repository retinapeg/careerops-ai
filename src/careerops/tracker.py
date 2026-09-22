"""Independent bookmarks and explicit human application state.

No providers or legacy workers are started here. Pure state helpers are also
shared with the optional legacy submission guard.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path


STAGES = ("not_started", "in_progress", "applied", "screening", "interview", "offer", "rejected", "withdrawn")
PROTECTED_STAGES = set(STAGES) - {"not_started"}
EDITABLE_FIELDS = {"stage", "application_date", "last_contact", "next_action", "follow_up_date", "notes",
                   "recruiter_contact", "material_id", "cover_letter_reference"}
STAGE_FROM_STATUS = {"applied": "applied", "submitted": "applied", "already_applied_external": "applied",
                     "in_progress": "in_progress", "screening": "screening", "interview": "interview",
                     "offer": "offer", "rejected": "rejected", "withdrawn": "withdrawn"}


class ConflictError(ValueError):
    def __init__(self, message, current_application=None):
        super().__init__(message)
        self.current_application = deepcopy(current_application)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def ensure_tracker(job: dict) -> dict:
    """Add fields without recasting closed worker outcomes as employer rejection."""
    result = deepcopy(job)
    bookmarked = result.get("bookmarked")
    if not isinstance(bookmarked, bool):
        bookmarked = bool(result.get("manually_saved") or result.get("status") == "saved")
    result["bookmarked"] = bookmarked
    # Unknown historical bookmark time stays unknown, never a fabricated today.
    result.setdefault("bookmarked_at", None)
    result["manually_saved"] = bookmarked
    existing = result.get("application") if isinstance(result.get("application"), dict) else {}
    stage = existing.get("stage", STAGE_FROM_STATUS.get(result.get("status"), "not_started"))
    if stage not in STAGES:
        stage = "not_started"
    application = {
        "stage": stage, "application_date": result.get("applied_at"), "last_contact": None,
        "next_action": "", "follow_up_date": result.get("follow_up_date"), "notes": result.get("notes") or "",
        "recruiter_contact": "", "material_id": None, "cover_letter_reference": "",
        "source": "legacy_import" if result.get("legacy") else "user_confirmed" if stage != "not_started" else "not_recorded",
        "version": 0, "updated_at": None,
        **existing,
    }
    application["stage"] = stage
    result["application"] = application
    return result


def update_application(job: dict, patch: dict, *, expected_version, confirmed_applied=False, actor="user") -> tuple[dict, dict]:
    result = ensure_tracker(job)
    before = deepcopy(result["application"])
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise ValueError("An integer expected_version is required to update an application.")
    if expected_version != before["version"]:
        raise ConflictError("This application changed elsewhere. Review its current details before saving again.", before)
    if not isinstance(patch, dict) or not patch:
        raise ValueError("Supply the application fields to update.")
    if set(patch) - EDITABLE_FIELDS:
        raise ValueError("Application source, version and audit fields cannot be edited directly.")
    after = deepcopy(before)
    for field, value in patch.items():
        if field == "stage":
            if value not in STAGES:
                raise ValueError("Choose a supported application stage.")
        elif field == "material_id":
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise ValueError("Material reference must be an existing material ID or null.")
        elif field in {"application_date", "last_contact", "follow_up_date"}:
            if value in (None, ""):
                value = None
            else:
                if not isinstance(value, str) or len(value) > 40:
                    raise ValueError(f"{field} must be an ISO date or timestamp.")
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError(f"{field} must be an ISO date or timestamp.") from None
        else:
            if value is None:
                value = ""
            if not isinstance(value, str) or len(value) > (20000 if field == "notes" else 4000):
                raise ValueError(f"{field} must be text within its length limit.")
        after[field] = value
    changed_stage = after["stage"] != before["stage"]
    if changed_stage and after["stage"] == "applied":
        if confirmed_applied is not True:
            raise ValueError("Confirm that the application was submitted before marking it Applied.")
        if not after["application_date"]:
            after["application_date"] = timestamp()
    if changed_stage:
        after["source"] = "user_confirmed" if actor == "user" else actor
    changes = {field: {"before": before.get(field), "after": after.get(field)} for field in EDITABLE_FIELDS if before.get(field) != after.get(field)}
    if changes:
        after["version"] = before["version"] + 1
        after["updated_at"] = timestamp()
    result["application"] = after
    result["notes"] = after["notes"]
    result["follow_up_date"] = after["follow_up_date"]
    if after["application_date"]:
        result["applied_at"] = after["application_date"]
    elif "application_date" in patch:
        result.pop("applied_at", None)
    if changed_stage:
        result["status"] = after["stage"] if after["stage"] != "not_started" else "saved" if result["bookmarked"] else "new"
    event = {"actor": actor, "changes": changes, "before": before, "after": deepcopy(after), "expected_version": expected_version}
    return result, event


@contextmanager
def application_write_lock(database_path):
    """Serialize manual edits against the legacy worker's final-submit boundary.

    It is deliberately non-blocking: the user sees a conflict if submission is
    already in flight, rather than a fake saved state racing an external click.
    """
    import fcntl
    path = Path(database_path).resolve().with_suffix(".application-lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConflictError("An application update or guarded submission is in progress. Reload and retry after its outcome is known.") from None
        yield
    finally:
        os.close(fd)
