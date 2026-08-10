#!/usr/bin/env python3
"""Generate a small deterministic clip and test recording validation gates."""

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import verify_video_artifact as validator


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS", name)


ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
ffprobe = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
check("ffmpeg available", bool(ffmpeg))
check("ffprobe available", bool(ffprobe))

with tempfile.TemporaryDirectory() as temp:
    video = os.path.join(temp, "fixture.mp4")
    command = [
        ffmpeg, "-y", "-f", "lavfi", "-i",
        "testsrc2=size=320x180:rate=25", "-t", "2.4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", video,
    ]
    completed = subprocess.run(command, capture_output=True, check=False)
    check("fixture generated", completed.returncode == 0 and os.path.isfile(video))
    result = validator.validate_video(
        video, ffprobe_path=ffprobe, min_duration=2.0,
        expected_width=320, expected_height=180, min_fps=20.0)
    check("valid clip passes", result["pass"])
    check("clip resolution", result["metrics"]["width"] == 320 and result["metrics"]["height"] == 180)
    check("clip frames", result["metrics"]["frame_count"] >= 50)
    check("clip hash", len(result["sha256"]) == 64)
    failed = validator.validate_video(
        video, ffprobe_path=ffprobe, min_duration=5.0,
        expected_width=1920, expected_height=1080, min_fps=30.0)
    check("bad expectations fail", not failed["pass"] and len(failed["errors"]) >= 3)

print("ALL_OK")
