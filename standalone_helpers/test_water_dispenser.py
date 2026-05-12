import time
from water_dispenser import WaterDispenser
from configurations import SET_CALIB_FACTOR, SET_LICK_THRESHOLD

print("\n=== Water Dispenser Standalone Test ===\n")

try:
    wd = WaterDispenser()
except Exception as e:
    print(f"[ERROR] Could not initialize WaterDispenser: {e}")
    exit(1)

def menu():
    print("\nCommands:")
    print("  1: RESET")
    print("  2: HELP")
    print(f"  3: SET_CALIB_FACTOR ({SET_CALIB_FACTOR})")
    print("  4: GET_CALIB_FACTOR")
    print(f"  5: SET_LICK_THRESHOLD ({SET_LICK_THRESHOLD})")
    print("  6: GET_PARAMETERS")
    print("  7: PULSE (dispense)")
    print("  q: Quit")

while True:
    menu()
    cmd = input("Select command: ").strip().lower()
    t0 = time.time()
    if cmd == "1":
        wd.send("RESET")
        print(f"[{time.time()-t0:.3f}s] Sent RESET")
    elif cmd == "2":
        wd.send("HELP")
        print(f"[{time.time()-t0:.3f}s] Sent HELP")
    elif cmd == "3":
        wd.send(f"SET_CALIB_FACTOR:{SET_CALIB_FACTOR}")
        print(f"[{time.time()-t0:.3f}s] Sent SET_CALIB_FACTOR:{SET_CALIB_FACTOR}")
    elif cmd == "4":
        wd.send("GET_CALIB_FACTOR")
        resp = wd._get_response(timeout=1.0)
        print(f"[{time.time()-t0:.3f}s] GET_CALIB_FACTOR -> {resp}")
    elif cmd == "5":
        wd.send(f"SET_LICK_THRESHOLD:{SET_LICK_THRESHOLD}")
        print(f"[{time.time()-t0:.3f}s] Sent SET_LICK_THRESHOLD:{SET_LICK_THRESHOLD}")
    elif cmd == "6":
        wd.send("GET_PARAMETERS")
        resp = wd._get_response(timeout=1.0)
        print(f"[{time.time()-t0:.3f}s] GET_PARAMETERS -> {resp}")
    elif cmd == "7":
        print("Sending PULSE...")
        wd.pulse()
        print(f"[{time.time()-t0:.3f}s] Sent PULSE")
    elif cmd == "q":
        print("Exiting and closing connection...")
        wd.close()
        break
    else:
        print("Unknown command.")

    # Print any extra responses in the queue
    while True:
        resp = wd._get_response(timeout=0.2)
        if resp is None:
            break
        print(f"[WD] {resp}")

print("Done.")
