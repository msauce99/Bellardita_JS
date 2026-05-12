"""
lick_sensitivity_test.py

Test lick detection sensitivity.
- Connects to water dispenser and verifies calibration
- Prints each lick detection to the terminal with a running count
- Type 'w' + Enter to trigger a manual reward
- Press Ctrl+C or type 'q' + Enter to quit
"""

import threading
import time

from configurations import WD_IN_USE
from water_dispenser import WaterDispenser


def main():
    if not WD_IN_USE:
        print("[ERROR] WD_IN_USE=False in configurations.py — enable it before running this test.")
        return

    print("\n" + "=" * 60)
    print("LICK SENSITIVITY TEST")
    print("  'w' + Enter  →  trigger manual reward")
    print("  'q' + Enter  →  quit")
    print("  Ctrl+C       →  quit")
    print("=" * 60)

    wd = WaterDispenser()

    print("\n[WD] Verifying calibration and lick threshold...")
    ok = wd.verify_setup()
    if not ok:
        print("[ERROR] Water dispenser verification failed. Check configurations.py and retry.")
        wd.close()
        return
    print("[WD] Verification complete.\n")

    wd.reset_lick_counter()
    wd.get_update_lick()  # flush delta so counter starts clean

    stop = threading.Event()
    lick_total = [0]

    def _input_loop():
        while not stop.is_set():
            try:
                cmd = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                stop.set()
                return
            if cmd == "w":
                wd.pulse()
                print("[REWARD] Manual reward triggered.")
            elif cmd == "q":
                stop.set()

    input_thread = threading.Thread(target=_input_loop, daemon=True)
    input_thread.start()

    print("[INFO] Listening for licks... (lick sensor active)\n")

    try:
        while not stop.is_set():
            delta = wd.get_update_lick()
            if delta > 0:
                lick_total[0] += delta
                print(f"[LICK]  +{delta}  (session total: {lick_total[0]})")
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        wd.close()
        print(f"\n[INFO] Session ended. Total licks detected: {lick_total[0]}")


if __name__ == "__main__":
    main()
