"""
Launch two fully independent joystick-system processes from the same codebase.

Each process selects its own hardware profile using JOYSTICK_SYSTEM_ID, so the
systems do not share serial ports, GUI state, CSV files, camera triggers, or
water-dispenser connections.

Update the SYSTEM_IDS list if your profile names differ.
"""

import os
import sys
import subprocess
from pathlib import Path

SYSTEM_IDS = ["system_L", "system_R"]


def main():
    script_dir = Path(__file__).resolve().parent
    main_py = script_dir / "main.py"

    if not main_py.exists():
        raise FileNotFoundError(f"Could not find main.py next to launcher: {main_py}")

    processes = []

    try:
        for system_id in SYSTEM_IDS:
            env = os.environ.copy()
            env["JOYSTICK_SYSTEM_ID"] = system_id

            print(f"[LAUNCHER] Starting {system_id}...")
            proc = subprocess.Popen([sys.executable, str(main_py)], env=env)
            processes.append((system_id, proc))

        print("[LAUNCHER] Both joystick systems launched.")
        print("[LAUNCHER] Close each GUI window or press Ctrl+C here to stop both.")

        exit_code = 0
        for system_id, proc in processes:
            code = proc.wait()
            print(f"[LAUNCHER] {system_id} exited with code {code}")
            if code != 0:
                exit_code = code

        sys.exit(exit_code)

    except KeyboardInterrupt:
        print("\n[LAUNCHER] Ctrl+C detected. Terminating all child processes...")
        for system_id, proc in processes:
            if proc.poll() is None:
                proc.terminate()

        for system_id, proc in processes:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

        print("[LAUNCHER] All child processes terminated.")


if __name__ == "__main__":
    main()