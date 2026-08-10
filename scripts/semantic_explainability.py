#!/usr/bin/env python3
"""Persistent explainability records for IFSC semantic missions.

The record is evidence, not a control input. Flight authorization remains in
the dashboard's local plan validator and deterministic route planner.
"""

import copy
import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone


SCHEMA = "ifsc.semantic_explainability.v1"


def canonical_json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_explanation_id(now=None):
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    return "explain_%s_%s" % (stamp, uuid.uuid4().hex[:8])


def goal_resolution(plan, map_data):
    goals = map_data.get("goals", {})
    resolved = []
    for index, step in enumerate(plan, start=1):
        item = {"step": index, "action": step["action"]}
        if step["action"] == "fly_to":
            goal_id = step["goal_id"]
            goal = goals.get(goal_id, {})
            item.update({
                "goal_id": goal_id,
                "display_label": (
                    goal.get("display_label") or goal.get("label") or goal_id),
                "position_m": copy.deepcopy(goal.get("position_m")),
                "altitude_m": goal.get("altitude_m"),
                "vision": bool(step.get("vision", False)),
                "fly_through": bool(step.get("fly_through", False)),
            })
        elif step["action"] == "hover":
            item["seconds"] = float(step["seconds"])
        resolved.append(item)
    return resolved


class ExplainabilityStore:
    """Atomic JSON record store keyed by a server-generated explanation id."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self._lock = threading.Lock()

    def path_for(self, explanation_id):
        if not isinstance(explanation_id, str) or not explanation_id:
            raise ValueError("解释记录 ID 不能为空")
        if not explanation_id.startswith("explain_"):
            raise ValueError("解释记录 ID 无效")
        if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in explanation_id):
            raise ValueError("解释记录 ID 无效")
        return os.path.join(self.root, explanation_id + ".json")

    def _write(self, record):
        os.makedirs(self.root, exist_ok=True)
        path = self.path_for(record["explanation_id"])
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
        return path

    def read(self, explanation_id):
        path = self.path_for(explanation_id)
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def create(self, raw_command, requested_vision, parser_name, model,
               fallback_reason, plan, map_path, map_data, source="command"):
        explanation_id = new_explanation_id()
        created = utc_now()
        parser_contract = {
            "name": parser_name,
            "model": model if parser_name == "deepseek" else None,
            "goal_whitelist": sorted(map_data.get("goals", {}).keys()),
            "accepted_actions": ["fly_to", "hover", "land"],
            "control_authority": "local_validation_and_route_planner",
        }
        record = {
            "schema": SCHEMA,
            "explanation_id": explanation_id,
            "status": "parsed",
            "created_at": created,
            "updated_at": created,
            "command": {
                "source": source,
                "raw_text": str(raw_command or "")[:2000],
                "requested_vision": bool(requested_vision),
            },
            "parser": {
                "name": parser_name,
                "model": model if parser_name == "deepseek" else None,
                "fallback_reason": fallback_reason,
                "contract_sha256": canonical_sha256(parser_contract),
                "contract": parser_contract,
            },
            "semantic_map": {
                "map_id": map_data.get("map_id"),
                "display_name": map_data.get("display_name"),
                "frame": map_data.get("frame"),
                "ue_map": map_data.get("ue_map"),
                "path": os.path.abspath(map_path),
                "sha256": file_sha256(map_path),
            },
            "validated_plan": copy.deepcopy(plan),
            "plan_sha256": canonical_sha256(plan),
            "goal_resolution": goal_resolution(plan, map_data),
            "safety_review": None,
            "trajectory": None,
            "execution": None,
        }
        with self._lock:
            record["record_path"] = self.path_for(explanation_id)
            self._write(record)
        return copy.deepcopy(record)

    def update_plan(self, explanation_id, plan, map_data,
                    reason="plan_changed_before_trajectory"):
        """Replace the active plan while preserving an auditable revision."""
        new_hash = canonical_sha256(plan)
        with self._lock:
            record = self.read(explanation_id)
            old_hash = record.get("plan_sha256")
            if old_hash and old_hash != new_hash:
                revisions = list(record.get("plan_revisions") or [])
                revisions.append({
                    "changed_at": utc_now(),
                    "reason": reason,
                    "previous_plan_sha256": old_hash,
                    "previous_plan": copy.deepcopy(
                        record.get("validated_plan")),
                    "replacement_plan_sha256": new_hash,
                    "replacement_plan": copy.deepcopy(plan),
                })
                record["plan_revisions"] = revisions
            record["validated_plan"] = copy.deepcopy(plan)
            record["plan_sha256"] = new_hash
            record["goal_resolution"] = goal_resolution(plan, map_data)
            record["status"] = "parsed"
            record["updated_at"] = utc_now()
            self._write(record)
            return copy.deepcopy(record)

    def update(self, explanation_id, status, fields):
        with self._lock:
            record = self.read(explanation_id)
            record.update(copy.deepcopy(fields))
            record["status"] = status
            record["updated_at"] = utc_now()
            self._write(record)
            return copy.deepcopy(record)

    def attach_review(self, explanation_id, review):
        return self.update(explanation_id, "reviewed", {
            "safety_review": copy.deepcopy(review),
        })

    def attach_trajectory(self, explanation_id, trajectory):
        return self.update(explanation_id, "trajectory_generated", {
            "trajectory": copy.deepcopy(trajectory),
        })

    def attach_execution(self, explanation_id, task_id, status, **details):
        execution = {"task_id": task_id, "status": status}
        execution.update(copy.deepcopy(details))
        terminal = status in {"done", "stopped", "error"}
        return self.update(
            explanation_id,
            "completed" if terminal else "executing",
            {"execution": execution},
        )


def public_summary(record):
    review = record.get("safety_review") or {}
    trajectory = record.get("trajectory") or {}
    execution = record.get("execution") or {}
    semantic_map = record.get("semantic_map") or {}
    return {
        "explanation_id": record.get("explanation_id"),
        "status": record.get("status"),
        "parser": (record.get("parser") or {}).get("name"),
        "model": (record.get("parser") or {}).get("model"),
        "map_id": semantic_map.get("map_id"),
        "map_sha256": semantic_map.get("sha256"),
        "plan_sha256": record.get("plan_sha256"),
        "risk": review.get("risk"),
        "blocked": review.get("blocked"),
        "route_distance_m": review.get("route_distance_m"),
        "route_point_count": trajectory.get("route_point_count"),
        "task_id": execution.get("task_id"),
        "outcome": execution.get("outcome"),
        "record_path": record.get("record_path"),
    }
