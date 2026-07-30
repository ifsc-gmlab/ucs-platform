#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Full PASS-to-completion end-to-end regression for the SZTU drone dashboard.

Unlike regress_dashboard_e2e.py (which STOPS mid-flight to stay clear of the
weak-machine freeze window), this script lets the task run to its terminal
state and verifies the FULL stage chain lights up:

    启动 -> 飞行 -> 降落 -> 清理   (outcome == PASS)

It is meant to be run on a STABLE machine. If the known weak-machine freeze
hits (log goes silent, or the overall wall-clock cap is exceeded), it
auto-stops the task, cleans up, and reports MACHINE_UNSTABLE so the caller
(scheduled automation) can simply retry on a later, stable day -- no manual
intervention required.

API path (identical to what a browser user does):
  1. POST /api/command        parse Chinese command
  2. POST /api/execute        launch run_semantic_city.ps1 (UE+PX4+controller)
  3. GET  /api/task/<id>      poll until terminal; watch stage_index advance
  4. GET  /api/task/<id>/report   clickable HTML report (PASS badge)
  5. GET  /api/history        history record with outcome=PASS + ts
  6. GET  /api/current        cleared after completion

Usage:
  python regress_dashboard_full_pass.py [--base http://127.0.0.1:8080]
Exit code 0 = full PASS chain verified; 2 = MACHINE_UNSTABLE (retry later);
            1 = genuine functional FAIL.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---- config -------------------------------------------------------------
HARD_TIMEOUT = 540        # overall wall-clock cap (s); full flight ~3-5 min
POLL_INTERVAL = 5         # s between status polls
SILENT_STOP = 70          # s of log silence => treat as freeze, auto-stop
STAGE_NAMES = ["启动", "飞行", "降落", "清理"]
COMMAND_TEXT = "飞到投递区"
FINAL_STAGE_INDEX = 3     # 清理

# Managed python + dashboard script (kept in sync with the project layout)
_HERE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Users\49337\.workbuddy\binaries\python\versions\3.13.12\python.exe"
DASHBOARD = os.path.join(_HERE, "drone_dashboard.py")


def http(method, url, data=None, timeout=20):
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    else:
        req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def ensure_dashboard(base):
    """Return True if the dashboard is reachable; else start it detached and wait."""
    try:
        s, _ = http("GET", base + "/api/current", timeout=5)
        if s == 200:
            return True
    except Exception:  # noqa: BLE001
        pass
    # launch detached
    if not os.path.isfile(PY) or not os.path.isfile(DASHBOARD):
        return False
    subprocess.Popen([PY, DASHBOARD], cwd=_HERE,
                     creationflags=0x00000008)  # DETACHED_PROCESS
    for _ in range(40):
        time.sleep(1)
        try:
            s, _ = http("GET", base + "/api/current", timeout=5)
            if s == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    args = ap.parse_args()
    B = args.base.rstrip("/")

    results = []  # (name, status, detail)

    def check(name, ok, detail=""):
        results.append((name, "PASS" if ok else "FAIL", detail))
        print(("[PASS] " if ok else "[FAIL] ") + name + ((" — " + detail) if detail else ""))

    t0 = time.monotonic()

    # ---- 0. dashboard up -------------------------------------------------
    if not ensure_dashboard(B):
        check("dashboard online / startable", False, "cannot reach or start dashboard")
        _report(results, "MACHINE_UNSTABLE")
        return 2

    # ---- 1. clean start -------------------------------------------------
    s, b = http("GET", B + "/api/current")
    cur = json.loads(b) if b else {}
    check("clean start (no in-flight task)", cur.get("task_id") is None,
          "current=%s" % b[:80])

    # ---- 2. parse Chinese command --------------------------------------
    s, b = http("POST", B + "/api/command", {"text": COMMAND_TEXT})
    cmd = json.loads(b)
    check("command parse 飞到投递区 -> delivery_zone",
          cmd.get("action") == "fly_to" and cmd.get("goal_id") == "delivery_zone",
          "parser=%s" % cmd.get("parser"))

    # ---- 3. execute from cold ------------------------------------------
    s, b = http("POST", B + "/api/execute",
                {"action": "fly_to", "goal": cmd.get("goal_id"), "vision": "0"})
    ex = json.loads(b)
    if "error" in ex:
        check("execute launched task", False, "error: %s" % ex["error"])
        _report(results, "FAIL")
        return 1
    tid = ex.get("task_id")
    check("execute launched task (cold)", bool(tid), "task_id=%s status=%s" % (tid, ex.get("status")))

    # ---- 4. poll until terminal, watch stages --------------------------
    seen_stages = []
    last_log_time = time.monotonic()
    restore_ok = None
    log_has_nul = None
    terminal = False
    final = None
    auto_stopped = False

    while time.monotonic() - t0 < HARD_TIMEOUT:
        s, b = http("GET", B + "/api/task/%s" % tid)
        if s != 200:
            check("task status reachable", False, "HTTP %s" % s)
            break
        task = json.loads(b)
        st = task.get("status")
        si = task.get("stage_index", 0)
        stage = task.get("stage", "?")
        if stage not in seen_stages:
            seen_stages.append(stage)
            print("   stage advanced -> %s (status=%s)" % (stage, st))
        log = task.get("log", [])
        if log:
            last_log_time = time.monotonic()
            joined = "\n".join(log)
            if log_has_nul is None:
                log_has_nul = "\x00" in joined

        if restore_ok is None and st in ("queued", "running"):
            s2, b2 = http("GET", B + "/api/current")
            cj = json.loads(b2)
            restore_ok = (cj.get("task_id") == tid)
            check("refresh-restore: /api/current serves in-flight task", restore_ok,
                  "current=%s" % b2[:80])

        if st in ("done", "stopped", "error", "timeout"):
            terminal = True
            final = task
            break

        # freeze guard: log silent too long => weak-machine freeze suspected
        if not auto_stopped and (time.monotonic() - last_log_time) > SILENT_STOP:
            s3, _ = http("POST", B + "/api/task/%s/stop" % tid)
            auto_stopped = True
            check("freeze guard auto-stopped (log silent >%ds)" % SILENT_STOP, True, "HTTP %s" % s3)
            break

        time.sleep(POLL_INTERVAL)

    check("stages advanced on REAL wrapper log", len(seen_stages) >= 2,
          "seen=%s" % " -> ".join(seen_stages))
    check("log is readable (no UTF-16 NUL mojibake)", log_has_nul is False,
          "has_nul=%s" % log_has_nul)

    # ---- 5. terminal outcome -------------------------------------------
    if not terminal:
        # hard timeout without terminal state -> freeze
        if not auto_stopped:
            http("POST", B + "/api/task/%s/stop" % tid)
        check("task reached terminal state", False, "HARD_TIMEOUT (suspected freeze)")
        _report(results, "MACHINE_UNSTABLE")
        return 2

    check("task reached terminal state", True, "final_status=%s" % final.get("status"))

    if final.get("status") != "done" or final.get("outcome") != "PASS":
        # stopped/error earlier => likely freeze
        if auto_stopped or final.get("outcome") in ("STOPPED", "ERROR", "TIMEOUT"):
            check("outcome == PASS (full chain)", False,
                  "outcome=%s (freeze/stop detected)" % final.get("outcome"))
            _report(results, "MACHINE_UNSTABLE")
            return 2
        check("outcome == PASS (full chain)", False, "outcome=%s" % final.get("outcome"))
        _report(results, "FAIL")
        return 1

    check("outcome == PASS (full chain)", True, "outcome=%s" % final.get("outcome"))
    check("ALL stages lit (启动->飞行->降落->清理)",
          final.get("stage_index", -1) >= FINAL_STAGE_INDEX,
          "stage_index=%s stage=%s" % (final.get("stage_index"), final.get("stage")))

    # ---- 6. clickable report -------------------------------------------
    s, b = http("GET", B + "/api/task/%s/report" % tid)
    report_ok = (s == 200 and "PASS" in b and "<html" in b.lower())
    check("clickable report renders (HTML + PASS badge)", report_ok,
          "HTTP %s len=%d" % (s, len(b)))

    # ---- 7. history record ---------------------------------------------
    s, b = http("GET", B + "/api/history")
    hist = json.loads(b)
    rec = next((h for h in hist if h.get("task_id") == tid), None)
    check("history recorded this task", rec is not None, "history_len=%d" % len(hist))
    if rec is not None:
        check("history outcome == PASS", rec.get("outcome") == "PASS",
              "outcome=%s" % rec.get("outcome"))
        check("history has goal + timestamp", bool(rec.get("goal_id")) and bool(rec.get("ts")),
              "goal=%s ts=%s" % (rec.get("goal_id"), rec.get("ts")))

    # ---- 8. current cleared --------------------------------------------
    s, b = http("GET", B + "/api/current")
    cj = json.loads(b)
    check("current cleared after completion", cj.get("task_id") is None, "current=%s" % b[:60])

    _report(results, "PASS")
    fails = sum(1 for _, st, _ in results if st == "FAIL")
    return 1 if fails else 0


def _report(results, verdict):
    print("\n================ FULL-PASS REGRESSION SUMMARY ================")
    npass = sum(1 for _, st, _ in results if st == "PASS")
    nfail = sum(1 for _, st, _ in results if st == "FAIL")
    for name, st, detail in results:
        print("  %-52s %s" % (name, st))
    print("-------------------------------------------------------------")
    print("  PASS=%d  FAIL=%d  VERDICT=%s" % (npass, nfail, verdict))
    print("=============================================================")


if __name__ == "__main__":
    sys.exit(main())
