"""
Camera TTL trigger control via Arduino.

Sends commands to the Arduino to start/stop hardware TTL pulses on digital pin,
which trigger external camera recording at the configured frame rate.
"""

from configurations import DEBUG, CAM_DIGITAL_PIN, CAM_SERIAL_TRIGGER_FPS


def start_camera(shared_state):
    """
    Tell the Arduino to begin continuous TTL pulses at the configured camera FPS.
    Sends 'C' over the joystick serial connection.
    """
    ard_ser = shared_state.get("ard_ser", None)
    if ard_ser is None:
        return
    try:
        ard_ser.write(b'C')
        if DEBUG:
            print(f"[CAMERA] Continuous triggering started (pin {CAM_DIGITAL_PIN}, {CAM_SERIAL_TRIGGER_FPS} FPS)")
    except Exception as e:
        if DEBUG:
            print(f"[CAMERA] Start failed: {e}")


def stop_camera(shared_state):
    """
    Tell the Arduino to stop continuous TTL pulses.
    Sends 'c' over the joystick serial connection.
    """
    ard_ser = shared_state.get("ard_ser", None)
    if ard_ser is None:
        return
    try:
        ard_ser.write(b'c')
        if DEBUG:
            print(f"[CAMERA] Continuous triggering stopped (pin {CAM_DIGITAL_PIN})")
    except Exception as e:
        if DEBUG:
            print(f"[CAMERA] Stop failed: {e}")
