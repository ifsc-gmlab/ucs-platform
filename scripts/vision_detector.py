#!/usr/bin/env python3
"""Synthetic visual landmark confirmation for the UE semantic-city drone pipeline.

The Unreal drone pawn renders the onboard camera to a render target and saves
PNG frames to ``Saved/CameraFrames/<runId>/`` (Windows) at ~1 Hz. This module
reads the frames and detects the colored landmark markers that the C++ side
spawns near each semantic goal:

    start_pad       -> yellow
    inspection_gate -> blue
    delivery_zone   -> green
    medical_station -> red

Detection is pure OpenCV HSV color thresholding + contour area filtering, so it
needs no model download and runs in real time even on CPU. The returned
detections carry a semantic ``label`` (goal id) plus a bounding box and a
confidence derived from the visible blob area.

IMPORTANT: this is SYNTHETIC VISUAL LANDMARK CONFIRMATION — it only recognizes
the deliberately colored markers the simulator places in the world. It is NOT a
general visual-semantic model (it cannot understand arbitrary scenes/objects).
A general model (e.g. YOLOv8) remains a future upgrade, supported via
``--yolo <weights>`` if ``ultralytics`` is importable.
"""

import argparse
import glob
import json
import os
import sys
import time

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("ERROR: opencv-python is required (pip install opencv-python-headless)\n")
    raise


# Goal id -> BGR lower/upper HSV bounds. Tuned for the emissive BasicShape
# material colors used by the C++ SpawnSemanticLandmarks(). Saturated, distinct
# hues so they pop against the urban scene ground/building.
# HSV ranges are (H, S, V) with H in [0,179], S/V in [0,255].
LANDMARK_COLORS = {
    "start_pad":       {"hsv_lower": (20, 120, 120), "hsv_upper": (35, 255, 255)},   # yellow
    "inspection_gate": {"hsv_lower": (95, 120, 120), "hsv_upper": (125, 255, 255)},  # blue
    "delivery_zone":   {"hsv_lower": (30, 70, 90),   "hsv_upper": (100, 255, 255)},  # green (widened for emissive+lighting+standoff distance)
    "medical_station": {"hsv_lower": (0, 120, 120),  "hsv_upper": (12, 255, 255)},   # red (low H)
}

# Red wraps around the HSV hue circle; add a second band.
LANDMARK_COLORS["medical_station_red2"] = {
    "hsv_lower": (168, 120, 120), "hsv_upper": (179, 255, 255),
    "_alias": "medical_station",
}

DEFAULT_MIN_AREA_FRAC = 0.002  # min blob area as fraction of frame area (lowered for distant/standoff markers)


def detect_with_opencv(frame, min_area_frac=DEFAULT_MIN_AREA_FRAC):
    """Return a list of detections from an BGR numpy frame.

    Each detection: dict(label, color, bbox=[x,y,w,h], area, confidence).
    """
    h, w = frame.shape[:2]
    min_area = max(50, int(h * w * min_area_frac))
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    detections = []
    for key, spec in LANDMARK_COLORS.items():
        label = spec.get("_alias", key)
        lower = np.array(spec["hsv_lower"], dtype=np.uint8)
        upper = np.array(spec["hsv_upper"], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)
        # De-noise.
        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area:
                continue
            x, y, cw, ch = cv2.boundingRect(cnt)
            cx, cy = x + cw / 2.0, y + ch / 2.0
            # Confidence: fraction of the largest possible centered blob,
            # so a large, clearly-visible marker scores higher.
            confidence = float(min(1.0, area / (h * w * 0.20)))
            detections.append({
                "label": label,
                "color": label,
                "bbox": [int(x), int(y), int(cw), int(ch)],
                "center": [float(cx), float(cy)],
                "area": int(area),
                "confidence": round(confidence, 3),
            })

    # Merge duplicate labels (e.g. red two-band) keeping the strongest.
    best = {}
    for d in detections:
        cur = best.get(d["label"])
        if cur is None or d["confidence"] > cur["confidence"]:
            best[d["label"]] = d
    return list(best.values())


def detect_with_yolo(frame, model, min_area_frac=DEFAULT_MIN_AREA_FRAC):
    """Optional YOLOv8-nano delegate. Requires ultralytics + a trained/labeled
    model. Falls back to OpenCV detection if YOLO finds nothing."""
    h, w = frame.shape[:2]
    results = model(frame, verbose=False)[0]
    detections = []
    for box in results.boxes:
        cls_id = int(box.cls[0])
        name = results.names[cls_id]
        if name not in LANDMARK_COLORS:
            continue
        x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
        area = (x2 - x1) * (y2 - y1)
        min_area = max(50, int(h * w * min_area_frac))
        if area < min_area:
            continue
        conf = float(box.conf[0])
        detections.append({
            "label": name, "color": name,
            "bbox": [x1, y1, x2 - x1, y2 - y1],
            "center": [(x1 + x2) / 2.0, (y1 + y2) / 2.0],
            "area": int(area), "confidence": round(conf, 3),
        })
    if detections:
        return detections
    return detect_with_opencv(frame, min_area_frac)


def list_run_dirs(base):
    """Return timestamp-named UE vision run directories without statting every
    legacy flat PNG in ``base``. The directory may contain thousands of old
    ``frame_*.png`` files, and one ``isdir`` syscall per entry is prohibitively
    slow through WSL DrvFS."""
    if not os.path.isdir(base):
        return []
    names = [
        name for name in os.listdir(base)
        if len(name) == 15 and name[8] == "_"
        and name[:8].isdigit() and name[9:].isdigit()
    ]
    return [
        os.path.join(base, name) for name in names
        if os.path.isdir(os.path.join(base, name))
    ]


def find_latest_run_dir(base):
    """UE writes frames into <base>/<runId>/frame_*.png (one dir per mission).
    Return the newest run subdir if any exist, else the base dir itself."""
    if not os.path.isdir(base):
        return base
    pointer = os.path.join(base, "current_run.txt")
    try:
        with open(pointer, "r", encoding="utf-8") as handle:
            run_id = handle.read().strip()
    except OSError:
        run_id = ""
    if (
        len(run_id) == 15 and run_id[8] == "_"
        and run_id[:8].isdigit() and run_id[9:].isdigit()
    ):
        pointed = os.path.join(base, run_id)
        if os.path.isdir(pointed):
            return pointed
    subdirs = list_run_dirs(base)
    if not subdirs:
        return base
    return max(subdirs)


def latest_frame(path):
    """Return (filename, BGR frame) for the most recently written PNG, or
    (None, None) if no frame is available yet. Resolves the latest run subdir."""
    if not os.path.isdir(path):
        return None, None
    run_dir = find_latest_run_dir(path)
    ring_files = [
        os.path.join(run_dir, "latest_%02d.png" % slot)
        for slot in range(8)
    ]
    files = [filename for filename in ring_files if os.path.isfile(filename)]
    if not files:
        files = glob.glob(os.path.join(run_dir, "frame_*.png"))
    if not files:
        return None, None
    newest = max(files, key=os.path.getmtime)
    img = cv2.imread(newest)
    if img is None:
        return None, None
    return newest, img


def recent_frames(base, window_sec=1.5, now=None, max_frames=8):
    """Return list of (filename, BGR frame) for PNGs written within the last
    ``window_sec`` seconds (multi-frame window for robust voting).

    Frame names contain a monotonically increasing counter, so sort by name and
    stat only the newest bounded set. Calling ``getmtime`` for every file under
    a Windows drive mounted in WSL is extremely slow and can otherwise stall the
    OFFBOARD control loop for tens of seconds once a run accumulates many PNGs.
    """
    if not os.path.isdir(base):
        return []
    run_dir = find_latest_run_dir(base)
    ring_files = [
        os.path.join(run_dir, "latest_%02d.png" % slot)
        for slot in range(8)
    ]
    files = [filename for filename in ring_files if os.path.isfile(filename)]
    if not files:
        files = glob.glob(os.path.join(run_dir, "frame_*.png"))
    if not files:
        return []
    files.sort(reverse=True)
    if max_frames is not None and max_frames > 0:
        files = files[:max_frames]
    now = time.time() if now is None else now
    out = []
    for f in files:
        if now - os.path.getmtime(f) <= window_sec:
            img = cv2.imread(f)
            if img is not None:
                out.append((f, img))
    out.sort(key=lambda x: os.path.basename(x[0]))
    return out


def aggregate_detections(frames, min_area_frac=DEFAULT_MIN_AREA_FRAC):
    """Run single-frame detection on each frame and vote across frames.

    Returns a list of detections, one per distinct label, carrying the strongest
    single-frame confidence plus ``frames_seen`` (how many of the supplied frames
    contained that label). The controller uses ``frames_seen`` to require the
    expected landmark to appear in a majority of recent frames before accepting
    arrival — this replaces the brittle single-latest-frame check.
    """
    best = {}
    counts = {}
    for _, frame in frames:
        for d in detect_with_opencv(frame, min_area_frac):
            label = d["label"]
            counts[label] = counts.get(label, 0) + 1
            cur = best.get(label)
            if cur is None or d["confidence"] > cur["confidence"]:
                best[label] = d
    result = []
    for label, det in best.items():
        merged = dict(det)
        merged["frames_seen"] = counts[label]
        result.append(merged)
    return result


def annotate(frame, detections):
    """Draw bounding boxes + labels onto a copy of the frame."""
    out = frame.copy()
    for d in detections:
        x, y, w, h = d["bbox"]
        cv2.rectangle(out, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(out, "%s %.2f" % (d["label"], d["confidence"]),
                    (x, max(0, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (0, 255, 0), 2)
    return out


def main():
    parser = argparse.ArgumentParser(description="UE semantic landmark detector")
    parser.add_argument("--frame", help="single frame image to analyze")
    parser.add_argument("--camera-dir",
                        default=r"D:\SZTUWindFieldPX4Demo\Unreal\Saved\CameraFrames",
                        help="directory watched for newest frame_*.png")
    parser.add_argument("--watch", type=float, default=0.0,
                        help="if >0, poll camera-dir for this many seconds")
    parser.add_argument("--yolo", help="optional YOLOv8 .pt weights")
    parser.add_argument("--out", help="optional annotated output image path")
    parser.add_argument("--min-area", type=float, default=DEFAULT_MIN_AREA_FRAC)
    args = parser.parse_args()

    model = None
    if args.yolo:
        try:
            from ultralytics import YOLO
            model = YOLO(args.yolo)
            sys.stderr.write("[vision] YOLO model loaded: %s\n" % args.yolo)
        except Exception as exc:  # pragma: no cover
            sys.stderr.write("[vision] YOLO unavailable (%s); using OpenCV\n" % exc)
            model = None

    def run_detection(frame):
        if model is not None:
            return detect_with_yolo(frame, model, args.min_area)
        return detect_with_opencv(frame, args.min_area)

    if args.frame:
        frame = cv2.imread(args.frame)
        if frame is None:
            sys.stderr.write("ERROR: cannot read frame %s\n" % args.frame)
            return 2
        dets = run_detection(frame)
        if args.out:
            cv2.imwrite(args.out, annotate(frame, dets))
        print(json.dumps({"frame": args.frame, "detections": dets}, ensure_ascii=False))
        return 0

    if args.watch > 0:
        deadline = time.monotonic() + args.watch
        last_seen = None
        while time.monotonic() < deadline:
            fname, frame = latest_frame(args.camera_dir)
            if frame is not None and fname != last_seen:
                last_seen = fname
                dets = run_detection(frame)
                print(json.dumps({"frame": fname, "detections": dets}, ensure_ascii=False),
                      flush=True)
            time.sleep(0.1)
        return 0

    # Single-shot newest-frame mode (used by the controller each nav tick).
    fname, frame = latest_frame(args.camera_dir)
    if frame is None:
        print(json.dumps({"frame": None, "detections": []}, ensure_ascii=False))
        return 0
    dets = run_detection(frame)
    if args.out:
        cv2.imwrite(args.out, annotate(frame, dets))
    print(json.dumps({"frame": fname, "detections": dets}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
