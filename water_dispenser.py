import csv
import serial
import threading
import queue
import time
import math
import re

from configurations import (
    WD_PORT, WD_BAUD, DEBUG, WD_IN_USE,
    REWARD_COOLDOWN_S, TRIGGER_WAIT_S, SET_CALIB_FACTOR, SET_LICK_THRESHOLD,
    WD_VERIFY_MAX_RETRIES,
    WD_PULSE_N, WD_PULSE_QTY_UL, WD_PULSE_FREQ_HZ, WD_PULSE_N_SEQ, WD_PULSE_DELAY_MS,
    CSV_DELIMITER, SYSTEM_ID, REWARD_PEAK_TRACKING_WINDOW_S,
)


class WaterDispenser:
    """
    Water dispenser controller using serial port:

    Based on LabeoTech module manual:
        PULSE:<n_pulses>:<qty_uL>:<freq_hz>:<n_seq>:<delay_ms>
        RESET, HELP, SET_CALIB_FACTOR:<ms_per_ul>, SET_LICK_THRESHOLD:<0-255>
    """
    def __init__(self, port: str = WD_PORT, baud: int = WD_BAUD, timeout: float = 0.05):
        if DEBUG:
            print(f"Water Dispenser Opening {port} @ {baud}")
        self.ser = serial.Serial(port=port, baudrate=baud, timeout=timeout)

        self.rx_q: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()

        # Lick bookkeeping
        self._lock = threading.Lock()
        self._lick_total = 0
        self._last_returned_total = 0

        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

        self.setup_status = {
            "calibration_verified": False,
            "lick_threshold_verified": False,
            "calibration_response": None,
            "lick_threshold_response": None,
        }

        self.send("RESET")
        time.sleep(0.1)
        self.send("HELP")
        time.sleep(0.1)
        self.drain_rx(print_lines=True)
        time.sleep(0.1)
        self.arm(0)

    # ------------------------------------------------------------------
    # Reader thread + RX helpers
    # ------------------------------------------------------------------
    def _reader(self):
        while not self._stop.is_set():
            try:
                line = self.ser.readline()
                if not line:
                    continue

                decoded = line.decode(errors="ignore").strip()
                if not decoded:
                    continue

                if "lick" in decoded.lower():
                    with self._lock:
                        self._lick_total += 1

                self.rx_q.put(decoded)

            except Exception as e:
                print("[WD] Serial read error:", e)
                continue

    def drain_rx(self, print_lines: bool = False):
        """Drain any queued RX lines (useful after HELP/RESET spam)."""
        n = 0
        while True:
            try:
                msg = self.rx_q.get_nowait()
            except queue.Empty:
                break
            n += 1
            if print_lines and DEBUG:
                print("[WD][drain]", msg)
        if DEBUG:
            print(f"[WD] Drained {n} lines")

    def _get_response(self, *, timeout: float = 0.5, match_any: tuple[str, ...] | None = None):
        """
        Wait for a line from rx_q. If match_any is provided, keep reading until a line
        containing ANY of those tokens arrives (or timeout).

        Returns:
            - The first matching line
            - Or the last line seen before timeout
            - Or None if nothing arrived
        """
        t_end = time.perf_counter() + timeout
        last = None

        while time.perf_counter() < t_end:
            try:
                line = self.rx_q.get(timeout=0.05)
            except queue.Empty:
                continue

            last = line

            if match_any is None:
                return line

            for tok in match_any:
                if tok in line:
                    return line

        return last

    def _extract_first_number(self, text):
        if text is None:
            return None
        m = re.search(r"-?\d+(?:\.\d+)?", str(text))
        if not m:
            return None
        try:
            return float(m.group(0))
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Send raw command to water dispenser
    # ------------------------------------------------------------------
    def send(self, cmd: str):
        if not cmd.endswith("\n"):
            cmd += "\n"
        written = self.ser.write(cmd.encode("ascii", errors="ignore"))
        try:
            self.ser.flush()
        except Exception:
            pass
        if DEBUG:
            print(f"[WD] -> {cmd.strip()} ({written} bytes)")

    # ------------------------------------------------------------------
    # Manual commands
    # ------------------------------------------------------------------
    def arm(self, state: int):
        state = 1 if state else 0
        self.send(f"ARM:{state}")
        if DEBUG:
            print(f"[WD] ARM set to {state}")

    def set_calib_factor(self, ms_per_ul: float, verify: bool = True, max_retries: int = 3):
        """
        Set calibration factor with optional verification and retry logic.

        Args:
            ms_per_ul: milliseconds per microliter
            verify: if True, will verify the calibration was set correctly
            max_retries: number of retries if verification fails

        Returns:
            bool: True if calibration was set successfully (or not verified)
        """
        ms_per_ul = float(ms_per_ul)
        for attempt in range(max_retries):
            self.drain_rx(print_lines=False)

            self.send(f"SET_CALIB_FACTOR:{ms_per_ul:.2f}")
            time.sleep(0.2)

            if verify:
                self.send("GET_CALIB_FACTOR")
                time.sleep(0.1)
                resp = self._get_response(timeout=1.0, match_any=("GET_CALIB_FACTOR", "MS_TO_UL", "0.00"))
                self.setup_status["calibration_response"] = resp

                if resp is None:
                    if DEBUG:
                        print(f"[WD] Calibration verify attempt {attempt + 1}/{max_retries}: No response")
                    time.sleep(0.2)
                    continue

                value = self._extract_first_number(resp)
                if value is None:
                    if DEBUG:
                        print(f"[WD] Calibration verify attempt {attempt + 1}/{max_retries}: Could not parse response {resp!r}")
                    time.sleep(0.2)
                    continue

                if abs(value - ms_per_ul) > 0.05:
                    if DEBUG:
                        print(f"[WD] Calibration verify attempt {attempt + 1}/{max_retries}: Got {value:.2f}, expected {ms_per_ul:.2f}, retrying...")
                    time.sleep(0.3)
                    continue

                if DEBUG:
                    print(f"[WD] Calibration set successfully: {resp}")
                self.setup_status["calibration_verified"] = True
                return True
            else:
                self.setup_status["calibration_verified"] = True
                return True

        print(f"[WD] WARNING: Could not verify calibration was set after {max_retries} attempts")
        self.setup_status["calibration_verified"] = False
        return False

    def set_lick_threshold(self, threshold: int, verify: bool = True, max_retries: int = 3):
        threshold = max(0, min(255, int(threshold)))

        for attempt in range(max_retries):
            self.drain_rx(print_lines=False)
            self.send(f"SET_LICK_THRESHOLD:{threshold}")
            time.sleep(0.15)

            if not verify:
                self.setup_status["lick_threshold_verified"] = True
                return True

            self.send("GET_LICK_THRESHOLD")
            time.sleep(0.1)
            resp = self._get_response(timeout=1.0, match_any=("GET_LICK_THRESHOLD", "LICK_THRESHOLD"))
            self.setup_status["lick_threshold_response"] = resp

            if resp is None:
                if DEBUG:
                    print(f"[WD] Lick threshold verify attempt {attempt + 1}/{max_retries}: No response")
                time.sleep(0.2)
                continue

            value = self._extract_first_number(resp)
            if value is None:
                if DEBUG:
                    print(f"[WD] Lick threshold verify attempt {attempt + 1}/{max_retries}: Could not parse response {resp!r}")
                time.sleep(0.2)
                continue

            if int(round(value)) != threshold:
                if DEBUG:
                    print(f"[WD] Lick threshold verify attempt {attempt + 1}/{max_retries}: Got {value}, expected {threshold}, retrying...")
                time.sleep(0.2)
                continue

            if DEBUG:
                print(f"[WD] Lick threshold set successfully: {resp}")
            self.setup_status["lick_threshold_verified"] = True
            return True

        print(f"[WD] WARNING: Could not verify lick threshold was set after {max_retries} attempts")
        self.setup_status["lick_threshold_verified"] = False
        return False

    def verify_setup(self, calib_factor=SET_CALIB_FACTOR, lick_threshold=SET_LICK_THRESHOLD, max_retries=WD_VERIFY_MAX_RETRIES):
        """
        Apply and verify required startup settings before allowing a session to start.
        """
        calib_ok = self.set_calib_factor(calib_factor, verify=True, max_retries=max_retries)
        lick_ok = self.set_lick_threshold(lick_threshold, verify=True, max_retries=max_retries)

        self.send("GET_PARAMETERS")
        params = self._get_response(timeout=0.75, match_any=("GET_PARAMETERS", "NUMBER_OF_PULSE", "WATER_QTY", "MS_TO_UL"))
        if DEBUG:
            print(f"[WD] GET_PARAMETERS -> {params}")

        return calib_ok and lick_ok

    # ------------------------------------------------------------------
    # Send pulse command to dispense water
    # ------------------------------------------------------------------
    def pulse(
        self,
        n_pulses: int = WD_PULSE_N,
        qty_uL: float = WD_PULSE_QTY_UL,
        freq_hz: float = WD_PULSE_FREQ_HZ,
        n_seq: int = WD_PULSE_N_SEQ,
        delay_ms: int = WD_PULSE_DELAY_MS,
        *,
        confirm_params: bool = False,
    ):
        cmd_set = f"SET:{n_pulses}:{qty_uL}:{freq_hz}:{n_seq}:{delay_ms}"
        self.send(cmd_set)

        if confirm_params:
            self.send("GET_PARAMETERS")
            params = self._get_response(timeout=0.75, match_any=("GET_PARAMETERS", "NUMBER_OF_PULSE", "WATER_QTY", "MS_TO_UL"))
            print(f"[WD] GET_PARAMETERS -> {params}")

        cmd_pulse = f"PULSE:{n_pulses}:{qty_uL}:{freq_hz}:{n_seq}:{delay_ms}"
        self.send(cmd_pulse)

    def get_lick_total(self) -> int:
        """Return total lick count."""
        with self._lock:
            return self._lick_total

    def get_update_lick(self) -> int:
        """
        Return number of licks since the last time get_update_lick() was called.
        """
        with self._lock:
            delta = self._lick_total - self._last_returned_total
            self._last_returned_total = self._lick_total
        return max(0, delta)

    def reset_lick_counter(self):
        """Reset lick totals — resets both Python counters and hardware sensor."""
        self.send("RESET")
        if DEBUG:
            print("[WD] RESET command sent to hardware lick sensor")
        time.sleep(0.05)

        with self._lock:
            self._lick_total = 0
            self._last_returned_total = 0
        if DEBUG:
            print("[WD] Python lick counters zeroed")

    # ------------------------------------------------------------------
    # Clean up and close
    # ------------------------------------------------------------------
    def close(self):
        if DEBUG:
            print("[WD] Closing connection")
        self._stop.set()
        try:
            self._thread.join(timeout=0.5)
        except Exception:
            pass
        time.sleep(0.05)
        try:
            self.ser.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# ----------------------------------------------------------------------
# Shared event logging helpers
# ----------------------------------------------------------------------

def _fmt(val):
    """Format a value for CSV: floats use comma as decimal point (European locale)."""
    if isinstance(val, float):
        return f"{val:.4f}".replace(".", ",")
    return val if val is not None else ""


def _write_event_row(
    shared_state, trigger_t_rel, dispense_t_rel, event_type, reason, sample,
    hold_duration_s=None,
    peak_dist_mm=None, peak_x_mm=None, peak_y_mm=None,
    peak_time_s=None, time_to_peak_s=None,
):
    """Write a single row to the events CSV file."""
    writer = shared_state.get("events_csv_writer", None)
    if writer is None:
        return

    baseline = shared_state.get("session_baseline", None)

    if sample is not None:
        x_mm = sample.get("x_mm", "")
        y_mm = sample.get("y_mm", "")
        sample_idx = sample.get("sample_idx", "")
        sample_t_s = sample.get("t_s", "")
    else:
        x_mm = y_mm = sample_idx = sample_t_s = ""

    if sample is not None and baseline is not None:
        dx = x_mm - baseline[0]
        dy = y_mm - baseline[1]
        eucl = math.sqrt(dx**2 + dy**2)
    else:
        dx, dy, eucl = "", "", ""

    writer.writerow([
        _fmt(trigger_t_rel),
        _fmt(dispense_t_rel),
        sample_idx,
        _fmt(sample_t_s),
        event_type,
        reason,
        _fmt(x_mm),
        _fmt(y_mm),
        _fmt(dx),
        _fmt(dy),
        _fmt(eucl),
        _fmt(hold_duration_s),
        _fmt(peak_dist_mm),
        _fmt(peak_x_mm),
        _fmt(peak_y_mm),
        _fmt(peak_time_s),
        _fmt(time_to_peak_s),
    ])

    fh = shared_state.get("events_csv_fh", None)
    if fh:
        fh.flush()


def _run_peak_tracking(shared_state, trigger_t_rel, dispense_t_rel, reason, trigger_sample):
    """
    Background thread: poll latest_sample for REWARD_PEAK_TRACKING_WINDOW_S after a reward,
    find the maximum displacement from session baseline, then write the event CSV row.
    """
    baseline = shared_state.get("session_baseline")
    t0_wall = shared_state.get("t0")

    if trigger_sample is not None and baseline is not None:
        tx = trigger_sample.get("x_mm")
        ty = trigger_sample.get("y_mm")
        if tx is not None and ty is not None:
            trigger_dist = math.sqrt((tx - baseline[0]) ** 2 + (ty - baseline[1]) ** 2)
        else:
            tx = ty = trigger_dist = None
    else:
        tx = ty = trigger_dist = None

    peak_dist = trigger_dist if trigger_dist is not None else 0.0
    peak_x = tx
    peak_y = ty
    peak_t_s = trigger_t_rel

    wall_start = time.perf_counter()
    while time.perf_counter() - wall_start < REWARD_PEAK_TRACKING_WINDOW_S:
        sample = shared_state.get("latest_sample")
        if sample is not None and baseline is not None:
            sx = sample.get("x_mm")
            sy = sample.get("y_mm")
            if sx is not None and sy is not None:
                d = math.sqrt((sx - baseline[0]) ** 2 + (sy - baseline[1]) ** 2)
                if d > peak_dist:
                    peak_dist = d
                    peak_x = sx
                    peak_y = sy
                    if t0_wall is not None:
                        peak_t_s = time.perf_counter() - t0_wall
        time.sleep(0.005)

    time_to_peak = (peak_t_s - trigger_t_rel) if (peak_t_s is not None and trigger_t_rel is not None) else None

    peak_entry = {
        "peak_dist_mm": peak_dist,
        "peak_x_mm": peak_x,
        "peak_y_mm": peak_y,
        "peak_time_s": peak_t_s,
        "time_to_peak_s": time_to_peak,
    }
    reward_peak_data = shared_state.get("reward_peak_data")
    if reward_peak_data is not None:
        reward_peak_data.append(peak_entry)

    _write_event_row(
        shared_state, trigger_t_rel, dispense_t_rel, "reward", reason, trigger_sample,
        peak_dist_mm=peak_dist,
        peak_x_mm=peak_x,
        peak_y_mm=peak_y,
        peak_time_s=peak_t_s,
        time_to_peak_s=time_to_peak,
    )


def _wait_for_peak_windows(shared_state, timeout_s=1.5):
    """Join all active peak-tracking threads before flushing/closing the CSV."""
    for pt in shared_state.get("_peak_threads", []):
        if pt.is_alive():
            pt.join(timeout=timeout_s)


def log_nose_poke_event(shared_state, *, nose_present: bool, event_t_rel=None, reason=None):
    """
    Log a nose poke state change to the events CSV.

    Parameters
    ----------
    nose_present : bool
        True when beam is blocked / nose present, False when beam is clear.
    event_t_rel : float | None
        Relative event time. If None, this is computed from shared_state['t0'].
    reason : str | None
        Optional custom reason string. Defaults to 'present' or 'clear'.
    """
    if event_t_rel is None:
        t0 = shared_state.get("t0", None)
        event_t_rel = (time.perf_counter() - t0) if t0 is not None else None

    sample = shared_state.get("latest_sample", None)

    if reason is None:
        reason = "present" if nose_present else "clear"

    hold_duration_s = shared_state.get("nose_poke_duration_s") if not nose_present else None

    _write_event_row(
        shared_state,
        trigger_t_rel=event_t_rel,
        dispense_t_rel=None,
        event_type="nose_poke",
        reason=reason,
        sample=sample,
        hold_duration_s=hold_duration_s,
    )


def log_lick_event(shared_state, lick_count):
    """Log a lick event to the events CSV and session summary tracker."""
    t0 = shared_state.get("t0", None)
    t_rel = (time.perf_counter() - t0) if t0 is not None else None
    sample = shared_state.get("latest_sample", None)

    reward_summary = shared_state.get("reward_summary", [])
    t_since_reward = None
    if reward_summary and t_rel is not None:
        t_since_reward = t_rel - reward_summary[-1][0]

    lick_summary = shared_state.get("lick_summary")
    if lick_summary is not None:
        lick_summary.append((t_rel, lick_count, t_since_reward))

    _write_event_row(shared_state, t_rel, None, "lick", f"count={lick_count}", sample)


def log_blocked_reward_event(shared_state, reason):
    """Log a reward attempt that was blocked (e.g. nose poke not present) to the events CSV."""
    t0 = shared_state.get("t0", None)
    t_rel = (time.perf_counter() - t0) if t0 is not None else None
    sample = shared_state.get("latest_sample", None)

    _write_event_row(shared_state, t_rel, None, "reward_blocked", reason, sample)


def write_session_summary_csv(shared_state):
    """Write a session summary CSV combining reward and lick events, sorted by time."""
    _wait_for_peak_windows(shared_state)

    summary_csv_path = shared_state.get("summary_csv_path")
    if not summary_csv_path:
        return

    baseline = shared_state.get("session_baseline")
    reward_summary = shared_state.get("reward_summary", [])
    lick_summary = shared_state.get("lick_summary", [])
    hold_durations = shared_state.get("nose_poke_reward_hold_durations", [])

    reward_peak_data = shared_state.get("reward_peak_data", [])

    rows = []
    for i, (t_rel, reason, pos_mm) in enumerate(reward_summary):
        dx = dy = eucl = ""
        if pos_mm is not None and baseline is not None:
            dx = pos_mm[0] - baseline[0]
            dy = pos_mm[1] - baseline[1]
            eucl = math.sqrt(dx**2 + dy**2)
        hold_s = hold_durations[i] if i < len(hold_durations) else ""
        peak = reward_peak_data[i] if i < len(reward_peak_data) else {}
        rows.append((
            t_rel, "reward", reason, "", "", dx, dy, eucl, hold_s,
            peak.get("peak_dist_mm", ""),
            peak.get("peak_x_mm", ""),
            peak.get("peak_y_mm", ""),
            peak.get("peak_time_s", ""),
            peak.get("time_to_peak_s", ""),
        ))

    for t_rel, lick_count, t_since_reward in lick_summary:
        rows.append((t_rel, "lick", "", lick_count, t_since_reward, "", "", "", "",
                     "", "", "", "", ""))

    rows.sort(key=lambda r: (r[0] if r[0] is not None else float("inf")))

    try:
        with open(summary_csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, delimiter=CSV_DELIMITER)
            writer.writerow([
                "event_no", "event_type", "time_s", "reason",
                "lick_count", "t_since_reward_s",
                "delta_x_mm", "delta_y_mm", "euclidean_dist_mm",
                "hold_duration_s",
                "peak_dist_mm_after_reward",
                "peak_x_mm_after_reward", "peak_y_mm_after_reward",
                "peak_time_s_after_reward", "time_to_peak_s",
            ])
            for idx, (t_rel, etype, reason, lick_count, t_since_reward,
                      dx, dy, eucl, hold_s,
                      peak_dist, peak_x, peak_y, peak_t, time_to_peak) in enumerate(rows, 1):
                writer.writerow([
                    idx,
                    etype,
                    _fmt(t_rel),
                    reason,
                    lick_count,
                    _fmt(t_since_reward),
                    _fmt(dx),
                    _fmt(dy),
                    _fmt(eucl),
                    _fmt(hold_s),
                    _fmt(peak_dist),
                    _fmt(peak_x),
                    _fmt(peak_y),
                    _fmt(peak_t),
                    _fmt(time_to_peak),
                ])
        print(f"[INFO] Session summary CSV saved to: {summary_csv_path}")
    except Exception as e:
        print(f"[ERROR] Failed to write session summary CSV: {e}")


def try_deliver_reward(
    shared_state,
    *,
    reason="reward",
    n_pulses=None,
    qty_uL=None,
    freq_hz=None,
    n_seq=None,
    delay_ms=None
):
    """
    Deliver a reward via the WaterDispenser in shared_state["wd"].

    Enforces cooldown using shared_state["last_reward_t"] and REWARD_COOLDOWN_S.
    Logs events (rewards, cooldowns, licks) to the events CSV.

    Returns:
        (delivered: bool, msg: str)
    """
    now = time.perf_counter()
    t0 = shared_state.get("t0", None)
    t_rel = (now - t0) if t0 is not None else None

    if DEBUG:
        print(f"[REWARD] try_deliver_reward called: now={now:.3f}, t0={t0}, t_rel={t_rel}, reason={reason}")

    last_t = shared_state.get("last_reward_t", -1e9)
    if (now - last_t) < REWARD_COOLDOWN_S:
        trigger_sample = shared_state.get("latest_sample", None)
        _write_event_row(shared_state, t_rel, None, "cooldown_block", reason, trigger_sample)

        remaining = REWARD_COOLDOWN_S - (now - last_t)
        t_str = f"t={t_rel:.1f}s" if t_rel is not None else "t=?"
        print(f"[REWARD] Cooldown active ({remaining:.1f}s remaining) — blocked at {t_str}  [{reason}]")
        if DEBUG:
            print(f"[REWARD] Cooldown block logged: t_rel={t_rel}, reason={reason}")
        return False, f"[REWARD] Cooldown active ({remaining:.2f}s remaining)."

    if not WD_IN_USE:
        return False, "[REWARD] WD_IN_USE=False (reward ignored)."

    wd = shared_state.get("wd", None)
    if wd is None:
        return False, "[REWARD] WaterDispenser not available (init failed or not connected)."

    shared_state["last_reward_t"] = now

    trigger_t_rel = t_rel
    trigger_sample = shared_state.get("latest_sample", None)

    if TRIGGER_WAIT_S and TRIGGER_WAIT_S > 0:
        if DEBUG:
            print(f"[REWARD] Waiting {TRIGGER_WAIT_S:.3f}s before sending pulse ({reason})")
        time.sleep(TRIGGER_WAIT_S)

    try:
        wd.pulse(
            n_pulses=WD_PULSE_N if n_pulses is None else n_pulses,
            qty_uL=WD_PULSE_QTY_UL if qty_uL is None else qty_uL,
            freq_hz=WD_PULSE_FREQ_HZ if freq_hz is None else freq_hz,
            n_seq=WD_PULSE_N_SEQ if n_seq is None else n_seq,
            delay_ms=WD_PULSE_DELAY_MS if delay_ms is None else delay_ms,
            confirm_params=DEBUG,
        )

        dispense_t_rel = (time.perf_counter() - t0) if t0 is not None else None

        pt = threading.Thread(
            target=_run_peak_tracking,
            args=(shared_state, trigger_t_rel, dispense_t_rel, reason, trigger_sample),
            daemon=True,
        )
        pt.start()
        _peak_threads = shared_state.get("_peak_threads")
        if _peak_threads is not None:
            _peak_threads.append(pt)

        if trigger_t_rel is not None:
            reward_summary = shared_state.get("reward_summary", [])
            pos_mm = None
            if trigger_sample is not None:
                pos_mm = (trigger_sample["x_mm"], trigger_sample["y_mm"])
            reward_summary.append((trigger_t_rel, reason, pos_mm))
            shared_state["reward_summary"] = reward_summary

            hold_durations = shared_state.get("nose_poke_reward_hold_durations")
            if hold_durations is not None:
                hold_durations.append(None)  # filled in at nose-clear with actual final duration

            label = "MANUAL" if "manual" in reason.lower() else "Reward"
            mins = int(trigger_t_rel // 60)
            secs = trigger_t_rel % 60

            dist_str = ""
            baseline = shared_state.get("session_baseline")
            if trigger_sample is not None and baseline is not None:
                dx = trigger_sample["x_mm"] - baseline[0]
                dy = trigger_sample["y_mm"] - baseline[1]
                dist_at_trigger = (dx**2 + dy**2) ** 0.5
                dist_str = f"  triggered_at_dist={dist_at_trigger:.1f}mm"

            print(f"[REWARD][{SYSTEM_ID}] {label} at {mins}:{secs:05.2f}{dist_str}")
            if DEBUG:
                print(f"  reason={reason}  total={len(reward_summary)}")
            return True, f"[REWARD] Delivered ({reason}) at t={trigger_t_rel:.3f}s."
        else:
            label = "MANUAL" if "manual" in reason.lower() else "Reward"
            print(f"[REWARD][{SYSTEM_ID}] {label}  (no session time reference)")
            if DEBUG:
                print(f"[REWARD] DELIVERED but NOT logged (t_rel is None): reason={reason}")
            return True, f"[REWARD] Delivered ({reason})."

    except Exception as e:
        return False, f"[REWARD] Failed: {e}"