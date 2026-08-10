#!/usr/bin/env python3
"""Offline regression checks for the IFSC vision/YOLO v1 contract."""

import json
import os
import sys
import tempfile

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import ifsc_event_log
import vision_detector as vd


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS", name)


class FakeBox:
    def __init__(self, cls_id, xyxy, confidence):
        self.cls = np.array([cls_id])
        self.xyxy = np.array([xyxy], dtype=float)
        self.conf = np.array([confidence], dtype=float)


class FakeResult:
    names = {0: "crack", 1: "unknown"}

    def __init__(self):
        self.boxes = [
            FakeBox(0, [10, 12, 60, 72], 0.91),
            FakeBox(1, [0, 0, 40, 40], 0.99),
        ]


class FakeModel:
    def __call__(self, frame, verbose=False):
        return [FakeResult()]


frame = np.zeros((180, 320, 3), dtype=np.uint8)
cv2.rectangle(frame, (40, 50), (140, 130), (0, 255, 255), -1)
opencv_detections = vd.detect_with_opencv(frame, min_area_frac=0.001)
start = next((item for item in opencv_detections if item["label"] == "start_pad"), None)
check("OpenCV canonical landmark", start is not None)
check("OpenCV bbox xywh", start["bbox"][2] > 0 and start["bbox"][3] > 0)
check("OpenCV bbox xyxy", start["bbox_xyxy"][2] > start["bbox_xyxy"][0])
check("OpenCV backend explicit", start["backend"] == "opencv_landmark")

yolo_detections = vd.detect_with_yolo(
    frame, FakeModel(), min_area_frac=0.001,
    class_map={"crack": "road_crack"})
check("YOLO class map", len(yolo_detections) == 1 and yolo_detections[0]["label"] == "road_crack")
check("YOLO unknown class excluded", all(item["raw_label"] != "unknown" for item in yolo_detections))
check("YOLO class id preserved", yolo_detections[0]["class_id"] == 0)
check("YOLO confidence preserved", yolo_detections[0]["confidence"] == 0.91)

with tempfile.TemporaryDirectory() as temp:
    frame_path = os.path.join(temp, "frame.png")
    log_path = os.path.join(temp, "vision.jsonl")
    check("test frame written", cv2.imwrite(frame_path, frame))
    envelope = vd.detection_envelope(
        frame_path, frame, yolo_detections, "yolo", model_name="fake.pt")
    check("schema version", envelope["schema_version"] == vd.VISION_SCHEMA_VERSION)
    check("frame hash", len(envelope["frame_sha256"]) == 64)
    check("image size", envelope["image_size"] == {"width": 320, "height": 180})
    event = ifsc_event_log.append_event(
        log_path, "vision.detection.completed", envelope, run_id="test")
    with open(log_path, "r", encoding="utf-8") as handle:
        logged = json.loads(handle.readline())
    check("JSONL event schema", logged["schema_version"] == "ifsc.event.v1")
    check("JSONL event id", logged["event_id"] == event["event_id"])
    check("JSONL payload traceable", logged["payload"]["frame_sha256"] == envelope["frame_sha256"])

schema_path = os.path.join(ROOT, "contracts", "vision_detection.schema.json")
with open(schema_path, "r", encoding="utf-8") as handle:
    schema = json.load(handle)
check("contract schema id", schema["properties"]["schema_version"]["const"] == vd.VISION_SCHEMA_VERSION)
check("contract requires detector", "detector" in schema["required"])
print("ALL_OK")
