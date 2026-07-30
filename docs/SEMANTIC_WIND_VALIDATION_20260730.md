# Semantic wind validation - 2026-07-30

## Final result

- 3 m/s: five non-regressed scenarios passed in the full suite; the initial
  delivery-vision scenario exposed low-speed yaw drift. After fixing target
  heading hold, that scenario passed independently with 8/8 confirming frames,
  zero collisions, and 0.0197 m final error.
- 5 m/s: 6/6 plans passed, all regression gates passed, zero collisions,
  0.0240 m average final error, and 0.0431 m maximum final error.
- 8 m/s: 6/6 plans passed, all regression gates passed, zero collisions,
  0.0352 m average final error, and 0.0532 m maximum final error.

## Evidence

- 3 m/s post-fix vision regression:
  `semantic-regression/20260730-171945/summary.md`
- 5/8 m/s full matrix:
  `semantic-wind-matrix/20260730-172405/summary.md`
- 5 m/s full regression:
  `semantic-regression/20260730-172405/summary.md`
- 8 m/s full regression:
  `semantic-regression/20260730-174554/summary.md`
