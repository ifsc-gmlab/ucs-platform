#!/usr/bin/env python3
"""Validate an IFSC recording and emit a machine-readable artifact manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys


SCHEMA_VERSION = "ifsc.recording.validation.v1"


def _ratio(value):
    if not value or value == "0/0":
        return 0.0
    numerator, denominator = value.split("/", 1)
    denominator_value = float(denominator)
    return float(numerator) / denominator_value if denominator_value else 0.0


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_video(path, ffprobe_path=None):
    ffprobe = ffprobe_path or shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found")
    command = [
        ffprobe, "-v", "error", "-count_frames",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate,nb_read_frames,duration:format=duration,size",
        "-of", "json", os.path.abspath(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError("ffprobe failed: %s" % completed.stderr.strip())
    payload = json.loads(completed.stdout)
    streams = payload.get("streams") or []
    if not streams:
        raise RuntimeError("recording contains no video stream")
    stream = streams[0]
    format_info = payload.get("format") or {}
    duration = float(stream.get("duration") or format_info.get("duration") or 0.0)
    return {
        "codec": stream.get("codec_name"),
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "fps": round(_ratio(stream.get("avg_frame_rate")), 3),
        "frame_count": int(stream.get("nb_read_frames") or 0),
        "duration_sec": round(duration, 3),
        "size_bytes": int(format_info.get("size") or os.path.getsize(path)),
    }


def validate_video(path, *, ffprobe_path=None, min_duration=5.0,
                   expected_width=None, expected_height=None, min_fps=20.0):
    full_path = os.path.abspath(path)
    checks = []
    errors = []
    if not os.path.isfile(full_path):
        raise FileNotFoundError(full_path)
    metrics = probe_video(full_path, ffprobe_path=ffprobe_path)

    def gate(name, passed, actual, expected):
        checks.append({"name": name, "pass": bool(passed),
                       "actual": actual, "expected": expected})
        if not passed:
            errors.append("%s: actual=%s expected=%s" % (name, actual, expected))

    gate("duration", metrics["duration_sec"] >= min_duration,
         metrics["duration_sec"], ">= %.3f s" % min_duration)
    gate("frame_rate", metrics["fps"] >= min_fps,
         metrics["fps"], ">= %.3f fps" % min_fps)
    gate("frame_count", metrics["frame_count"] >= int(min_duration * min_fps * 0.8),
         metrics["frame_count"], ">= %d" % int(min_duration * min_fps * 0.8))
    if expected_width is not None:
        gate("width", metrics["width"] == expected_width,
             metrics["width"], expected_width)
    if expected_height is not None:
        gate("height", metrics["height"] == expected_height,
             metrics["height"], expected_height)
    gate("codec", metrics["codec"] == "h264", metrics["codec"], "h264")

    return {
        "schema_version": SCHEMA_VERSION,
        "pass": not errors,
        "path": full_path,
        "sha256": _sha256(full_path),
        "metrics": metrics,
        "checks": checks,
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description="Validate an IFSC video artifact")
    parser.add_argument("--video", required=True)
    parser.add_argument("--ffprobe")
    parser.add_argument("--min-duration", type=float, default=5.0)
    parser.add_argument("--min-fps", type=float, default=20.0)
    parser.add_argument("--expected-width", type=int)
    parser.add_argument("--expected-height", type=int)
    parser.add_argument("--json-out")
    args = parser.parse_args()
    try:
        result = validate_video(
            args.video, ffprobe_path=args.ffprobe,
            min_duration=args.min_duration, min_fps=args.min_fps,
            expected_width=args.expected_width,
            expected_height=args.expected_height)
    except Exception as exc:
        result = {
            "schema_version": SCHEMA_VERSION,
            "pass": False,
            "path": os.path.abspath(args.video),
            "errors": [str(exc)],
        }
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded + "\n")
    print(encoded)
    return 0 if result.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
