"""Append-only failed assignment evidence; never rewrite a submitted manifest."""

from pathlib import Path
import uuid

from .records import JobError, digest, encoded, load_json, now, save
from .security import contains_secret_material, safe_cli_text


def record_hold(root, request, error, *, manifest_path=None, backend="managed"):
    if request is None:
        try:
            manifest = load_json(manifest_path)
        except (JobError, OSError):
            manifest = None
        request = {"backend": backend, "manifest": manifest,
                   "manifest_path": str(manifest_path)}
    record = {"schema_version": 1, "state": "held", "observed_at": now(),
              "request": request, "blocker": {"kind": getattr(error, "kind", "budget_expired"),
                                               "message": safe_cli_text(str(error))},
              "launch_effects": 0}
    # Retain the submitted data only if credential-blind. Refused payloads get
    # a digest, never a partial or silently edited manifest.
    payload = encoded(request)
    if contains_secret_material(payload.decode()):
        record["request"] = {"sha256": digest(payload), "redacted": True}
    path = Path(root) / "preflight-holds" / (uuid.uuid4().hex + ".json")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    save(path, record)
    return path
