import csv
import time
import pathlib
import statistics
import serial
from datetime import datetime


# =============================================================================
# USER CONFIGS - EDIT THESE BEFORE EACH CALIBRATION CAPTURE
# =============================================================================
JOYSTICK_ID = "JS_right"
CALIB_AXIS = "x2"          # "x" or "y"
SET_DISTANCE_MM = 20.0     # known imposed distance in mm

ARD_SERIAL_PORT = "COM5"
BAUD = 115200
TIMEOUT_S = 0.5

N_SAMPLES = 30
SAVE_DIR = r"C:\Users\mahin\OneDrive\Documents\THESIS\data\Calibration_17.4"

SETTLE_TIME_S = 0.5

DEBUG = True
MAX_CAPTURE_TIME_S = 20.0
PRINT_EVERY_VALID_SAMPLE = True

SEND_START_COMMAND = True
SEND_END_COMMAND = True
ARDUINO_BOOT_WAIT_S = 2.5

# ADC conversion
ARDUINO_BIT = 14
ARDUINO_REF_V = 5.0  
BYTES = (2 ** ARDUINO_BIT) - 1
# =============================================================================


def parse_xy(line: str):
    """
    Parse Arduino line of the form 'x,y' into integer tuple (raw_x, raw_y).
    Returns None if parsing fails.
    """
    if "," not in line:
        return None

    parts = line.split(",")
    if len(parts) != 2:
        return None

    try:
        raw_x = int(parts[0].strip())
        raw_y = int(parts[1].strip())
        return raw_x, raw_y
    except ValueError:
        return None


def raw_to_volts(raw_coords):
    """
    Convert raw ADC values to volts using the formula:
    V = (ADC_CODE * ADC_REF) / BYTES
    """
    raw_x, raw_y = raw_coords
    vx = (raw_x * ARDUINO_REF_V) / BYTES
    vy = (raw_y * ARDUINO_REF_V) / BYTES
    return vx, vy


def make_output_path():
    save_dir = pathlib.Path(SAVE_DIR)
    save_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base_name = (
        f"joystick_calibration"
        f"_JoystickID-{JOYSTICK_ID}"
        f"_Axis-{CALIB_AXIS}"
        f"_DistanceMM-{SET_DISTANCE_MM}"
        f"_{timestamp}"
    )

    uid = 1
    while True:
        candidate = save_dir / f"{base_name}_{uid:03d}.csv"
        if not candidate.exists():
            return candidate
        uid += 1


def collect_samples(ser, n_samples):
    """
    Collect n_samples valid x,y samples from the Arduino.
    Ignores malformed / non-data lines.
    Stops with TimeoutError if it takes too long.
    """
    samples = []
    invalid_count = 0
    empty_count = 0
    t_start = time.perf_counter()

    print(f"[INFO] Waiting for {n_samples} valid samples...")
    print(f"[INFO] Max capture time: {MAX_CAPTURE_TIME_S:.1f} s")

    while len(samples) < n_samples:
        elapsed = time.perf_counter() - t_start
        if elapsed > MAX_CAPTURE_TIME_S:
            raise TimeoutError(
                f"Timed out after {elapsed:.1f}s. "
                f"Collected {len(samples)}/{n_samples} valid samples, "
                f"{invalid_count} invalid lines, {empty_count} empty reads."
            )

        raw = ser.readline()

        if not raw:
            empty_count += 1
            if DEBUG and empty_count % 10 == 0:
                print(f"[DEBUG] Empty serial reads so far: {empty_count}")
            continue

        line = raw.decode(errors="ignore").strip()

        if DEBUG:
            print(f"[DEBUG] Raw serial line: {line!r}")

        parsed = parse_xy(line)
        if parsed is None:
            invalid_count += 1
            if DEBUG:
                print(f"[DEBUG] Ignored non x,y line #{invalid_count}: {line!r}")
            continue

        raw_x, raw_y = parsed
        volt_x, volt_y = raw_to_volts((raw_x, raw_y))

        sample_idx = len(samples) + 1
        samples.append((sample_idx, raw_x, raw_y, volt_x, volt_y))

        if PRINT_EVERY_VALID_SAMPLE:
            print(
                f"[VALID] Sample {sample_idx}/{n_samples}: "
                f"raw_x={raw_x}, raw_y={raw_y}, "
                f"volt_x={volt_x:.6f}, volt_y={volt_y:.6f}"
            )

    print(f"[INFO] Finished capture: {len(samples)} valid samples collected.")
    return samples


def write_csv(csv_path, samples):
    raw_x_vals = [row[1] for row in samples]
    raw_y_vals = [row[2] for row in samples]
    volt_x_vals = [row[3] for row in samples]
    volt_y_vals = [row[4] for row in samples]

    mean_x = statistics.mean(raw_x_vals)
    mean_y = statistics.mean(raw_y_vals)
    mean_vx = statistics.mean(volt_x_vals)
    mean_vy = statistics.mean(volt_y_vals)

    median_x = statistics.median(raw_x_vals)
    median_y = statistics.median(raw_y_vals)
    median_vx = statistics.median(volt_x_vals)
    median_vy = statistics.median(volt_y_vals)

    std_x = statistics.stdev(raw_x_vals) if len(raw_x_vals) > 1 else 0.0
    std_y = statistics.stdev(raw_y_vals) if len(raw_y_vals) > 1 else 0.0
    std_vx = statistics.stdev(volt_x_vals) if len(volt_x_vals) > 1 else 0.0
    std_vy = statistics.stdev(volt_y_vals) if len(volt_y_vals) > 1 else 0.0

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        writer.writerow(["joystick_id", JOYSTICK_ID])
        writer.writerow(["calib_axis", CALIB_AXIS])
        writer.writerow(["set_distance_mm", SET_DISTANCE_MM])
        writer.writerow(["arduino_port", ARD_SERIAL_PORT])
        writer.writerow(["baud", BAUD])
        writer.writerow(["n_samples", N_SAMPLES])
        writer.writerow(["arduino_bit", ARDUINO_BIT])
        writer.writerow(["arduino_ref_v", ARDUINO_REF_V])
        writer.writerow(["timestamp", datetime.now().isoformat(timespec="seconds")])
        writer.writerow([])

        writer.writerow(["sample_idx", "raw_x", "raw_y", "volt_x", "volt_y"])
        writer.writerows(samples)
        writer.writerow([])

        writer.writerow(["summary_metric", "raw_x", "raw_y", "volt_x", "volt_y"])
        writer.writerow(["mean", mean_x, mean_y, mean_vx, mean_vy])
        writer.writerow(["median", median_x, median_y, median_vx, median_vy])
        writer.writerow(["std", std_x, std_y, std_vx, std_vy])
        writer.writerow(["min", min(raw_x_vals), min(raw_y_vals), min(volt_x_vals), min(volt_y_vals)])
        writer.writerow(["max", max(raw_x_vals), max(raw_y_vals), max(volt_x_vals), max(volt_y_vals)])

    return {
        "mean_x": mean_x,
        "mean_y": mean_y,
        "mean_vx": mean_vx,
        "mean_vy": mean_vy,
        "median_x": median_x,
        "median_y": median_y,
        "median_vx": median_vx,
        "median_vy": median_vy,
        "std_x": std_x,
        "std_y": std_y,
        "std_vx": std_vx,
        "std_vy": std_vy,
    }


def main():
    print("\n=== Joystick Distance Calibration Capture ===\n")
    print(f"Joystick ID     : {JOYSTICK_ID}")
    print(f"Calibration axis: {CALIB_AXIS}")
    print(f"Set distance mm : {SET_DISTANCE_MM}")
    print(f"Serial port     : {ARD_SERIAL_PORT}")
    print(f"Samples to take : {N_SAMPLES}")
    print(f"Debug mode      : {DEBUG}")
    print()

    csv_path = make_output_path()
    ser = None

    try:
        ser = serial.Serial(ARD_SERIAL_PORT, BAUD, timeout=TIMEOUT_S)
        print(f"[INFO] Opened serial port {ARD_SERIAL_PORT} @ {BAUD}")
    except Exception as e:
        print(f"[ERROR] Could not open serial port {ARD_SERIAL_PORT}: {e}")
        print("[ERROR] Make sure Arduino Serial Monitor is closed.")
        return

    try:
        print(f"[INFO] Waiting {ARDUINO_BOOT_WAIT_S:.2f} seconds for Arduino reset/boot...")
        time.sleep(ARDUINO_BOOT_WAIT_S)

        print("[INFO] Resetting input/output buffers...")
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        input(
            "Position the joystick at the desired calibration distance, "
            "then press Enter to collect samples..."
        )

        if SETTLE_TIME_S > 0:
            print(f"[INFO] Settling for {SETTLE_TIME_S:.2f} s...")
            time.sleep(SETTLE_TIME_S)

        if SEND_START_COMMAND:
            print("[INFO] Sending start command 'S' to Arduino...")
            ser.write(b"S")
            ser.flush()
            time.sleep(0.2)

        samples = collect_samples(ser, N_SAMPLES)
        stats = write_csv(csv_path, samples)

        if SEND_END_COMMAND:
            print("[INFO] Sending end command 'E' to Arduino...")
            ser.write(b"E")
            ser.flush()

        print("\n[INFO] Capture complete.")
        print(f"[INFO] Saved to: {csv_path}")
        print(f"[INFO] Mean raw_x : {stats['mean_x']:.3f}")
        print(f"[INFO] Mean raw_y : {stats['mean_y']:.3f}")
        print(f"[INFO] Mean volt_x: {stats['mean_vx']:.6f}")
        print(f"[INFO] Mean volt_y: {stats['mean_vy']:.6f}")
        print(f"[INFO] Std raw_x  : {stats['std_x']:.3f}")
        print(f"[INFO] Std raw_y  : {stats['std_y']:.3f}")
        print(f"[INFO] Std volt_x : {stats['std_vx']:.6f}")
        print(f"[INFO] Std volt_y : {stats['std_vy']:.6f}")

    except TimeoutError as e:
        print(f"\n[ERROR] {e}")
        print("[ERROR] Likely causes:")
        print("  - Arduino sketch requires a different start command")
        print("  - Arduino is not running the expected joystick sketch")
        print("  - Arduino Serial Monitor was open before running")
        print("  - board reset timing is too short after port open")
    except KeyboardInterrupt:
        print("\n[INFO] Capture interrupted by user.")
    finally:
        try:
            if ser is not None and ser.is_open and SEND_END_COMMAND:
                ser.write(b"E")
                ser.flush()
        except Exception:
            pass

        try:
            if ser is not None and ser.is_open:
                ser.close()
                print("[INFO] Serial port closed.")
        except Exception:
            pass


if __name__ == "__main__":
    main()