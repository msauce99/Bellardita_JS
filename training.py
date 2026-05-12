import time
import threading
import math

from configurations import (
    DEBUG, SYSTEM_ID,
    CAM_IN_USE, CAM_START_WITH_TRIAL, JOYSTICK_CAM_TRIGGER, JOYSTICK_CAM_TRIGGER_RADIUS_MM,
    TRAINING_STAGE,
    TRAINING_TOUCH_R_MM,
    TRAINING_OUTSIDE_R_MM,
    TRAINING_INNER_X_MM, TRAINING_INNER_Y_MM,
    TRAINING_DURATION_S,
    REWARD_REARM_RADIUS_MM,
    NOSE_POKE_REQUIRED,
)
from helpers import dist2
from water_dispenser import try_deliver_reward, log_blocked_reward_event, write_session_summary_csv
from camera import start_camera, stop_camera


"""
Training program
Set terminal to R system: $env:JOYSTICK_SYSTEM_ID="system_R"
"""


def _print_reward_summary(shared_state, header_label):
    reward_summary = shared_state.get("reward_summary", [])
    baseline = shared_state.get("session_baseline", None)

    print(f"\n{'='*100}")
    print(
        f"[{header_label}] SESSION SUMMARY  |  Stage {TRAINING_STAGE}  |  Baseline: ({baseline[0]:.4f}, {baseline[1]:.4f})"
        if baseline else
        f"[{header_label}] SESSION SUMMARY  |  Stage {TRAINING_STAGE}  |  Baseline: N/A"
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


def _training_reward_condition(pos, baseline):
    """
    Evaluate the configured training reward condition relative to session baseline.
    """
    if pos is None or baseline is None:
        return False

    dx = pos[0] - baseline[0]
    dy = pos[1] - baseline[1]
    dist_from_baseline_sq = dx**2 + dy**2

    touched = dist_from_baseline_sq >= (TRAINING_TOUCH_R_MM ** 2)
    past_outer = dist_from_baseline_sq >= (TRAINING_OUTSIDE_R_MM ** 2)
    # inside_box = ((-TRAINING_INNER_X_MM <= dx <= TRAINING_INNER_X_MM) and (dy >= TRAINING_INNER_Y_MM)) # box in y direction
    inside_box = ((dx <= TRAINING_INNER_X_MM) and (-TRAINING_INNER_Y_MM <= dy <= TRAINING_INNER_Y_MM)) #box in x direction

    if TRAINING_STAGE == 1:
        return touched
    elif TRAINING_STAGE == 2:
        return past_outer
    elif TRAINING_STAGE == 3:
        return inside_box
    else:
        raise ValueError(f"Unsupported TRAINING_STAGE: {TRAINING_STAGE!r}")


def run_training_controller(shared_state, start_event, stop_event=None):
    """
    Training controller loop:
      - waits for start_event (Enter in main)
      - sets trial_in_progress True to enable joystick CSV logging
      - watches joystick movement and dispenses reward according to TRAINING_STAGE
      - if NOSE_POKE_REQUIRED is True, a nose poke must be present before joystick
        movement can trigger reward
      - ends when stop_event is set
    """
    duration_str = f"{TRAINING_DURATION_S/60:.0f} min" if TRAINING_DURATION_S > 0 else "unlimited"
    print(
        "\n" + "=" * 120
        + "\nTRAINING MODE ACTIVE"
        + "\n Training Stage set to " + str(TRAINING_STAGE)
        + f"\n Session duration: {duration_str}"
        + "\n Type 'c' + Enter to STOP training, 't' to toggle camera, or 'q' + Enter to quit program."
        + "\n" + "=" * 120
    )

    if stop_event is None:
        stop_event = threading.Event()

    shutdown_event = shared_state.get("shutdown")

    while not stop_event.is_set() and not (shutdown_event and shutdown_event.is_set()):
        start_event.wait(timeout=0.5)

        if shutdown_event and shutdown_event.is_set():
            break

        if not start_event.is_set():
            continue

        t0 = shared_state.get("t0", None)
        if t0 is None:
            t0 = time.perf_counter()
            shared_state["t0"] = t0

        start_event.clear()

        shared_state["trial_in_progress"] = True
        shared_state["trial_complete"] = False

        baseline = shared_state.get("session_baseline", None)
        if baseline is None:
            baseline = shared_state.get("latest_mm", None)
            shared_state["session_baseline"] = baseline

        if DEBUG:
            print(f"[TRAIN] Training started. Stage={TRAINING_STAGE}. Baseline={baseline}")
            print(f"[TRAIN] Nose poke required: {NOSE_POKE_REQUIRED}")

        prev_reward_condition = False
        shared_state["reward_armed"] = True
        last_countdown_print_t = 0.0

        shared_state["camera_running"] = False
        if CAM_IN_USE and CAM_START_WITH_TRIAL and not JOYSTICK_CAM_TRIGGER:
            start_camera(shared_state)
            shared_state["camera_running"] = True
            if DEBUG:
                print("[TRAIN] Camera triggering started (immediate).")
        elif CAM_IN_USE and JOYSTICK_CAM_TRIGGER:
            if DEBUG:
                print("[TRAIN] Waiting for joystick movement to trigger camera.")

        while (
            shared_state.get("trial_in_progress", False)
            and not stop_event.is_set()
            and not (shutdown_event and shutdown_event.is_set())
        ):
            if TRAINING_DURATION_S > 0:
                elapsed = time.perf_counter() - t0
                if elapsed >= TRAINING_DURATION_S:
                    print(f"\n[TIMER] Session time limit reached ({TRAINING_DURATION_S/60:.0f} min). Ending training.")
                    shared_state["trial_in_progress"] = False
                    break
                remaining = TRAINING_DURATION_S - elapsed
                interval = 60.0 if remaining <= 300.0 else 300.0
                if elapsed - last_countdown_print_t >= interval:
                    mins = int(remaining // 60)
                    secs = int(remaining % 60)
                    print(f"[TIMER] {mins:02d}:{secs:02d} remaining in training session.")
                    last_countdown_print_t = elapsed

            pos = shared_state.get("latest_mm", None)
            if pos is None or baseline is None:
                time.sleep(0.01)
                continue

            dist_from_baseline_sq = dist2(pos, baseline)
            dist_mm = dist_from_baseline_sq ** 0.5
            if dist_mm > shared_state.get("max_dist_mm", 0.0):
                shared_state["max_dist_mm"] = dist_mm

            if not shared_state.get("reward_armed", True):
                if dist_mm > shared_state.get("swing_max_mm", 0.0):
                    shared_state["swing_max_mm"] = dist_mm
                if dist_from_baseline_sq <= (REWARD_REARM_RADIUS_MM ** 2):
                    swing_max = shared_state.get("swing_max_mm", 0.0)
                    shared_state["reward_armed"] = True
                    prev_reward_condition = False
                    shared_state["swing_max_mm"] = 0.0
                    if swing_max > 0:
                        print(f"[REWARD][{SYSTEM_ID}] max_dist_achieved_post_reward={swing_max:.1f}mm")

            if (
                CAM_IN_USE and JOYSTICK_CAM_TRIGGER
                and not shared_state.get("camera_running", False)
                and baseline is not None
            ):
                if dist_from_baseline_sq > (JOYSTICK_CAM_TRIGGER_RADIUS_MM ** 2):
                    start_camera(shared_state)
                    shared_state["camera_running"] = True
                    if DEBUG:
                        d = dist_from_baseline_sq ** 0.5
                        print(f"[TRAIN] Camera triggered by movement (distance: {d:.1f} mm)")

            reward_condition = _training_reward_condition(pos, baseline)

            # Gate joystick reward on current nose poke state if enabled
            nose_gate_ok = True
            if NOSE_POKE_REQUIRED:
                nose_gate_ok = bool(shared_state.get("nose_poke_satisfied", False))

            if (
                shared_state.get("reward_armed", True)
                and reward_condition
                and not prev_reward_condition
                and nose_gate_ok
            ):
                delivered, msg = try_deliver_reward(
                    shared_state,
                    reason=f"training_stage_{TRAINING_STAGE}"
                )
                if delivered:
                    shared_state["reward_zone_hits"] = shared_state.get("reward_zone_hits", 0) + 1
                    shared_state["reward_armed"] = False
                    shared_state["swing_max_mm"] = 0.0
                if DEBUG:
                    print(msg)
                    print(f"[TRAIN] Edge trigger at pos={pos} (baseline={baseline})")

            elif (
                shared_state.get("reward_armed", True)
                and reward_condition
                and not prev_reward_condition
                and not nose_gate_ok
            ):
                log_blocked_reward_event(shared_state, reason=f"no_nose_poke_stage_{TRAINING_STAGE}")
                if DEBUG:
                    print("[TRAIN] Reward condition met, but blocked because nose poke was not present.")

            prev_reward_condition = reward_condition
            time.sleep(0.01)

        if CAM_IN_USE and shared_state.get("camera_running", False):
            stop_camera(shared_state)
            shared_state["camera_running"] = False
            if DEBUG:
                print("[TRAIN] Camera triggering stopped.")

        shared_state["trial_complete"] = True

        _print_reward_summary(shared_state, "TRAIN")
        write_session_summary_csv(shared_state)

        if DEBUG:
            print("[TRAIN] Training session ended.")