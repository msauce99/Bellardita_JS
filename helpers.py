"""
Shared helpers: joystick signal conversions, serial parsing, and session file naming.
"""

import datetime
import pathlib

from configurations import (
    BYTES, ARDUINO_REF_V, JOYSTICK_CALIB_X, JOYSTICK_CALIB_Y,
    JOYSTICK_SAVE_DIR,
    First_Name, Last_Name, Mouse_ID, Time_point, Condition, Task, Data_type, Run, Batch,
    SYSTEM_ID,
)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def dist2(a, b):
    """Squared Euclidean distance between two 2-D points (avoids sqrt)."""
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


# ---------------------------------------------------------------------------
# Arduino serial parsing
# ---------------------------------------------------------------------------

def parse_xy(line: str):
    """
    Parse an 'x,y' line from the Arduino.
    Returns (x_raw, y_raw) as ints on success, None on failure.
    """
    if ',' not in line:
        return None
    parts = line.split(',')
    if len(parts) != 2:
        return None
    try:
        return (int(parts[0].strip()), int(parts[1].strip()))
    except ValueError:
        print(f"[WARN] ValueError parsing serial line: {line!r}")
        return None


# ---------------------------------------------------------------------------
# ADC → physical unit conversions
# ---------------------------------------------------------------------------

def raw_to_volts(raw_coords):
    """Convert raw ADC values to volts: V = (ADC_CODE × ADC_REF) / ADC_MAX."""
    raw_x, raw_y = raw_coords
    return (raw_x * ARDUINO_REF_V) / BYTES, (raw_y * ARDUINO_REF_V) / BYTES


def volts_to_mm(vx, vy):
    """Convert volts to mm using per-system calibration factors from configurations.py."""
    return vx / JOYSTICK_CALIB_X, vy / JOYSTICK_CALIB_Y


# ---------------------------------------------------------------------------
# Session CSV file naming
# ---------------------------------------------------------------------------

def make_unique_csv_name(data_label, run_label=None):
    """
    Return a unique CSV path under JOYSTICK_SAVE_DIR.

    Format:
      FirstName_LastName_MouseID_TimePoint_Condition_Task_DataType_Run_Batch_SystemID
      _<data_label>_<timestamp>_<NNN>.csv

    A three-digit numeric suffix guarantees uniqueness within the same second.
    """
    data_dir = pathlib.Path(JOYSTICK_SAVE_DIR)
    data_dir.mkdir(exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base = (
        f"{First_Name}_{Last_Name}_{Mouse_ID}_{Time_point}"
        f"_{Condition}_{Task}_{Data_type}_{(run_label or Run)}_{Batch}_{SYSTEM_ID}"
        f"_{data_label}_{timestamp}"
    )

    uid = 1
    while True:
        candidate = data_dir / f"{base}_{uid:03d}.csv"
        if not candidate.exists():
            return candidate
        uid += 1
