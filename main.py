import sys
import threading
import time
import signal
import csv
import re
import queue
import serial
import matplotlib.pyplot as plt

from configurations import (
    DEBUG,
    ARD_SERIAL_PORT, DEFAULT_BAUD, DEFAULT_TIMEOUT_S,
    WD_IN_USE, CAM_IN_USE,
    TRAINING_MODE, Run, SYSTEM_ID, SYSTEM_NAME, CSV_DELIMITER,
)

from joystick_pos import figure_setup, make_update, start_animation
from water_dispenser import WaterDispenser, try_deliver_reward, _wait_for_peak_windows
from camera import start_camera, stop_camera
from trial_controller import run_trial_controller
from training import run_training_controller
from helpers import make_unique_csv_name


def main():
    def _increment_run_label(run_label: str) -> str:
        """Increment trailing digits in a run label."""
        m = re.match(r"^(.*?)(\d+)$", str(run_label).strip())
        if not m:
            return f"{run_label}_2"
        prefix, digits = m.groups()
        return f"{prefix}{int(digits) + 1:0{len(digits)}d}"

    def _capture_session_baseline(shared_state, timeout_s=2.0):
        """Capture a session-start baseline from incoming joystick samples."""
        baseline = None
        t_start = time.perf_counter()

        while baseline is None and (time.perf_counter() - t_start) < timeout_s and not shutdown_event.is_set():
            latest = shared_state.get("latest_mm", None)
            if latest is not None:
                baseline = latest
                break
            time.sleep(0.01)

        shared_state["session_baseline"] = baseline
        return baseline

    def _verify_wd_ready(shared_state):
        """Verify WD setup before allowing a session to start."""
        if not WD_IN_USE:
            return True

        wd = shared_state.get("wd", None)
        if wd is None:
            print("[ERROR] Water dispenser is enabled in configurations.py, but it is not available.")
            print("[ERROR] Session not started. Check WD connection / port and try again.")
            return False

        print("[WD] Verifying calibration factor and lick threshold before session start...")
        try:
            ok = wd.verify_setup()
        except Exception as e:
            print(f"[ERROR] Water dispenser verification failed: {e}")
            print("[ERROR] Session not started. Please fix WD setup and try again.")
            return False

        if not ok:
            print("[ERROR] Water dispenser verification failed.")
            print("[ERROR] Calibration factor and/or lick threshold could not be confirmed.")
            print("[ERROR] Session not started. Please fix WD setup and try again.")
            return False

        print("[WD] Verification complete. Water dispenser is ready.")
        return True

    shutdown_event = threading.Event()

    def signal_handler(signum, frame):
        if DEBUG:
            print("\n[SIGNAL] Ctrl+C detected, initiating shutdown...")
        shutdown_event.set()
        try:
            plt.close('all')
        except Exception:
            pass

    signal.signal(signal.SIGINT, signal_handler)

    print("\n" + "=" * 120)
    print(f"Welcome to the Behavioral Joystick!  |  {SYSTEM_NAME} ({SYSTEM_ID})")
    print("-" * 120)
    print("- Ensure you have set the correct parameters in configurations.py for the active hardware profile, then run this script to start a trial.")
    print("- GUI hotkeys: t = camera toggle, w = manual reward")
    print(f"- Active system profile: {SYSTEM_ID} -> {SYSTEM_NAME}")
    print("=" * 120)

    shared_state = {
        # joystick data
        "latest_raw": None,
        "latest_mm": None,
        "latest_sample": None,
        "sample_counter": 0,

        # trial/session state
        "trial_in_progress": False,
        "trial_complete": False,
        "t0": None,
        "shutdown": shutdown_event,

        # reward system
        "wd": None,
        "last_reward_t": -1e9,
        "max_dist_mm": 0.0,
        "swing_max_mm": 0.0,
        "reward_summary": [],
        "reward_peak_data": [],
        "_peak_threads": [],
        "lick_summary": [],
        "nose_poke_reward_hold_durations": [],
        "reward_zone_hits": 0,
        "reward_armed": True,
        "current_run_label": Run,

        # session / baseline states
        "training_patches": None,
        "session_baseline": None,

        # events CSV
        "trial_csv_file": None,
        "events_csv_fh": None,
        "events_csv_writer": None,
        "events_csv_file": None,

        # camera state
        "camera_running": False,

        # external TTL trigger flag (set by serial reader when Arduino sees rising edge on pin 2)
        "ttl_trigger_pending": False,

        # nose poke state
        "nose_poke_present": False,
        "nose_poke_prev": False,
        "nose_poke_event_t": None,
        "nose_poke_required": False,
        "nose_poke_satisfied": False,
        "nose_poke_start_t": None,
        "nose_poke_duration_s": 0.0,
        "nose_poke_reward_given": False,

        # system metadata
        "system_id": SYSTEM_ID,
        "system_name": SYSTEM_NAME,
    }

    resources = {
        "ard_ser": None,
        "fig": None,
        "threads": [],
    }

    try:
        # ------------------------------------------------------------
        # Open Arduino serial
        # ------------------------------------------------------------
        try:
            ard_ser = serial.Serial(ARD_SERIAL_PORT, DEFAULT_BAUD, timeout=DEFAULT_TIMEOUT_S)
            ard_ser.set_buffer_size(rx_size=65536)
            resources["ard_ser"] = ard_ser
            print(f"[SYSTEM] {SYSTEM_NAME} ({SYSTEM_ID}) using Arduino {ARD_SERIAL_PORT}")
            if DEBUG:
                print(f"[DEBUG] Arduino serial opened: {ARD_SERIAL_PORT} @ {DEFAULT_BAUD}")
        except Exception as e:
            print(f"[ERROR] Error opening Arduino serial port {ARD_SERIAL_PORT}: {e}")
            sys.exit(1)

        shared_state["ard_ser"] = ard_ser

        # ------------------------------------------------------------
        # Water dispenser
        # ------------------------------------------------------------
        if WD_IN_USE:
            try:
                shared_state["wd"] = WaterDispenser()
                if DEBUG:
                    print("[DEBUG] Water dispenser initialized")
            except Exception as e:
                print(f"[WARN] Water dispenser init failed; continuing without it. Error: {e}")
                shared_state["wd"] = None

        # ------------------------------------------------------------
        # Build the figure/UI
        # ------------------------------------------------------------
        fig, ax, dot, line, coord_text, training_patches = figure_setup(training_mode=TRAINING_MODE)
        resources["fig"] = fig
        shared_state["training_patches"] = training_patches

        update = make_update(ard_ser, dot, line, coord_text, shared_state)

        # ------------------------------------------------------------
        # Manual reward: press 'w'
        # Manual camera toggle: press 't'
        # ------------------------------------------------------------
        def on_key(event):
            if event.key is None:
                return
            key = event.key.lower()

            if key == "w":
                def _manual_reward():
                    delivered, msg = try_deliver_reward(shared_state, reason="manual_hotkey")
                    print(msg)
                    if delivered:
                        shared_state["reward_zone_hits"] = shared_state.get("reward_zone_hits", 0) + 1
                        print("[REWARD] Manual reward triggered by 'w' key.")
                threading.Thread(target=_manual_reward, daemon=True).start()

            elif key == "t" and CAM_IN_USE:
                if shared_state["camera_running"]:
                    stop_camera(shared_state)
                    shared_state["camera_running"] = False
                    print(f"[CAMERA][{SYSTEM_ID}] Manually stopped")
                else:
                    start_camera(shared_state)
                    shared_state["camera_running"] = True
                    print(f"[CAMERA][{SYSTEM_ID}] Manually started")

        fig.canvas.mpl_connect("key_press_event", on_key)

        # ------------------------------------------------------------
        # Start controller thread
        # ------------------------------------------------------------
        start_event = threading.Event()

        if TRAINING_MODE:
            if DEBUG:
                print("[DEBUG] TRAINING MODE ACTIVE")
            ctrl_thread = threading.Thread(
                target=run_training_controller,
                args=(shared_state, start_event),
                daemon=False
            )
            ctrl_thread.start()
            resources["threads"].append(ctrl_thread)
        else:
            if DEBUG:
                print("[DEBUG] TRIAL MODE ACTIVE")
            trial_thread = threading.Thread(
                target=run_trial_controller,
                args=(shared_state, start_event),
                daemon=False
            )
            trial_thread.start()
            resources["threads"].append(trial_thread)

        # ------------------------------------------------------------
        # Input manager thread
        # ------------------------------------------------------------
        def _input_manager(start_event, shared_state, fig, ard_ser, shutdown_event):
            _input_q = queue.Queue()
            _AUTO_COMPLETE = object()
            auto_start_next = False

            def _stdin_reader():
                while not shutdown_event.is_set():
                    try:
                        line = input()
                        _input_q.put(line.strip())
                    except (EOFError, KeyboardInterrupt):
                        _input_q.put(None)
                        break

            threading.Thread(target=_stdin_reader, daemon=True).start()

            def _wait_input(prompt="", check_trial_complete=False):
                if prompt:
                    print(prompt, end="", flush=True)
                while not shutdown_event.is_set():
                    if check_trial_complete and shared_state.get("trial_complete", False):
                        while not _input_q.empty():
                            try:
                                _input_q.get_nowait()
                            except queue.Empty:
                                break
                        return _AUTO_COMPLETE
                    if shared_state.get("ttl_trigger_pending", False):
                        shared_state["ttl_trigger_pending"] = False
                        print("[TTL] External TTL trigger received — starting trial.")
                        return ""
                    try:
                        return _input_q.get(timeout=0.3)
                    except queue.Empty:
                        continue
                return None

            while not shutdown_event.is_set():
                if auto_start_next:
                    auto_start_next = False
                    result = ""
                    if DEBUG:
                        print("[DEBUG] Auto-starting next session after restart='y'.")
                elif TRAINING_MODE:
                    result = _wait_input("\nPress Enter to START training session, or start neural imaging (TTL on pin 2 will auto-start) (Ctrl+C to quit): ")
                else:
                    result = _wait_input("\nPress Enter to START trial, or start neural imaging (TTL on pin 2 will auto-start) (Ctrl+C to quit): ")

                if result is None:
                    if DEBUG:
                        print("[DEBUG] Input manager received interrupt/EOF")
                    shutdown_event.set()
                    try:
                        plt.close(fig)
                    except Exception:
                        pass
                    break

                if shutdown_event.is_set():
                    break

                if not _verify_wd_ready(shared_state):
                    continue

                shared_state["reward_summary"] = []
                shared_state["reward_peak_data"] = []
                shared_state["_peak_threads"] = []
                shared_state["lick_summary"] = []
                shared_state["nose_poke_reward_hold_durations"] = []
                shared_state["reward_zone_hits"] = 0
                shared_state["reward_armed"] = True
                shared_state["last_reward_t"] = -1e9
                shared_state["max_dist_mm"] = 0.0
                shared_state["swing_max_mm"] = 0.0
                shared_state["latest_sample"] = None
                shared_state["summary_csv_path"] = None

                # reset nose poke state for new session
                shared_state["nose_poke_present"] = False
                shared_state["nose_poke_prev"] = False
                shared_state["nose_poke_event_t"] = None
                shared_state["nose_poke_satisfied"] = False
                shared_state["nose_poke_start_t"] = None
                shared_state["nose_poke_duration_s"] = 0.0
                shared_state["nose_poke_reward_given"] = False

                _wait_for_peak_windows(shared_state)
                if shared_state.get("events_csv_fh") is not None:
                    try:
                        shared_state["events_csv_fh"].flush()
                        shared_state["events_csv_fh"].close()
                    except Exception:
                        pass
                    shared_state["events_csv_fh"] = None
                    shared_state["events_csv_writer"] = None

                events_csv_path = make_unique_csv_name(
                    "event_logging",
                    run_label=shared_state.get("current_run_label", Run),
                )
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

                summary_csv_path = make_unique_csv_name(
                    "session_summary",
                    run_label=shared_state.get("current_run_label", Run),
                )
                shared_state["summary_csv_path"] = str(summary_csv_path)

                wd = shared_state.get("wd", None)
                if wd is not None:
                    try:
                        wd.reset_lick_counter()
                        wd.get_update_lick()
                        if DEBUG:
                            print("[DEBUG] Lick counter reset before trial start")
                    except Exception as e:
                        if DEBUG:
                            print(f"[DEBUG] Lick counter reset failed: {e}")

                try:
                    ard_ser.write(b"S")
                except Exception as e:
                    print(f"[ERROR] Failed to write start byte to Arduino: {e}")
                    try:
                        events_fh.close()
                    except Exception:
                        pass
                    shared_state["events_csv_fh"] = None
                    shared_state["events_csv_writer"] = None
                    continue

                baseline = _capture_session_baseline(shared_state)
                if baseline is None:
                    print("[ERROR] Could not capture a joystick baseline after session start.")
                    print("[ERROR] Session not started. Make sure joystick data is streaming and try again.")
                    try:
                        ard_ser.write(b"E")
                        ard_ser.flush()
                    except Exception:
                        pass
                    try:
                        events_fh.close()
                    except Exception:
                        pass
                    shared_state["events_csv_fh"] = None
                    shared_state["events_csv_writer"] = None
                    shared_state["events_csv_file"] = None
                    continue

                if DEBUG:
                    print(f"[DEBUG] Session baseline captured: {baseline}")

                # t0 = moment Arduino receives start command (right after write)
                # All subsystems (CSV logger, reward timestamps) reference this
                shared_state["t0"] = time.perf_counter()
                shared_state["trial_in_progress"] = True
                shared_state["trial_complete"] = False

                if DEBUG:
                    print("[DEBUG] Trial triggered (Arduino start byte sent).")

                start_event.set()

                if TRAINING_MODE:
                    print("\nTraining running. Type 'c' + Enter to STOP training session, 't' to toggle camera via the terminal, or 'q' + Enter / Ctrl+C to quit the program.")
                else:
                    print("\nTrial running. Type 'c' + Enter to END trial session, 't' to toggle camera via the terminal, or 'q' + Enter / Ctrl+C to quit the program.")

                while not shutdown_event.is_set():
                    cmd_raw = _wait_input("> ", check_trial_complete=True)
                    if cmd_raw is _AUTO_COMPLETE:
                        break
                    if cmd_raw is None:
                        shutdown_event.set()
                        try:
                            plt.close(fig)
                        except Exception:
                            pass
                        return

                    cmd = cmd_raw.lower()
                    if cmd == "c":
                        shared_state["trial_in_progress"] = False
                        break
                    elif cmd == "q":
                        shared_state["trial_in_progress"] = False
                        shutdown_event.set()
                        try:
                            plt.close(fig)
                        except Exception:
                            pass
                        return
                    elif cmd == "":
                        continue
                    elif cmd == "t":
                        if not CAM_IN_USE:
                            print("[CAMERA] CAM_IN_USE is False — camera not enabled.")
                        elif shared_state.get("camera_running", False):
                            stop_camera(shared_state)
                            shared_state["camera_running"] = False
                            print(f"[CAMERA][{SYSTEM_ID}] Manual toggle: camera STOPPED.")
                        else:
                            start_camera(shared_state)
                            shared_state["camera_running"] = True
                            print(f"[CAMERA][{SYSTEM_ID}] Manual toggle: camera STARTED.")
                    elif cmd == "w":
                        delivered, msg = try_deliver_reward(shared_state, reason="manual_terminal")
                        print(msg)
                    else:
                        print("Unknown command. Type 'c' to stop, 't' to toggle camera, 'w' for manual reward, 'q' to quit.")

                if shutdown_event.is_set():
                    break

                while not shared_state.get("trial_complete", False) and not shutdown_event.is_set():
                    time.sleep(0.1)

                shared_state["trial_complete"] = False

                if shutdown_event.is_set():
                    break

                print("\n" + "=" * 120)
                print(f"TRIAL COMPLETE!  |  {SYSTEM_NAME} ({SYSTEM_ID})")
                print("=" * 120)

                _wait_for_peak_windows(shared_state)
                if shared_state.get("events_csv_fh") is not None:
                    try:
                        shared_state["events_csv_fh"].flush()
                        shared_state["events_csv_fh"].close()
                        events_file = shared_state.get("events_csv_file", "")
                        if DEBUG:
                            print(f"[DEBUG] Events CSV closed: {events_file}")
                    except Exception as e:
                        print(f"[ERROR] Error closing events CSV: {e}")
                    shared_state["events_csv_fh"] = None
                    shared_state["events_csv_writer"] = None

                if "_serial_reader_stop_event" in shared_state:
                    time.sleep(0.15)

                    trial_csv = shared_state.get("trial_csv_file", None)
                    if trial_csv:
                        try:
                            with open(trial_csv, "r") as f:
                                row_count = len(f.readlines())
                            if DEBUG:
                                print(f"[DEBUG] CSV file verified closed and readable: {trial_csv}")
                                print(f"[DEBUG] Total rows written: {row_count}")
                        except Exception as csv_check_err:
                            print(f"[ERROR] Could not verify CSV file: {csv_check_err}")

                restart_raw = _wait_input("\nRestart trial with new run label? (y/n): ")
                if restart_raw is None:
                    user_input = 'n'
                    shutdown_event.set()
                else:
                    user_input = restart_raw.lower()

                if user_input == 'y' and not shutdown_event.is_set():
                    shared_state["latest_raw"] = None
                    shared_state["latest_mm"] = None
                    shared_state["trial_in_progress"] = False
                    shared_state["current_run_label"] = _increment_run_label(
                        shared_state.get("current_run_label", Run)
                    )
                    if DEBUG:
                        print(f"[DEBUG] Next run label: {shared_state['current_run_label']}")
                    auto_start_next = True
                    continue
                else:
                    shutdown_event.set()
                    try:
                        plt.close(fig)
                    except Exception:
                        pass
                    break

        input_thread = threading.Thread(
            target=_input_manager,
            args=(start_event, shared_state, fig, ard_ser, shutdown_event),
            daemon=True
        )
        input_thread.start()
        resources["threads"].append(input_thread)

        # ------------------------------------------------------------
        # Close serial when window closes
        # ------------------------------------------------------------
        def on_close(_e):
            if DEBUG:
                print("[DEBUG] Window closing; Stopping Arduino, joystick serial, water dispenser")
            shutdown_event.set()

            try:
                if ard_ser and ard_ser.is_open:
                    ard_ser.write(b"E")
                    ard_ser.flush()
                    if DEBUG:
                        print("[DEBUG] Stop command sent to Arduino")
            except Exception:
                pass

            try:
                if ard_ser and ard_ser.is_open:
                    ard_ser.close()
            except Exception:
                pass
            try:
                wd = shared_state.get("wd", None)
                if wd is not None:
                    wd.close()
            except Exception:
                pass

        fig.canvas.mpl_connect("close_event", on_close)

        # Start Matplotlib loop
        _anim = start_animation(fig, update, shutdown_event=shutdown_event)

    finally:
        if DEBUG:
            print("[DEBUG] Beginning shutdown...")
        else:
            print("\n[SHUTDOWN] Closing resources...")

        shutdown_event.set()

        print("[SHUTDOWN] 1/5 Stopping Arduino...")
        try:
            if resources.get("ard_ser") is not None and resources["ard_ser"].is_open:
                resources["ard_ser"].write(b"E")
                resources["ard_ser"].flush()
                if DEBUG:
                    print("[DEBUG] Stop command sent to Arduino during shutdown")
        except Exception as e:
            if DEBUG:
                print(f"[DEBUG] Arduino stop command error: {e}")

        print("[SHUTDOWN] 2/5 Stopping serial reader...")
        serial_reader_stop = shared_state.get("_serial_reader_stop_event")
        serial_reader_thread = shared_state.get("_serial_reader_thread")
        if serial_reader_stop is not None:
            serial_reader_stop.set()
            if DEBUG:
                print("[DEBUG] Serial reader stop_event set")

        if serial_reader_thread is not None and serial_reader_thread.is_alive():
            try:
                serial_reader_thread.join(timeout=1.5)
                if serial_reader_thread.is_alive() and DEBUG:
                    print("[DEBUG] Serial reader thread did not exit in time (but closing port anyway)")
            except Exception as e:
                if DEBUG:
                    print(f"[DEBUG] Serial reader join error: {e}")

        # Close Arduino serial
        print("[SHUTDOWN] 3/5 Closing Arduino serial port...")
        try:
            if resources.get("ard_ser") is not None and resources["ard_ser"].is_open:
                resources["ard_ser"].close()
        except Exception as e:
            if DEBUG:
                print(f"[DEBUG] Arduino serial close error: {e}")

        # Close water dispenser
        print("[SHUTDOWN] 4/5 Closing water dispenser...")
        try:
            wd = shared_state.get("wd", None)
            if wd is not None:
                wd.close()
        except Exception as e:
            if DEBUG:
                print(f"[DEBUG] Water dispenser close error: {e}")

        # Close events CSV if still open
        _wait_for_peak_windows(shared_state)
        if shared_state.get("events_csv_fh") is not None:
            try:
                shared_state["events_csv_fh"].flush()
                shared_state["events_csv_fh"].close()
            except Exception:
                pass
            shared_state["events_csv_fh"] = None
            shared_state["events_csv_writer"] = None

        # Close figure
        print("[SHUTDOWN] 5/5 Closing GUI window...")
        try:
            if resources.get("fig") is not None:
                plt.close(resources["fig"])
        except Exception as e:
            if DEBUG:
                print(f"[DEBUG] Figure close error: {e}")

        for thread in resources.get("threads", []):
            try:
                thread.join(timeout=2.0)
                if thread.is_alive() and DEBUG:
                    print(f"[DEBUG] Thread {thread.name} did not exit cleanly (likely blocked in input())")
            except Exception as e:
                if DEBUG:
                    print(f"[DEBUG] Thread join error: {e}")

        if DEBUG:
            print("[DEBUG] Shutdown complete")

        print("Program terminated successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)