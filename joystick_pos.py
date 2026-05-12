"""
Joystick GUI visualization with trail.

"""

import matplotlib
matplotlib.use('TkAgg')  # Ensures interaction like zoom and pan
import threading
import time
import csv
from collections import deque
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import matplotlib.patches as patches

from configurations import (
    CLAMP_RADIUS, ANIM_INTERVAL_MS, DEBUG, TRAIL_LENGTH,
    TRAINING_STAGE, TRAINING_INNER_X_MM, TRAINING_INNER_Y_MM, TRAINING_OUTSIDE_R_MM,
    TRAINING_MODE,
    TRIAL_REWARD_MODE, TRIAL_REWARD_RADIUS_MM, TRIAL_BOX_X_MM, TRIAL_BOX_Y_MM,
    Run, SYSTEM_ID, SYSTEM_NAME,
    NOSE_POKE_IN_USE, NOSE_POKE_HOLD_REQUIRED, NOSE_POKE_HOLD_TIME_S,
    CSV_DELIMITER,
)
from helpers import parse_xy, raw_to_volts, volts_to_mm, make_unique_csv_name
from water_dispenser import log_lick_event, log_nose_poke_event


# ==================================================
# Plotting visuals - Animation
# ==================================================
def figure_setup(training_mode=False):
    """
    Create the figure, axes (area with plotted data), artists, and buttons.

    Adds training / trial target visualization

    Returns
    -------
    fig, ax, dot, line, coord_text, (target_patches dict or None)
    """
    fig = plt.figure(figsize=(8, 9))

    ax_plot = fig.add_axes([0.1, 0.2, 0.8, 0.75])
    ax = ax_plot
    ax.set_aspect('equal')
    ax.grid(True)
    ax.autoscale(enable=True)

    ax_info = fig.add_axes([0.1, 0.02, 0.8, 0.12])
    ax_info.axis('off')

    circle = plt.Circle((0, 0), CLAMP_RADIUS, color='black', fill=False)
    ax.add_patch(circle)

    target_patches = {}

    if training_mode:
        if TRAINING_STAGE == 2:
            outer_circle = plt.Circle(
                (0, 0),
                TRAINING_OUTSIDE_R_MM,
                color='lightgreen',
                fill=False,
                linewidth=2.5,
                linestyle='--',
                alpha=0.7,
                label='Stage 2'
            )
            ax.add_patch(outer_circle)
            target_patches['outer_circle'] = outer_circle
            ax.legend(loc='upper right')
            if DEBUG:
                print(f"[DEBUG] Training stage 2 outer radius circle displayed: radius={TRAINING_OUTSIDE_R_MM}mm")

        elif TRAINING_STAGE == 3:
            box = patches.Rectangle(
                xy=(-CLAMP_RADIUS, -TRAINING_INNER_Y_MM),
                width=CLAMP_RADIUS + TRAINING_INNER_X_MM,
                height=2 * TRAINING_INNER_Y_MM,
                linewidth=2,
                edgecolor='lightgreen',
                facecolor='lightgreen',
                alpha=0.3,
                label='Stage 3: Target Zone'
            )
            ax.add_patch(box)
            target_patches['box'] = box
            ax.legend(
                loc='lower center',
                bbox_to_anchor=(0.5, 1.02),
                ncol=1
            )
            if DEBUG:
                print(f"[DEBUG] Training stage 3 target box displayed: X<=({TRAINING_INNER_X_MM}) Y=[{-TRAINING_INNER_Y_MM}, {TRAINING_INNER_Y_MM}]")
    else:
        if TRIAL_REWARD_MODE == "outside_radius":
            outer_circle = plt.Circle(
                (0, 0),
                TRIAL_REWARD_RADIUS_MM,
                color='lightgreen',
                fill=False,
                linewidth=2.5,
                linestyle='--',
                alpha=0.7,
                label='Trial: Reward Outside Radius'
            )
            ax.add_patch(outer_circle)
            target_patches['outer_circle'] = outer_circle
            ax.legend(loc='upper right')
            if DEBUG:
                print(f"[DEBUG] Trial outside-radius circle displayed: radius={TRIAL_REWARD_RADIUS_MM}mm")

        elif TRIAL_REWARD_MODE == "box":
            ymin_target = TRIAL_BOX_Y_MM

            box = patches.Rectangle(
                xy=(-TRIAL_BOX_X_MM, ymin_target),
                width=2 * TRIAL_BOX_X_MM,
                height=CLAMP_RADIUS - ymin_target,
                linewidth=2,
                edgecolor='lightgreen',
                facecolor='lightgreen',
                alpha=0.3,
                label='Trial: Reward Box'
            )
            ax.add_patch(box)
            target_patches['box'] = box
            ax.legend(
                loc='lower center',
                bbox_to_anchor=(0.5, 1.02),
                ncol=1
            )
            if DEBUG:
                print(f"[DEBUG] Trial reward box displayed: X=[{-TRIAL_BOX_X_MM}, {TRIAL_BOX_X_MM}] Y>={TRIAL_BOX_Y_MM}")

    if not target_patches:
        target_patches = None

    dot, = ax.plot([], [], 'ro')
    line, = ax.plot([], [], 'b-', alpha=0.5)

    coord_text = ax_info.text(
        0.5, 0.5,
        'X: 0.000 mm  Y: 0.000 mm\nTime: --\nNose poke: False  Hold: 0.00 s\nKeys: t = camera toggle   |   w = manual reward',
        ha='center', va='center', fontsize=12, family='monospace',
        bbox=dict(boxstyle='round,pad=0.6', facecolor='#f0f0f0', alpha=0.9, edgecolor='#666666', linewidth=1.5),
        transform=ax_info.transAxes
    )

    ax.set_title(f"Joystick Position in Real Time - {SYSTEM_NAME} ({SYSTEM_ID})")
    ax.set_xlabel("X [mm]", fontsize=10)
    ax.set_ylabel("Y [mm]", fontsize=10)
    return fig, ax, dot, line, coord_text, target_patches


# ==================================================
# Serial Reader Thread - Background
# ==================================================

def _serial_reader(ser, stop_event, shared_state: dict):
    """
    Background thread: read lines from serial, parse, update shared_state,
    and log numeric samples to CSV.

    Trial boundaries are Python-only:
      - Logging is gated ONLY by shared_state["trial_in_progress"].
      - Arduino markers / nose poke event strings are not logged to joystick CSV.
    """
    recorder = {
        "fh": None,
        "writer": None,
        "start": None,
        "filename": None,
        "row_count": 0,
        "flush_every_n": 100, # at 1 kHz -> flush ~10x/sec
    }

    def _close_csv(reason: str = ""): #close CSV file if open, with optional reason for debug logging
        if recorder["fh"] is not None:
            try:
                recorder["fh"].flush()
                recorder["fh"].close()
                if DEBUG:
                    print(f"[DEBUG] CSV closed{(' - ' + reason) if reason else ''}: {recorder['filename']}")
            except Exception as e:
                print(f"[ERROR] Error closing CSV: {e}")
        recorder["fh"] = None
        recorder["writer"] = None
        recorder["start"] = None
        recorder["filename"] = None
        recorder["row_count"] = 0

    while not stop_event.is_set():
        try:
            # Hold timer check — runs on every iteration (~1 kHz joystick stream).
            # Sets nose_poke_satisfied once the hold threshold is met.
            if NOSE_POKE_HOLD_REQUIRED and shared_state.get("nose_poke_present", False):
                start_t = shared_state.get("nose_poke_start_t")
                t0 = shared_state.get("t0")
                if start_t is not None and t0 is not None:
                    hold_s = max(0.0, time.perf_counter() - t0 - start_t)
                    shared_state["nose_poke_duration_s"] = hold_s
                    if hold_s >= NOSE_POKE_HOLD_TIME_S and not shared_state.get("nose_poke_satisfied", False):
                        shared_state["nose_poke_satisfied"] = True
                        if DEBUG:
                            print(f"[NOSE] Hold requirement met ({hold_s:.3f}s) — joystick rewards unlocked")

            raw = ser.readline()

            if stop_event.is_set():
                break

            if not raw:
                continue

            line_raw = raw.decode(errors="ignore").strip()

            if not line_raw:
                continue

            # --------------------------------------------------
            # Nose poke event messages from Arduino
            # Format:
            #   NOSE_POKE:1 (nose present in hole)
            #   NOSE_POKE:0 (nose absent)
            # --------------------------------------------------
            if line_raw.startswith("NOSE_POKE:"):
                try:
                    value = line_raw.split(":", 1)[1].strip()
                    nose_present = (value == "1")

                    t0 = shared_state.get("t0", None)
                    t_rel = (time.perf_counter() - t0) if t0 is not None else None

                    prev_present = shared_state.get("nose_poke_present", False)
                    shared_state["nose_poke_prev"] = prev_present
                    shared_state["nose_poke_present"] = nose_present
                    shared_state["nose_poke_event_t"] = t_rel

                    if nose_present:
                        shared_state["nose_poke_start_t"] = t_rel
                        shared_state["nose_poke_duration_s"] = 0.0
                        # If hold required, satisfaction is deferred to the hold timer check
                        shared_state["nose_poke_satisfied"] = not NOSE_POKE_HOLD_REQUIRED
                    else:
                        start_t = shared_state.get("nose_poke_start_t", None)
                        if start_t is not None and t_rel is not None:
                            shared_state["nose_poke_duration_s"] = max(0.0, t_rel - start_t)
                        # Fill in the hold duration for the last reward if one was given this poke
                        if shared_state.get("nose_poke_reward_given", False):
                            hold_durations = shared_state.get("nose_poke_reward_hold_durations")
                            if hold_durations:
                                hold_durations[-1] = shared_state["nose_poke_duration_s"]
                        shared_state["nose_poke_start_t"] = None
                        shared_state["nose_poke_satisfied"] = False
                        shared_state["nose_poke_reward_given"] = False

                    # Log only real state changes
                    if nose_present != prev_present:
                        log_nose_poke_event(
                            shared_state,
                            nose_present=nose_present,
                            event_t_rel=t_rel,
                            reason="present" if nose_present else "clear",
                        )

                    if DEBUG:
                        print(f"[NOSE] Event received: present={nose_present}, t={t_rel}")

                except Exception as e:
                    if DEBUG:
                        print(f"[DEBUG] Failed to parse nose poke event {line_raw!r}: {e}")
                continue

            raw_coords = parse_xy(line_raw)
            if raw_coords is None:
                if DEBUG and line_raw not in ("",):
                    print(f"[DEBUG] Non-data line: {line_raw!r}")
                continue

            vx, vy = raw_to_volts(raw_coords)
            x_mm, y_mm = volts_to_mm(vx, vy)
            shared_state["latest_raw"] = raw_coords
            shared_state["latest_mm"] = (x_mm, y_mm)

            active_logging = bool(shared_state.get("trial_in_progress", False))

            if not active_logging and recorder["fh"] is not None:
                _close_csv("trial_in_progress=False")

            if active_logging and recorder["fh"] is None:
                recorder["filename"] = make_unique_csv_name(
                    "joystick_position",
                    run_label=shared_state.get("current_run_label", Run),
                )
                recorder["fh"] = open(recorder["filename"], "w", newline="", encoding="utf-8")
                recorder["writer"] = csv.writer(recorder["fh"], delimiter=CSV_DELIMITER)
                recorder["writer"].writerow([
                    "sample_idx", "t_s", "raw_x", "raw_y", "volt_x", "volt_y", "x_mm", "y_mm"
                ])

                recorder["start"] = shared_state.get("t0", time.perf_counter())
                recorder["row_count"] = 0
                shared_state["trial_csv_file"] = str(recorder["filename"])
                shared_state["sample_counter"] = 0

                if DEBUG:
                    print(f"[DEBUG] Opened CSV file: {recorder['filename']}")

            wd = shared_state.get("wd", None)
            if wd is not None and active_logging:
                try:
                    lick_delta = wd.get_update_lick()
                    if lick_delta > 0:
                        log_lick_event(shared_state, lick_delta)
                except Exception:
                    pass
            elif wd is not None and not active_logging:
                try:
                    wd.get_update_lick()
                except Exception:
                    pass

            if active_logging and recorder["fh"] is not None:
                t_rel = time.perf_counter() - recorder["start"]
                sample_idx = shared_state.get("sample_counter", 0)

                sample = {
                    "sample_idx": sample_idx,
                    "t_s": t_rel,
                    "raw_x": raw_coords[0],
                    "raw_y": raw_coords[1],
                    "volt_x": vx,
                    "volt_y": vy,
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                }

                shared_state["latest_sample"] = sample

                recorder["writer"].writerow([
                    sample["sample_idx"],
                    sample["t_s"],
                    sample["raw_x"], sample["raw_y"],
                    sample["volt_x"], sample["volt_y"],
                    sample["x_mm"], sample["y_mm"],
                ])

                shared_state["sample_counter"] = sample_idx + 1

                recorder["row_count"] += 1
                if recorder["row_count"] % recorder["flush_every_n"] == 0:
                    recorder["fh"].flush()

        except Exception as e:
            if DEBUG:
                print(f"[DEBUG] Serial reader exception: {e}")
            continue

    _close_csv("serial thread exit")
    if DEBUG:
        print("[DEBUG] Serial reader thread exiting.")


# ==================================================
# Update Function Builder - Visualization Logic
# ==================================================

def make_update(ser, dot, line, coord_text, shared_state: dict):
    """
    Starts background serial reader thread that updates shared_state["latest_mm"].
    GUI update reads only from shared_state.
    Returns the update function to be passed to FuncAnimation.
    """
    stop_event = threading.Event()
    reader_thr = threading.Thread(
        target=_serial_reader,
        args=(ser, stop_event, shared_state),
        daemon=True
    )
    reader_thr.start()

    shared_state["_serial_reader_thread"] = reader_thr
    shared_state["_serial_reader_stop_event"] = stop_event

    def _on_fig_close(_event):
        stop_event.set()
        if DEBUG:
            print("[DEBUG] Serial reader stop_event set on figure close.")
    try:
        dot.figure.canvas.mpl_connect("close_event", _on_fig_close)
    except Exception:
        if DEBUG:
            print("[DEBUG] Could not attach close_event for serial reader.")

    last_mm = (0.0, 0.0)
    trail_xs = deque(maxlen=TRAIL_LENGTH)
    trail_ys = deque(maxlen=TRAIL_LENGTH)

    def update(_frame):
        nonlocal last_mm
        mm = shared_state.get("latest_mm", None)
        if mm is not None:
            last_mm = mm

        x, y = last_mm

        trail_xs.append(x)
        trail_ys.append(y)

        dot.set_data([x], [y])
        line.set_data(list(trail_xs), list(trail_ys))

        t0 = shared_state.get("t0", None)
        if t0 is not None and shared_state.get("trial_in_progress", False):
            elapsed_s = time.perf_counter() - t0
            mins = int(elapsed_s // 60)
            secs = int(elapsed_s % 60)
            time_line = f"Time: {mins}:{secs:02d}  ({elapsed_s:.1f} s)"
        else:
            time_line = "Time: --"

        nose_present = bool(shared_state.get("nose_poke_present", False))
        nose_hold_s = 0.0

        if NOSE_POKE_IN_USE and nose_present:
            start_t = shared_state.get("nose_poke_start_t", None)
            if start_t is not None and t0 is not None:
                now_rel = time.perf_counter() - t0
                nose_hold_s = max(0.0, now_rel - start_t)
            else:
                nose_hold_s = float(shared_state.get("nose_poke_duration_s", 0.0))
        else:
            nose_hold_s = float(shared_state.get("nose_poke_duration_s", 0.0))

        rewards = shared_state.get("reward_zone_hits", 0)

        coord_text.set_text(
            f"X: {x:.3f} mm  Y: {y:.3f} mm\n"
            f"{time_line}\n"
            f"Nose poke: {nose_present}  Hold: {nose_hold_s:.2f} s\n"
            f"Rewards: {rewards}\n"
            f"Keys: t = camera toggle   |   w = manual reward"
        )
        return dot, line, coord_text

    return update


# ==================================================
# Animation starter
# ==================================================

def start_animation(fig, update, blit=True, shutdown_event=None):
    """
    Start the Matplotlib animation loop.
    Returns the FuncAnimation object — caller MUST keep a reference to
    prevent garbage collection.
    """
    anim = animation.FuncAnimation(
        fig,
        update,
        interval=ANIM_INTERVAL_MS,
        blit=blit,
        cache_frame_data=False,
    )

    fig._joystick_anim = anim

    plt.show(block=True)
    return anim