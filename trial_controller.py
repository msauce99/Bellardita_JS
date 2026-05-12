'''
Trial Controller - experiment mode (timed vs movement based trials)
Optional use of WD and camera trigger

Orchestrates trial flow:
- Wait for user to press Enter to start trial
- Trigger Arduino to start trial
- Start/stop camera triggering (immediately or based on joystick movement)
- Movement trials end after N reward-condition entries
- Signal trial completion to main thread for user prompt to restart or quit

'''
import time
import math

from configurations import (
    DEBUG, SYSTEM_ID,
    CAM_IN_USE, CAM_START_WITH_TRIAL, JOYSTICK_CAM_TRIGGER, JOYSTICK_CAM_TRIGGER_RADIUS_MM,
    TIME_TRIAL, MOVEMENT_TRIAL,
    TRIAL_DURATION_S,
    TRIAL_REWARD_MODE, TRIAL_REWARD_RADIUS_MM, TRIAL_BOX_X_MM, TRIAL_BOX_Y_MM,
    TRIAL_END_REWARD_ZONE_HITS, REWARD_REARM_RADIUS_MM,
    NOSE_POKE_REQUIRED,
)
from helpers import dist2
from camera import start_camera, stop_camera
from water_dispenser import try_deliver_reward, log_blocked_reward_event, write_session_summary_csv


def _start_trial_state(shared_state: dict):
    """Initialize per-trial state (Python source of truth)."""
    t0 = shared_state.get("t0", time.perf_counter())

    shared_state["trial_in_progress"] = True
    shared_state["trial_complete"] = False
    shared_state["trial_end_seen"] = False
    shared_state["reward_armed"] = True
    shared_state["nose_poke_block_prev"] = False
    shared_state["reward_prev_condition"] = False
    shared_state["trial_csv_file"] = None

    if DEBUG:
        print("[DEBUG] Trial START (Python).")
    return t0


def _end_trial_state(shared_state: dict, reason: str = ""):
    """Finalize per-trial state (Python source of truth)."""
    shared_state["trial_in_progress"] = False
    shared_state["trial_complete"] = True
    shared_state["trial_end_seen"] = True
    if DEBUG:
        print(f"[DEBUG] Trial END (Python). {reason}")


def _print_trial_reward_summary(shared_state):
    reward_summary = shared_state.get("reward_summary", [])
    baseline = shared_state.get("session_baseline", None)
    hits = shared_state.get("reward_zone_hits", 0)

    print(f"\n{'='*100}")
    print(
        f"[TRIAL] SESSION SUMMARY  |  Reward-zone hits counted: {hits}  |  Baseline: ({baseline[0]:.4f}, {baseline[1]:.4f})"
        if baseline else
        f"[TRIAL] SESSION SUMMARY  |  Reward-zone hits counted: {hits}  |  Baseline: N/A"
    )
    print(f"{'='*100}")
    if reward_summary:
        print(f"{'#':<4} {'Type':<22} {'Time (s)':<12} {'Delta X (mm)':<14} {'Delta Y (mm)':<14} {'Euclidean Dist (mm)':<20}")
        print(f"{'-'*86}")
        for i, (t_rel, reason, pos_mm) in enumerate(reward_summary, 1):
            if pos_mm is not None and baseline is not None:
                dx = pos_mm[0] - baseline[0]
                dy = pos_mm[1] - baseline[1]
                eucl = math.sqrt(dx**2 + dy**2)
                print(f"{i:<4} {reason:<22} {t_rel:<12.3f} {dx:<14.4f} {dy:<14.4f} {eucl:<20.4f}")
            else:
                print(f"{i:<4} {reason:<22} {t_rel:<12.3f} {'N/A':<14} {'N/A':<14} {'N/A':<20}")
    else:
        print("  No rewards delivered.")
    print(f"{'='*100}\n")


def _trial_reward_condition(pos, baseline):
    """
    Evaluate the configured trial reward condition relative to session baseline.

    Modes:
      - outside_radius: reward when distance from baseline >= TRIAL_REWARD_RADIUS_MM
      - box:            reward when inside stage-3 style box relative to baseline
    """
    if pos is None or baseline is None:
        return False

    dx = pos[0] - baseline[0]
    dy = pos[1] - baseline[1]
    dist_from_baseline_sq = dx**2 + dy**2

    if TRIAL_REWARD_MODE == "outside_radius":
        return dist_from_baseline_sq >= (TRIAL_REWARD_RADIUS_MM ** 2)

    elif TRIAL_REWARD_MODE == "box":
        return ((-TRIAL_BOX_X_MM <= dx <= TRIAL_BOX_X_MM) and (dy >= TRIAL_BOX_Y_MM))

    else:
        raise ValueError(f"Unsupported TRIAL_REWARD_MODE: {TRIAL_REWARD_MODE!r}")


def _handle_reward_logic(shared_state, pos, baseline):
    """
    Shared reward / reward-condition entry logic for timed and movement trials.

    Returns:
        reward_hit_counted (bool): True only when a new armed reward-condition entry is counted.
    """
    if pos is None or baseline is None:
        return False

    dx = pos[0] - baseline[0]
    dy = pos[1] - baseline[1]
    dist_from_baseline_sq = dx**2 + dy**2
    dist_mm = dist_from_baseline_sq ** 0.5
    if dist_mm > shared_state.get("max_dist_mm", 0.0):
        shared_state["max_dist_mm"] = dist_mm
    reward_condition = _trial_reward_condition(pos, baseline)

    if not shared_state.get("reward_armed", True):
        if dist_mm > shared_state.get("swing_max_mm", 0.0):
            shared_state["swing_max_mm"] = dist_mm
        if dist_from_baseline_sq <= (REWARD_REARM_RADIUS_MM ** 2):
            swing_max = shared_state.get("swing_max_mm", 0.0)
            shared_state["reward_armed"] = True
            shared_state["reward_prev_condition"] = False
            shared_state["swing_max_mm"] = 0.0
            if swing_max > 0:
                print(f"[REWARD][{SYSTEM_ID}] max_dist_achieved_post_reward={swing_max:.1f}mm")

    nose_gate_ok = True
    if NOSE_POKE_REQUIRED:
        nose_gate_ok = bool(shared_state.get("nose_poke_satisfied", False))

    prev_reward_condition = shared_state.get("reward_prev_condition", False)
    shared_state["reward_prev_condition"] = reward_condition

    if reward_condition and not prev_reward_condition and shared_state.get("reward_armed", True) and nose_gate_ok:
        prev_hit_count = shared_state.get("reward_zone_hits", 0)

        delivered, msg = try_deliver_reward(
            shared_state,
            reason=f"trial_reward_hit_{prev_hit_count + 1}"
        )
        shared_state["reward_armed"] = False
        shared_state["swing_max_mm"] = 0.0

        if delivered:
            hit_count = prev_hit_count + 1
            shared_state["reward_zone_hits"] = hit_count
            if DEBUG:
                print(msg)
                print(f"[TRIAL] Reward delivered: hit {hit_count}")
        else:
            if DEBUG:
                print(msg)
                print(f"[TRIAL] Reward-zone entry cooldown-blocked; hit count unchanged ({prev_hit_count})")
        return True

    prev_blocked = shared_state.get("nose_poke_block_prev", False)
    currently_blocked = reward_condition and shared_state.get("reward_armed", True) and not nose_gate_ok
    shared_state["nose_poke_block_prev"] = currently_blocked

    if currently_blocked and not prev_blocked:
        log_blocked_reward_event(shared_state, reason=f"no_nose_poke_{TRIAL_REWARD_MODE}")
        if DEBUG:
            print("[TRIAL] Reward condition met, but blocked because nose poke was not present.")

    return False


def run_trial_controller(shared_state, start_event):
    """
    Runs in a background thread so Matplotlib UI stays responsive.
    Waits for start_event to begin each trial.
    """
    shutdown_event = shared_state.get("shutdown")
    ard_ser = shared_state.get("ard_ser")

    while not (shutdown_event and shutdown_event.is_set()):
        if shutdown_event and shutdown_event.is_set():
            break

        start_event.wait(timeout=0.5)
        if shutdown_event and shutdown_event.is_set():
            break
        if not start_event.is_set():
            continue

        start_event.clear()
        t0 = _start_trial_state(shared_state)

        shared_state["camera_running"] = False
        baseline = shared_state.get("session_baseline", None)
        trial_start_pos = baseline if baseline is not None else shared_state.get("latest_mm", None)

        if DEBUG:
            print(f"[TRIAL] Reward mode: {TRIAL_REWARD_MODE}")
            print(f"[TRIAL] Nose poke required: {NOSE_POKE_REQUIRED}")
            if TRIAL_REWARD_MODE == "outside_radius":
                print(f"[TRIAL] Outside-radius threshold: {TRIAL_REWARD_RADIUS_MM} mm from session baseline")
            elif TRIAL_REWARD_MODE == "box":
                print(f"[TRIAL] Box threshold: X in [{-TRIAL_BOX_X_MM}, {TRIAL_BOX_X_MM}] mm and Y >= {TRIAL_BOX_Y_MM} mm relative to session baseline")

        if CAM_IN_USE and CAM_START_WITH_TRIAL and not JOYSTICK_CAM_TRIGGER:
            start_camera(shared_state)
            shared_state["camera_running"] = True
        elif CAM_IN_USE and JOYSTICK_CAM_TRIGGER:
            print("[INFO] Camera will start when joystick moves beyond set threshold.")

        try:
            if TIME_TRIAL:
                while True:
                    if shutdown_event and shutdown_event.is_set():
                        _end_trial_state(shared_state, reason="shutdown")
                        break

                    if not shared_state.get("trial_in_progress", False):
                        _end_trial_state(shared_state, reason="cancelled")
                        break

                    elapsed = time.perf_counter() - t0
                    if elapsed >= TRIAL_DURATION_S:
                        _end_trial_state(shared_state, reason="time_limit")
                        break

                    pos = shared_state.get("latest_mm", None)

                    if (
                        CAM_IN_USE and JOYSTICK_CAM_TRIGGER
                        and not shared_state.get("camera_running", False)
                        and trial_start_pos is not None and pos is not None
                    ):
                        if dist2(pos, trial_start_pos) > (JOYSTICK_CAM_TRIGGER_RADIUS_MM ** 2):
                            start_camera(shared_state)
                            shared_state["camera_running"] = True
                            if DEBUG:
                                d = dist2(pos, trial_start_pos) ** 0.5
                                print(f"[DEBUG] Camera triggered by movement (distance: {d:.1f} mm)")

                    _handle_reward_logic(shared_state, pos, baseline)
                    time.sleep(0.01)

            elif MOVEMENT_TRIAL:
                while True:
                    if shutdown_event and shutdown_event.is_set():
                        _end_trial_state(shared_state, reason="shutdown")
                        break

                    if not shared_state.get("trial_in_progress", False):
                        _end_trial_state(shared_state, reason="cancelled")
                        break

                    pos = shared_state.get("latest_mm", None)

                    if (
                        CAM_IN_USE and JOYSTICK_CAM_TRIGGER
                        and not shared_state.get("camera_running", False)
                        and trial_start_pos is not None and pos is not None
                    ):
                        if dist2(pos, trial_start_pos) > (JOYSTICK_CAM_TRIGGER_RADIUS_MM ** 2):
                            start_camera(shared_state)
                            shared_state["camera_running"] = True
                            if DEBUG:
                                d = dist2(pos, trial_start_pos) ** 0.5
                                print(f"[DEBUG] Camera triggered by movement (distance: {d:.1f} mm)")

                    reward_hit_counted = _handle_reward_logic(shared_state, pos, baseline)
                    if reward_hit_counted:
                        hits = shared_state.get("reward_zone_hits", 0)
                        if hits >= TRIAL_END_REWARD_ZONE_HITS:
                            _end_trial_state(shared_state, reason=f"reward_hits={hits}")
                            break

                    time.sleep(0.01)

            else:
                _end_trial_state(shared_state, reason="no_trial_mode_selected")
                print("[WARN] No trial mode selected (TIME_TRIAL or MOVEMENT_TRIAL). Ending trial immediately.")

        except Exception as e:
            print(f"[ERROR] Exception in trial controller: {e}")
            _end_trial_state(shared_state, reason=f"error: {e}")

        try:
            ard_ser.write(b"E")
            ard_ser.flush()
            if DEBUG:
                print("[DEBUG] End command sent to Arduino.")
        except Exception as e:
            print(f"[WARN] Failed to send end byte to Arduino: {e}")

        time.sleep(0.05)

        if CAM_IN_USE and shared_state.get("camera_running", False):
            stop_camera(shared_state)
            shared_state["camera_running"] = False

        if DEBUG:
            dt = time.perf_counter() - t0
            print(f"[DEBUG] Trial ended after ~{dt:.3f} s")

        _print_trial_reward_summary(shared_state)
        write_session_summary_csv(shared_state)