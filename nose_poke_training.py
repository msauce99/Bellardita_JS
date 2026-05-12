"""
nose_poke_training.py

Standalone automatic nose-poke hold training.
Set terminal to R system: $env:JOYSTICK_SYSTEM_ID="system_R"

Behavior
--------
- No joystick reward logic, No camera triggering
- No manual reward triggering
- One Arduino nose-poke input
- One water dispenser
- Reward is delivered automatically when the mouse maintains a nose poke
  for the configured hold duration
- One reward maximum per continuous nose poke hold
- Mouse must leave and re-enter to start a new hold attempt

Arduino serial messages expected
--------------------------------
- NOSE_POKE:1   -> beam blocked / nose present
- NOSE_POKE:0   -> beam clear / no nose present
"""

import csv
import time
import serial

from configurations import (
    DEBUG,
    ARD_SERIAL_PORT, DEFAULT_BAUD, DEFAULT_TIMEOUT_S,
    WD_IN_USE,
    NOSE_POKE_IN_USE,
    NOSE_POKE_TRAINING_STAGE,
    NOSE_POKE_HOLD_TIME_S,
    NOSE_POKE_TRAINING_DURATION_S,
    Run, SYSTEM_ID, SYSTEM_NAME,
    CSV_DELIMITER,
)
from water_dispenser import WaterDispenser, try_deliver_reward, log_nose_poke_event, log_lick_event, write_session_summary_csv, _wait_for_peak_windows
from helpers import make_unique_csv_name


def _print_summary(shared_state):
    reward_summary = shared_state.get("reward_summary", [])
    hold_durations = shared_state.get("nose_poke_reward_hold_durations", [])
    poke_count = shared_state.get("poke_count", 0)
    rewarded_count = len(reward_summary)

    valid_holds = [h for h in hold_durations if h is not None]
    avg_hold = sum(valid_holds) / len(valid_holds) if valid_holds else 0.0
    longest_hold = max(valid_holds) if valid_holds else 0.0

    print(f"\n{'='*100}")
    print(f"[NOSE POKE TRAINING] SESSION SUMMARY  |  Stage {NOSE_POKE_TRAINING_STAGE}  |  Hold requirement: {NOSE_POKE_HOLD_TIME_S:.2f} s")
    print(f"{'='*100}")
    print(f"  Total pokes:      {poke_count}")
    print(f"  Rewarded pokes:   {rewarded_count}")
    print(f"  Avg hold time:    {avg_hold:.3f} s")
    print(f"  Longest hold:     {longest_hold:.3f} s")
    if reward_summary:
        print(f"\n{'#':<4} {'Type':<28} {'Time (s)':<12} {'Hold (s)':<10}")
        print(f"{'-'*58}")
        for i, ((t_rel, reason, _pos_mm), hold_s) in enumerate(zip(reward_summary, hold_durations), 1):
            t_str = f"{t_rel:<12.3f}" if t_rel is not None else f"{'N/A':<12}"
            hold_str = f"{hold_s:<10.3f}" if hold_s is not None else f"{'N/A':<10}"
            print(f"{i:<4} {reason:<28} {t_str} {hold_str}")
    else:
        print("\n  No rewards delivered.")
    print(f"{'='*100}\n")


def _append_np_stats_to_summary_csv(shared_state):
    """Append session-level statistics as labelled rows at the end of the summary CSV."""
    summary_csv_path = shared_state.get("summary_csv_path")
    if not summary_csv_path:
        return

    hold_durations = shared_state.get("nose_poke_reward_hold_durations", [])
    poke_count = shared_state.get("poke_count", 0)
    rewarded_count = len(shared_state.get("reward_summary", []))
    valid_holds = [h for h in hold_durations if h is not None]
    avg_hold = sum(valid_holds) / len(valid_holds) if valid_holds else 0.0
    longest_hold = max(valid_holds) if valid_holds else 0.0

    def _fmt(v):
        return f"{v:.4f}".replace(".", ",") if isinstance(v, float) else v

    try:
        with open(summary_csv_path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, delimiter=CSV_DELIMITER)
            writer.writerow([])  # blank separator
            writer.writerow(["", "session_stat", poke_count,          "total_pokes",    "", "", "", "", "", ""])
            writer.writerow(["", "session_stat", rewarded_count,       "rewarded_pokes", "", "", "", "", "", ""])
            writer.writerow(["", "session_stat", _fmt(avg_hold),       "avg_hold_s",     "", "", "", "", "", ""])
            writer.writerow(["", "session_stat", _fmt(longest_hold),   "longest_hold_s", "", "", "", "", "", ""])
    except Exception as e:
        print(f"[ERROR] Failed to append stats to summary CSV: {e}")


def main():
    if not NOSE_POKE_IN_USE:
        print("[ERROR] NOSE_POKE_IN_USE=False in configurations.py")
        return

    if not WD_IN_USE:
        print("[ERROR] WD_IN_USE=False in configurations.py")
        print("[ERROR] Standalone nose-poke training requires the water dispenser.")
        return

    print("\n" + "=" * 120)
    print(f"NOSE POKE TRAINING  |  {SYSTEM_NAME} ({SYSTEM_ID})")
    print("-" * 120)
    print(f"- Arduino serial: {ARD_SERIAL_PORT}")
    print(f"- Hold stage: {NOSE_POKE_TRAINING_STAGE}")
    print(f"- Required hold time: {NOSE_POKE_HOLD_TIME_S:.2f} s")
    print(f"- Session duration: {NOSE_POKE_TRAINING_DURATION_S/60:.0f} min")
    print("- Press Ctrl+C to stop the session early.")
    print("=" * 120)

    shared_state = {
        "latest_raw": None,
        "latest_mm": None,
        "latest_sample": None,
        "sample_counter": 0,
        "trial_in_progress": False,
        "trial_complete": False,
        "t0": None,
        "shutdown": False,
        "wd": None,
        "last_reward_t": -1e9,
        "reward_summary": [],
        "reward_peak_data": [],
        "_peak_threads": [],
        "lick_summary": [],
        "nose_poke_reward_hold_durations": [],
        "reward_zone_hits": 0,
        "reward_armed": True,
        "current_run_label": Run,
        "session_baseline": None,
        "trial_csv_file": None,
        "events_csv_fh": None,
        "events_csv_writer": None,
        "events_csv_file": None,
        "camera_running": False,
        "poke_count": 0,
        "nose_poke_present": False,
        "nose_poke_prev": False,
        "nose_poke_event_t": None,
        "nose_poke_required": False,
        "nose_poke_satisfied": False,
        "nose_poke_start_t": None,
        "nose_poke_duration_s": 0.0,
        "nose_poke_reward_given": False,
        "system_id": SYSTEM_ID,
        "system_name": SYSTEM_NAME,
    }

    ard_ser = None
    wd = None

    try:
        # ------------------------------------------------------------
        # Open Arduino serial
        # ------------------------------------------------------------
        ard_ser = serial.Serial(ARD_SERIAL_PORT, DEFAULT_BAUD, timeout=DEFAULT_TIMEOUT_S)
        ard_ser.set_buffer_size(rx_size=65536)
        shared_state["ard_ser"] = ard_ser
        print(f"[SYSTEM] {SYSTEM_NAME} ({SYSTEM_ID}) using Arduino {ARD_SERIAL_PORT}")

        # ------------------------------------------------------------
        # Open water dispenser
        # ------------------------------------------------------------
        wd = WaterDispenser()
        shared_state["wd"] = wd

        print("[WD] Verifying calibration factor and lick threshold before session start...")
        ok = wd.verify_setup()
        if not ok:
            print("[ERROR] Water dispenser verification failed.")
            return
        print("[WD] Verification complete. Water dispenser is ready.")
        wd.reset_lick_counter()
        print("[WD] Lick counter reset after verification — session starts at zero.")

        # ------------------------------------------------------------
        # Open events CSV
        # ------------------------------------------------------------
        events_csv_path = make_unique_csv_name("event_logging", run_label=Run)
        events_fh = open(events_csv_path, "w", newline="", encoding="utf-8")
        events_writer = csv.writer(events_fh, delimiter=CSV_DELIMITER)
        events_writer.writerow([
            "trigger_t_s", "dispense_t_s",
            "sample_idx", "sample_t_s",
            "event_type", "reason",
            "x_mm", "y_mm",
            "delta_x_mm", "delta_y_mm", "euclidean_dist_mm",
            "hold_duration_s",
            "peak_dist_mm_after_reward",
            "peak_x_mm_after_reward", "peak_y_mm_after_reward",
            "peak_time_s_after_reward", "time_to_peak_s",
        ])
        shared_state["events_csv_fh"] = events_fh
        shared_state["events_csv_writer"] = events_writer
        shared_state["events_csv_file"] = str(events_csv_path)

        summary_csv_path = make_unique_csv_name("session_summary", run_label=Run)
        shared_state["summary_csv_path"] = str(summary_csv_path)

        # ------------------------------------------------------------
        # Start session on Arduino
        # ------------------------------------------------------------
        ard_ser.write(b"S")
        ard_ser.flush()

        shared_state["t0"] = time.perf_counter()
        shared_state["trial_in_progress"] = True
        shared_state["trial_complete"] = False
        last_countdown_print_t = 0.0

        print("[INFO] Nose-poke training session started.")
        print("[INFO] Waiting for NOSE_POKE:1 / NOSE_POKE:0 events from Arduino...")
        print("[INFO] (NOSE_POKE:1 = beam blocked / nose present | NOSE_POKE:0 = beam clear)")

        while True:
            raw = ard_ser.readline()

            # End session after the configured duration, with periodic countdown prints.
            elapsed = time.perf_counter() - shared_state["t0"]
            if elapsed >= NOSE_POKE_TRAINING_DURATION_S:
                print(f"\n[INFO] Session time limit reached ({NOSE_POKE_TRAINING_DURATION_S/60:.0f} min). Ending session.")
                break
            remaining = NOSE_POKE_TRAINING_DURATION_S - elapsed
            # Print every 5 min when >5 min remain; every 1 min in the last 5 min.
            interval = 60.0 if remaining <= 300.0 else 300.0
            if elapsed - last_countdown_print_t >= interval:
                mins = int(remaining // 60)
                secs = int(remaining % 60)
                print(f"[TIMER] {mins:02d}:{secs:02d} remaining in session.")
                last_countdown_print_t = elapsed

            # Check hold timer on every iteration — not just on serial timeout.
            # The Arduino streams joystick data at 1kHz so readline() rarely times out,
            # meaning a timeout-only check would never fire during active streaming.
            if shared_state.get("nose_poke_present", False):
                start_t = shared_state.get("nose_poke_start_t", None)
                t0 = shared_state.get("t0", None)
                if start_t is not None and t0 is not None:
                    now_rel = time.perf_counter() - t0
                    hold_s = max(0.0, now_rel - start_t)
                    shared_state["nose_poke_duration_s"] = hold_s

                    if (hold_s >= NOSE_POKE_HOLD_TIME_S) and (not shared_state.get("nose_poke_reward_given", False)):
                        delivered, msg = try_deliver_reward(
                            shared_state,
                            reason=f"nose_poke_hold_stage_{NOSE_POKE_TRAINING_STAGE}"
                        )
                        if delivered:
                            shared_state["reward_zone_hits"] = shared_state.get("reward_zone_hits", 0) + 1
                            shared_state["nose_poke_reward_given"] = True
                            print(f"[NOSE] Hold requirement met ({hold_s:.2f}s). {msg}")
                        else:
                            shared_state["nose_poke_reward_given"] = True
                            print(f"[NOSE] {msg} — will retry on next poke.")

            if wd is not None:
                try:
                    lick_delta = wd.get_update_lick()
                    if lick_delta > 0:
                        log_lick_event(shared_state, lick_delta)
                except Exception:
                    pass

            if not raw:
                continue

            line_raw = raw.decode(errors="ignore").strip()
            if not line_raw:
                continue

            if line_raw.startswith("NOSE_POKE:"):
                print(f"[SERIAL] Received: {line_raw!r}")

                value = line_raw.split(":", 1)[1].strip()
                nose_present = (value == "1")

                t0 = shared_state.get("t0", None)
                t_rel = (time.perf_counter() - t0) if t0 is not None else None

                prev_present = shared_state.get("nose_poke_present", False)
                shared_state["nose_poke_prev"] = prev_present
                shared_state["nose_poke_present"] = nose_present
                shared_state["nose_poke_event_t"] = t_rel

                if nose_present:
                    shared_state["poke_count"] = shared_state.get("poke_count", 0) + 1
                    poke_n = shared_state["poke_count"]
                    shared_state["nose_poke_start_t"] = t_rel
                    shared_state["nose_poke_duration_s"] = 0.0
                    shared_state["nose_poke_satisfied"] = True
                    shared_state["nose_poke_reward_given"] = False
                    print(f"[NOSE] >>> Poke #{poke_n} DETECTED at t={t_rel:.3f}s  (reward armed)")
                else:
                    start_t = shared_state.get("nose_poke_start_t", None)
                    if start_t is not None and t_rel is not None:
                        shared_state["nose_poke_duration_s"] = max(0.0, t_rel - start_t)
                    rewarded = shared_state.get("nose_poke_reward_given", False)
                    if rewarded:
                        hold_durations = shared_state.get("nose_poke_reward_hold_durations", [])
                        if hold_durations:
                            hold_durations[-1] = shared_state["nose_poke_duration_s"]
                    print(
                        f"[NOSE] <<< Nose CLEARED at t={t_rel:.3f}s  |  "
                        f"hold={shared_state['nose_poke_duration_s']:.3f}s  |  "
                        f"rewarded={'YES' if rewarded else 'NO'}"
                    )
                    shared_state["nose_poke_start_t"] = None
                    shared_state["nose_poke_satisfied"] = False
                    shared_state["nose_poke_reward_given"] = False
                    print(f"[NOSE] Reward REARMED — waiting for next poke (total pokes this session: {shared_state.get('poke_count', 0)})")

                if nose_present != prev_present:
                    log_nose_poke_event(
                        shared_state,
                        nose_present=nose_present,
                        event_t_rel=t_rel,
                        reason="present" if nose_present else "clear",
                    )
                continue

            # Ignore joystick samples and any other lines in this standalone script

    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user. Ending session...")
    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        try:
            if ard_ser is not None and ard_ser.is_open:
                ard_ser.write(b"E")
                ard_ser.flush()
        except Exception:
            pass

        try:
            if ard_ser is not None and ard_ser.is_open:
                ard_ser.close()
        except Exception:
            pass

        _wait_for_peak_windows(shared_state)
        try:
            if shared_state.get("events_csv_fh") is not None:
                shared_state["events_csv_fh"].flush()
                shared_state["events_csv_fh"].close()
        except Exception:
            pass

        try:
            if wd is not None:
                wd.close()
        except Exception:
            pass

        shared_state["trial_in_progress"] = False
        shared_state["trial_complete"] = True

        _print_summary(shared_state)
        write_session_summary_csv(shared_state)
        _append_np_stats_to_summary_csv(shared_state)

        events_file = shared_state.get("events_csv_file", None)
        if events_file:
            print(f"[INFO] Events CSV saved to: {events_file}")


if __name__ == "__main__":
    main()