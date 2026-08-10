# IFSC vision and YOLO interface v1

`vision_detector.py` emits `ifsc.vision.detection.v1`. The authoritative JSON Schema is `contracts/vision_detection.schema.json`.

## Coordinate and confidence contract

- `bbox`: integer pixel coordinates `[x, y, width, height]`.
- `bbox_xyxy`: integer pixel coordinates `[x1, y1, x2, y2]`; `x2` and `y2` are exclusive.
- `confidence`: number in `[0, 1]` supplied by YOLO, or a documented visible-area score for the synthetic OpenCV landmark detector.
- `label`: canonical IFSC class after applying `--class-map`.
- `raw_label` and `class_id`: original YOLO model outputs when the backend is YOLO.
- Frame SHA-256 and UTC timestamp make every result traceable to its input.

Canonical classes currently accepted by the adapter are semantic goals (`start_pad`, `inspection_gate`, `delivery_zone`, `medical_station`) and bridge inspection classes (`road_crack`, `pest`, `plant`, `bird`). Unknown classes are excluded.

## Strict model behavior

An explicit `--yolo weights.pt` request is strict: missing `ultralytics`, invalid weights, or a load error returns a non-zero exit. Add `--allow-fallback` only when a demonstration explicitly permits falling back to synthetic OpenCV landmark detection. Empty YOLO results do not silently invoke another detector.

Example class map:

```json
{"crack": "road_crack", "vegetation": "plant"}
```

Example invocation:

```powershell
py -3 outputs\vision_detector.py --frame frame.png --yolo best.pt `
  --class-map outputs\contracts\example_yolo_class_map.json `
  --jsonl-log outputs\logs\vision.jsonl
```

The JSONL file wraps each result in `ifsc.event.v1` with event type `vision.detection.completed`.
