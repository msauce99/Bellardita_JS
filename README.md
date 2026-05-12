# Joystick Overview

This repository contains code for a modified joystick tracking and behavioral experiment platform intended for mice, including joystick displacement tracking, reward delivery, camera triggering, modular trial control, and nose-poke integration.

It supports running **two independent chambers/systems in parallel** by launching **two separate Python processes**, each with its own hardware profile.

## System Overview

**Hardware per chamber/system:**
- Arduino Uno reading joystick analog signals
- Optional water dispenser module for reward delivery
- Optional camera with hardware trigger (TTL via Arduino digital pin)
- Optional nose-poke photo interrupter input read on an Arduino digital pin

**Software:**
- Real-time matplotlib GUI with live joystick trail visualization
- Multi-threaded architecture (serial reader, animation, trial control, water dispenser)
- Multiple modes:
  - Joystick training
  - Timed trial
  - Movement trial
  - Standalone nose-poke hold training
- Data logging:
  - position CSV
  - event logging CSV for rewards, licks, and nose-poke state changes
  - session summary CSV
- Multi-system support through per-process hardware profiles selected with `JOYSTICK_SYSTEM_ID`

## File Descriptions

### Arduino (`Joystick_Pos.ino`)
- Reads joystick analog inputs on pins A0 (X) and A1 (Y)
- 14-bit ADC resolution, baseline centering, oversampling
- Streams joystick data as `X,Y`
- Reads nose-poke digital input on the configured nose-poke pin
- Sends separate nose-poke event strings:
  - `NOSE_POKE:1` = beam blocked / nose present
  - `NOSE_POKE:0` = beam clear / no nose present
- TTL trigger output on digital pin for camera

### Python Configuration (`configurations.py`)
Central configuration file. Edit to customize:
- Shared experiment settings
- Hardware profile selection via `JOYSTICK_SYSTEM_ID`
- Per-system mappings for:
  - Arduino COM port
  - water dispenser COM port
  - camera trigger pin
  - nose-poke digital pin
  - save directory
  - training stage

### Core Visualization (`joystick_pos.py`)
**Main Functions:**
- `figure_setup()` — creates matplotlib figure and artists
- `make_update()` — builds animation callback function
- `start_animation()` — starts matplotlib animation loop
- `_serial_reader()` — background thread: reads Arduino serial, parses joystick samples, parses nose-poke events, logs position CSV, polls lick updates

**Behavior:**
- live GUI shows joystick position and trail
- live GUI also shows current nose-poke state, hold duration, and cumulative reward count for the session

### Trial Control (`trial_controller.py`)
Implements experiment flow for non-training modes:
- waits for `start_event` from `main.py`
- supports:
  - **Timed Trial** — ends after `TRIAL_DURATION_S`
  - **Movement Trial** — ends after `TRIAL_END_REWARD_ZONE_HITS`
- supports reward-condition modes:
  - `outside_radius`
  - `box`
- can start camera triggering immediately or after joystick movement

### Training Controller (`training.py`)
Implements joystick training mode:
- **Stage 1:** reward when joystick moves beyond radius `TRAINING_TOUCH_R_MM`
- **Stage 2:** reward when joystick moves outside radius `TRAINING_OUTSIDE_R_MM`
- **Stage 3:** reward when joystick enters the configured target box

### Standalone Nose-Poke Training (`nose_poke_training.py`)
Implements automatic nose-poke-only hold training:
- no joystick reward logic
- reward is automatically delivered when the mouse maintains a nose poke for the configured hold time
- one reward maximum per continuous hold & mouse must leave and re-enter to start a new hold attempt
- logs nose-poke state changes and rewards to the events CSV

### Water Dispenser Control (`water_dispenser.py`)
Serial interface to LabeoTech water dispenser:
- `WaterDispenser` class for connection, command sending, and lick counting
- `try_deliver_reward()` for reward delivery with cooldown
- event logging support for rewards, licks, nose-poke state changes

### Camera Trigger Control (`camera.py`)
Arduino TTL pulse generation for hardware camera triggering:
- `start_camera()` — sends `'C'` to Arduino to begin continuous TTL pulses
- `stop_camera()` — sends `'c'` to Arduino to stop TTL pulses
- used by training and trial controllers, and can also be toggled manually from the GUI / terminal when enabled

### Utility Functions (`helpers.py`)
- `dist2()` — squared Euclidean distance
- `parse_xy()` — parses `X,Y` string from Arduino
- `raw_to_volts()` — converts ADC counts to voltage
- `volts_to_mm()` — converts voltage to joystick displacement using calibration factors
- `make_unique_csv_name()` — builds a unique, timestamped CSV path under `JOYSTICK_SAVE_DIR` using the subject/session metadata fields from `configurations.py`

### Main Entrypoint (`main.py`)
1. Loads the active hardware profile from `configurations.py`
2. Opens that system's Arduino serial port
3. Initializes that system's water dispenser if enabled
4. Creates the matplotlib UI
5. Builds the animation update callback
6. Starts background threads:
   - serial reader
   - training controller or trial controller
   - input manager
7. Opens and closes CSV logging for each session
8. Cleans up on shutdown

### Dual-System Launcher (`launch_dual_systems.py`)
- starts one `main.py` process for `system_L`
- starts one `main.py` process for `system_R`
- both displayed in one terminal
- each process gets its own `JOYSTICK_SYSTEM_ID`

## Data Logging

### Position CSV - Joystick output only
Written only while a session is active:
- `sample_idx`
- `t_s`
- `raw_x`
- `raw_y`
- `volt_x`
- `volt_y`
- `x_mm`
- `y_mm`

### Events CSV
Written once per session. One row per event, appended in real time:
- `trigger_t_s` — time the event was detected / reward triggered (s from session start)
- `dispense_t_s` — time the WD pulse command was actually sent (reward rows only)
- `sample_idx` — joystick sample index at event time
- `sample_t_s` — joystick sample timestamp at event time (s from session start)
- `event_type` — `reward`, `reward_blocked`, `cooldown_block`, `lick`, `nose_poke`
- `reason` — specific reason string (e.g. `training_stage_1`, `present`, `clear`, `count=3`)
- `x_mm`, `y_mm` — absolute joystick position at event time
- `delta_x_mm`, `delta_y_mm` — joystick position relative to session baseline
- `euclidean_dist_mm` — distance from session baseline at event time
- `hold_duration_s` — for `nose_poke` clear events: total hold duration of that poke (s); for reward rows in NP training: hold time at moment of reward delivery

### Session Summary CSV
Written once at end of each session. Combines reward and lick events in chronological order:
- `event_no`
- `event_type` (`reward` or `lick`)
- `time_s`
- `reason`
- `lick_count`
- `t_since_reward_s` — time since last reward (lick rows only)
- `delta_x_mm`, `delta_y_mm`, `euclidean_dist_mm` — joystick position relative to baseline at reward time
- `hold_duration_s` — nose-poke hold duration at moment of reward (if applicable)

For **nose-poke training** sessions, four `session_stat` rows are appended after the events:
- `total_pokes` — total number of nose-poke entries
- `rewarded_pokes` — pokes that reached the hold threshold and received a reward
- `avg_hold_s` — average hold duration across all pokes
- `longest_hold_s` — longest single hold duration

## Threading Model

| Thread | Started By | Purpose | Blocking |
|--------|-----------|---------|----------|
| **Main** | Python | UI loop (`plt.show(...)`) | Yes |
| **Serial Reader** | `make_update()` | Continuous Arduino polling, joystick parsing, nose-poke parsing, CSV logging | Background |
| **Animation** | `start_animation()` | GUI update callback | Background |
| **Training / Trial Controller** | `main.py` | Session logic and reward/camera orchestration | Background |
| **Water Dispenser RX** | `WaterDispenser.__init__()` | Reads lick events and responses | Background |
| **Input Manager** | `main.py` | Start / stop / restart handling through terminal input | Background |

## Quick Start

### 1. Configure settings
In `configurations.py`, update the per-system hardware profiles (COM ports, calibration factors) and the shared settings below:
- `JOYSTICK_SAVE_DIR` = data output directory (single path, shared by both systems)
- `CAM_IN_USE` = `True` if using camera
- `WD_IN_USE` = `True` if using dispenser
- `NOSE_POKE_IN_USE` = `True` if using nose poke
- `NOSE_POKE_REQUIRED` = `True` if joystick rewards require a nose poke first (set to `False` for joystick-only reward)

For joystick training:
- set `TRAINING_MODE = True`
- choose `TRAINING_STAGE`

For joystick trials:
- set `TRAINING_MODE = False`
- set either `TIME_TRIAL = True` or `MOVEMENT_TRIAL = True`

For standalone nose-poke hold training:
- run `nose_poke_training.py`
- set `NOSE_POKE_TRAINING_STAGE`
- confirm the resulting `NOSE_POKE_HOLD_TIME_S`

### 2. Upload Arduino sketch
Open `Joystick_Pos.ino` in Arduino IDE:
- verify serial baud matches `DEFAULT_BAUD`
- verify joystick pins match hardware
- verify camera trigger pin matches hardware
- verify nose-poke pin matches hardware
- upload to the correct Arduino

### 3. Run one chamber
For joystick training or trials (PowerShell):
```powershell
$env:JOYSTICK_SYSTEM_ID="system_L"
python main.py
```

For standalone nose-poke training (PowerShell):
```powershell
$env:JOYSTICK_SYSTEM_ID="system_L"
python nose_poke_training.py
```

> **CMD alternative:** `set JOYSTICK_SYSTEM_ID=system_L` then `python main.py`  
> Use `system_L` or `system_R` to match the profile name in `configurations.py`.

### 4. Run two chambers simultaneously
   - **Automatic:** `python launch_dual_systems.py` — spawns both systems in parallel with correct IDs
   - **Manual (two separate terminals):**
     - Terminal 1: `$env:JOYSTICK_SYSTEM_ID="system_L"` → `python main.py`
     - Terminal 2: `$env:JOYSTICK_SYSTEM_ID="system_R"` → `python main.py`
   - All `[REWARD]` lines in the terminal include `[system_L]` or `[system_R]` so interleaved output is distinguishable

### 5. **During trial:**
   - Press Enter in the terminal to start
   - In the GUI, press 'w' for manual reward, 't' for camera toggle
   - In the terminal use 'w' for manual reward, 't' for camera toggle, 'c' to end the current session, q to quit program
   - Press Ctrl+C to shut down trial




