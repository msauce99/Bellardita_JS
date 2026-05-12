"""
Global configuration for the joystick system & visualizer.

"""

import os

DEBUG = False                  # enable debug prints

# ------------------------------------------------------------------------------------
# CSV SETTINGS
# ------------------------------------------------------------------------------------
# Use ";" for European PCs (Excel regional setting uses ; as list separator)
# Use "," for American PCs
CSV_DELIMITER = ";"
# ------------------------------------------------------------------------------------

# ------------------------------------------------------------------------------------
# ACTIVE SYSTEM PROFILE - environment variables
# ------------------------------------------------------------------------------------
# Run one Python process per chamber/system. Each process selects its own hardware
# mapping from SYSTEM_PROFILES using the JOYSTICK_SYSTEM_ID environment variable.
ACTIVE_SYSTEM_ID = os.environ.get("JOYSTICK_SYSTEM_ID", "system_L").strip()

SYSTEM_PROFILES = {
    "system_L": {
        "SYSTEM_NAME": "Chamber L",
        "ARD_SERIAL_PORT": "COM4",
        "WD_PORT": "COM7",
        "CAM_DIGITAL_PIN": 8,
        "NOSE_POKE_DIGITAL_PIN": 7,
        "JOYSTICK_CALIB_X": 0.0885,   # V/mm for X axis — update per physical calibration
        "JOYSTICK_CALIB_Y": 0.0836,   # V/mm for Y axis
        # --- Training mode (per-system) ---
        "TRAINING_MODE": True,
        "TRAINING_STAGE": 3,
        "TRAINING_DURATION_S": 60.0 * 30,
        "NOSE_POKE_REQUIRED": True,
        "NOSE_POKE_HOLD_REQUIRED": False,
        "NOSE_POKE_TRAINING_STAGE": 1,
        "NOSE_POKE_TRAINING_DURATION_S": 60.0 * 15,
    },
    "system_R": {
        "SYSTEM_NAME": "Chamber R",
        "ARD_SERIAL_PORT": "COM6",
        "WD_PORT": "COM5",
        "CAM_DIGITAL_PIN": 8,
        "NOSE_POKE_DIGITAL_PIN": 7,
        "JOYSTICK_CALIB_X": 0.0866,
        "JOYSTICK_CALIB_Y": 0.0883,
        # --- Training mode (per-system) ---
        "TRAINING_MODE": True,
        "TRAINING_STAGE": 1,
        "TRAINING_DURATION_S": 60.0 * 30,
        "NOSE_POKE_REQUIRED": True,
        "NOSE_POKE_HOLD_REQUIRED": False,
        "NOSE_POKE_TRAINING_STAGE": 1,
        "NOSE_POKE_TRAINING_DURATION_S": 60.0 * 15,
    },
}

# Save directory shared by both systems
JOYSTICK_SAVE_DIR = r"N:\SUN-IN-Common\SUN-IN-Bellardita-Lab\Mahina\np_training_april\10.05.2026_np_js"

if ACTIVE_SYSTEM_ID not in SYSTEM_PROFILES:
    valid_ids = ", ".join(SYSTEM_PROFILES.keys())
    raise ValueError(
        f"Unsupported JOYSTICK_SYSTEM_ID={ACTIVE_SYSTEM_ID!r}. Valid options: {valid_ids}"
    )

_ACTIVE_PROFILE = SYSTEM_PROFILES[ACTIVE_SYSTEM_ID]

SYSTEM_ID = ACTIVE_SYSTEM_ID
SYSTEM_NAME = _ACTIVE_PROFILE["SYSTEM_NAME"]

# ------------------------------------------------------------------------------------
# File Saving Parameters
# ------------------------------------------------------------------------------------
First_Name = "Mahina"      # Operator name
Last_Name = "S"
Mouse_ID = "m3L,66R"
Time_point = "Day12"
Condition = "wild"  # e.g. preop, postop, etc.
Task = "np_js"     # JS = joystick. Training vs Timed Trial vs Movement Trial
Data_type = "BEH"          # BEH = behavioral
Run = "R1"                 # for multiple runs on the same day, e.g. Run 1, Run 2, etc.
Batch = "BA"               # for different operators or samples
# ------------------------------------------------------------------------------------

# ------------------------------------------------------------------------------------
# Arduino Serial Parameters
# ------------------------------------------------------------------------------------
ARD_SERIAL_PORT = _ACTIVE_PROFILE["ARD_SERIAL_PORT"]  # hard-coded serial port for active system
DEFAULT_BAUD = 115200          # must match Serial.begin(...) on the Arduino
DEFAULT_TIMEOUT_S = 0.1        # serial readline() timeout (seconds) - reduced for faster shutdown
ARDUINO_BIT = 14               # Arduino ADC resolution (bits)
BYTES = 2**ARDUINO_BIT - 1     # 2^14 - 1 for 14-bit ADC (max ADC value)
ARDUINO_REF_V = 5.0            # Arduino ADC reference voltage (Volts)

# Joystick calibration factors pulled from the active system profile (V/mm, per physical calibration)
JOYSTICK_CALIB_X = _ACTIVE_PROFILE["JOYSTICK_CALIB_X"]
JOYSTICK_CALIB_Y = _ACTIVE_PROFILE["JOYSTICK_CALIB_Y"]
# ------------------------------------------------------------------------------------

# ------------------------------------------------------------------------------------
# CAMERA PARAMETERS (hardware trigger via Arduino TTL pulse on digital pin)
# ------------------------------------------------------------------------------------
CAM_IN_USE = False             # if True, Arduino sends TTL trigger pulses to camera
# *** The camera frame rate is hard coded into Arduino .ino file - set & upload before trial ***
CAM_DIGITAL_PIN = _ACTIVE_PROFILE["CAM_DIGITAL_PIN"]
CAM_SERIAL_TRIGGER_FPS = 100   # frame rate for serial TTL triggering (must change on Arduino or in Arduino .ino)

# Only set one of the following to True (mutually exclusive options for when to start camera triggering):
CAM_START_WITH_TRIAL = True     # if True, start continuous triggering at trial/session start
JOYSTICK_CAM_TRIGGER = False    # if True, continuous triggering starts only after joystick moves beyond threshold
JOYSTICK_CAM_TRIGGER_RADIUS_MM = 2.0  # joystick movement radius (mm) to start continuous triggering
# ------------------------------------------------------------------------------------

# ------------------------------------------------------------------------------------
# NOSE POKE PARAMETERS
# ------------------------------------------------------------------------------------
NOSE_POKE_IN_USE = True
NOSE_POKE_DIGITAL_PIN = _ACTIVE_PROFILE["NOSE_POKE_DIGITAL_PIN"]

# Beam-blocked = mouse nose present = True
# Arduino will send separate event strings:
#   NOSE_POKE:1   -> beam blocked / nose present
#   NOSE_POKE:0   -> beam clear / no nose present

# Per-system: whether nose poke gates joystick rewards, and hold behaviour
NOSE_POKE_REQUIRED       = _ACTIVE_PROFILE["NOSE_POKE_REQUIRED"]
NOSE_POKE_HOLD_REQUIRED  = _ACTIVE_PROFILE["NOSE_POKE_HOLD_REQUIRED"]

# Hold-time lookup table (shared across systems — stage number selects the entry)
NOSE_POKE_STAGE_HOLD_TIMES_S = {
    1: 2.0, # immediate reward upon NP detection
    2: 3.0, # Hold for 3 s
    3: 4.0, # Hold for 4 s
}

# Per-system nose-poke training settings
NOSE_POKE_TRAINING_STAGE    = _ACTIVE_PROFILE["NOSE_POKE_TRAINING_STAGE"]
NOSE_POKE_TRAINING_DURATION_S = _ACTIVE_PROFILE["NOSE_POKE_TRAINING_DURATION_S"]

if NOSE_POKE_TRAINING_STAGE not in NOSE_POKE_STAGE_HOLD_TIMES_S:
    valid_stages = ", ".join(str(k) for k in sorted(NOSE_POKE_STAGE_HOLD_TIMES_S))
    raise ValueError(
        f"[{ACTIVE_SYSTEM_ID}] Unsupported NOSE_POKE_TRAINING_STAGE={NOSE_POKE_TRAINING_STAGE!r}. "
        f"Valid options: {valid_stages}"
    )

NOSE_POKE_HOLD_TIME_S = NOSE_POKE_STAGE_HOLD_TIMES_S[NOSE_POKE_TRAINING_STAGE]
# ------------------------------------------------------------------------------------

# ------------------------------------------------------------------------------------
# WATER DISPENSER PARAMETERS
# ------------------------------------------------------------------------------------
WD_IN_USE = True                # if True, trial will send dispense commands to water dispenser via serial
WD_PORT = _ACTIVE_PROFILE["WD_PORT"]
WD_BAUD = 115200                # must match Serial.begin(...) on the water dispenser
REWARD_COOLDOWN_S = 2           # minimum time between rewards
TRIGGER_WAIT_S = 0.2            # min time between reward trigger and actual pulse command sent to WD - 200 ms

SET_CALIB_FACTOR = 12.00        # :<ms_per_ul>
SET_LICK_THRESHOLD = 15         # : <0-255> threshold 8bit
# 36 was not sufficient for logging licks for mice (detected finger touches)

WD_VERIFY_MAX_RETRIES = 3       # retries for calibration / lick-threshold verification

# PULSE: default parameters are 10:20:2.5:2:1000
WD_PULSE_N = 1
WD_PULSE_QTY_UL = 18
WD_PULSE_FREQ_HZ = 2.5
WD_PULSE_N_SEQ = 1
WD_PULSE_DELAY_MS = 1000
REWARD_PEAK_TRACKING_WINDOW_S = 1.0  # seconds to poll joystick after reward for peak displacement
# ------------------------------------------------------------------------------------

# ------------------------------------------------------------------------------------
# TRAINING Mode Parameters (per-system — set in SYSTEM_PROFILES above)
# ------------------------------------------------------------------------------------
TRAINING_MODE       = _ACTIVE_PROFILE["TRAINING_MODE"]       # if True, uses training parameters
TRAINING_STAGE      = _ACTIVE_PROFILE["TRAINING_STAGE"]      # 1= any touch, 2=outside radius, 3=small target
TRAINING_DURATION_S = _ACTIVE_PROFILE["TRAINING_DURATION_S"] # session time limit in seconds (0 = no limit)

# stage 1 threshold
TRAINING_TOUCH_R_MM = 0.75       # "any touch" = joystick moves >= this radius from baseline

# stage 2 threshold
TRAINING_OUTSIDE_R_MM = 5.0
# stage 3 threshold (smaller target zone 'BOX')
TRAINING_INNER_X_MM = - 5.0       # x-dimension of inner target zone (box) >= this distance from center
TRAINING_INNER_Y_MM = 2.5       # y-dimension of inner target zone (box) >= this distance from center
# ------------------------------------------------------------------------------------

# ------------------------------------------------------------------------------------
# TRIAL PARAMETERS
# ------------------------------------------------------------------------------------
# Trial Type - CHOOSE ONLY ONE TRIAL TYPE (TIME_TRIAL vs MOVEMENT_TRIAL)!!
TIME_TRIAL = False              # if True, sends a start byte to the Arduino to trigger a timed trial
MOVEMENT_TRIAL = True          # if True, ends trial based on reward-zone entries

if TIME_TRIAL and MOVEMENT_TRIAL:
    raise ValueError("TIME_TRIAL and MOVEMENT_TRIAL are mutually exclusive — set only one to True.")

if CAM_START_WITH_TRIAL and JOYSTICK_CAM_TRIGGER:
    raise ValueError("CAM_START_WITH_TRIAL and JOYSTICK_CAM_TRIGGER are mutually exclusive — set only one to True.")

# Time Trial Parameters
TRIAL_DURATION_S = 60.0 * 0.5   # trial duration in seconds if using timed trials (0 = no time limit)

# Trial reward mode
# options:
#   "outside_radius" = reward when joystick moves >= radius from session baseline
#   "box"            = reward when joystick enters a coordinate-defined box relative to session baseline
TRIAL_REWARD_MODE = "outside_radius"

# Trial reward mode parameters - outside radius
TRIAL_REWARD_RADIUS_MM = 5.0    # reward if joystick distance from session baseline is >= this radius

# Trial reward mode parameters - box
# Same style as training stage 3:
#   X must stay within [-TRIAL_BOX_X_MM, +TRIAL_BOX_X_MM]
#   Y must be >= TRIAL_BOX_Y_MM
TRIAL_BOX_X_MM = 2.0
TRIAL_BOX_Y_MM = 9.0

# Trial end / reset parameters
TRIAL_END_REWARD_ZONE_HITS = 5  # movement trial ends after this many reward-zone entries
REWARD_REARM_RADIUS_MM = 1.0    # joystick must return within this radius before next reward / zone hit can count
# ------------------------------------------------------------------------------------

# ------------------------------------------------------------------------------------
# Visualization Parameters
# ------------------------------------------------------------------------------------
TRAIL_LENGTH = 500
VISUAL_RATE_HZ = 30
ANIM_INTERVAL_MS = int(1000 / VISUAL_RATE_HZ)

CLAMP_RADIUS = 10
ZOOM_MARGIN = 0.5
# ------------------------------------------------------------------------------------