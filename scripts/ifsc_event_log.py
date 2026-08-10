#!/usr/bin/env python3
"""Small JSONL event logger shared by IFSC command-line components."""

from __future__ import annotations

import datetime as _datetime
import json
import os
import uuid


EVENT_SCHEMA_VERSION = "ifsc.event.v1"


def utc_now_iso():
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat()


def append_event(path, event_type, payload, *, run_id=None, event_id=None):
    """Append one durable, self-describing UTF-8 JSON event."""
    if not path:
        return None
    full_path = os.path.abspath(path)
    parent = os.path.dirname(full_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    event = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": event_id or "event_" + uuid.uuid4().hex,
        "event_type": str(event_type),
        "timestamp_utc": utc_now_iso(),
        "run_id": run_id,
        "payload": payload,
    }
    encoded = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
    with open(full_path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return event
