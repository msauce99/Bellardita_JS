// UNO R4 Minima 14-bit ADC
// Joystick + camera TTL + nose poke digital input

// ------------------------------------------------------------------------------------
// PIN DEFINITIONS
// ------------------------------------------------------------------------------------
const int joystickX = A0;          // Analog pin A0 for joystick X axis
const int joystickY = A1;          // Analog pin A1 for joystick Y axis
const int camTriggerPin = 8;       // Digital pin 8 for camera TTL trigger
const int nosePokePin = 7;         // Digital pin 7 for nose poke signal
const int ttlInputPin = 2;         // Digital pin 2 for external TTL trial trigger (INPUT_PULLUP, active-low: idles HIGH, trigger on LOW)
// const int pertPin = 4;               // Digital pin 4 for perturbation signal
// ------------------------------------------------------------------------------------

// ------------------------------------------------------------------------------------
// ADC / SAMPLING PARAMETERS
// ------------------------------------------------------------------------------------
const int resolution = 14;
const int center_N = 500;          // baseline samples (median)
const int oversample_N = 8;        // per-sample oversampling (noise reduction)

const uint32_t SAMPLE_RATE_HZ = 1000;                       // 1 kHz
const uint32_t SAMPLE_INTERVAL_US = 1000000UL / SAMPLE_RATE_HZ;
// ------------------------------------------------------------------------------------

// ------------------------------------------------------------------------------------
// CAMERA TRIGGER PARAMETERS
// ------------------------------------------------------------------------------------
const uint32_t CAM_FPS = 100;                               // 100 Hz
const uint32_t CAM_TRIGGER_INTERVAL_US = 1000000UL / CAM_FPS;
bool camRunning = false;
uint32_t lastCamTriggerUs = 0;
// ------------------------------------------------------------------------------------

// ------------------------------------------------------------------------------------
// BASELINE VARIABLES
// ------------------------------------------------------------------------------------
int x_base = 0;
int y_base = 0;

// ------------------------------------------------------------------------------------
// TTL TRIGGER STATE
// ------------------------------------------------------------------------------------
bool lastTtlState = HIGH;  // idle state with INPUT_PULLUP is HIGH
// ------------------------------------------------------------------------------------

// ------------------------------------------------------------------------------------
// HELPERS
// ------------------------------------------------------------------------------------

// Bubble sort (adapted from GeeksforGeeks https://www.geeksforgeeks.org/dsa/bubble-sort-algorithm/), modified for Arduino)
// early exit if already sorted
void bubbleSort(int arr[], int n) {
  bool swapped;

  for (int i = 0; i < n - 1; i++) {
    swapped = false;

    for (int j = 0; j < n - i - 1; j++) {
      if (arr[j] > arr[j + 1]) {
        int temp = arr[j];
        arr[j] = arr[j + 1];
        arr[j + 1] = temp;
        swapped = true;
      }
    }

    if (!swapped) {
      break;
    }
  }
}

// Median calculation using sorted copy
int medianOfArray(const int input[], int n) {
  int temp[500];  // must be >= center_N and oversample_N

  for (int i = 0; i < n; i++) {
    temp[i] = input[i];
  }

  bubbleSort(temp, n);

  if (n % 2 == 0) {
    return (temp[n / 2 - 1] + temp[n / 2]) / 2;
  } else {
    return temp[n / 2];
  }
}

// Median-based analog read with oversampling
int readMedian(int pin) {
  int vals[16];   // must be >= oversample_N

  for (int i = 0; i < oversample_N; i++) {
    vals[i] = analogRead(pin);
  }

  return medianOfArray(vals, oversample_N);
}

// Send current nose poke state as an event string
// NOSE_POKE:1 = beam blocked / nose present
// NOSE_POKE:0 = beam clear / no nose present
void sendNosePokeEvent(bool nosePresent) {
  Serial.print("NOSE_POKE:");
  Serial.println(nosePresent ? 1 : 0);
}
// ------------------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  analogReadResolution(resolution);

  pinMode(camTriggerPin, OUTPUT);
  digitalWrite(camTriggerPin, LOW);

  pinMode(ttlInputPin, INPUT_PULLUP);

  // Nose poke input:
  // Use INPUT (pull-up in circuitry)
  // HIGH  = 1 = beam blocked  = mouse nose present
  // LOW   = 0 = beam clear    = no mouse nose present
  pinMode(nosePokePin, INPUT);

  // perturb input:
  // pinMode(pertPin, OUTPUT)
  // digitalWrite(SOLENOID_PIN, LOW);

  delay(50);

  // Capture joystick baseline while untouched
  static int samples_x[center_N];
  static int samples_y[center_N];

  for (int i = 0; i < center_N; i++) {
    samples_x[i] = analogRead(joystickX);
    samples_y[i] = analogRead(joystickY);
    delay(2);  // ~1 s total for 500 samples
  }

  x_base = medianOfArray(samples_x, center_N);
  y_base = medianOfArray(samples_y, center_N);

  Serial.print("Baseline, X: ");
  Serial.print(x_base);
  Serial.print(" Y: ");
  Serial.println(y_base);
}

void loop() {
  // Detect falling edge on TTL input pin (active-low: idles HIGH, trigger on HIGH->LOW)
  bool ttlState = (digitalRead(ttlInputPin) == HIGH);
  if (!ttlState && lastTtlState) {
    Serial.println("TTL_START");
  }
  lastTtlState = ttlState;

  // Wait for 'S' command to start session/trial
  if (!Serial.available()) {
    return;
  }

  char cmd = Serial.read();

  if (cmd != 'S') {
    return;
  }

  // Clear any remaining input buffer
  while (Serial.available()) {
    Serial.read();
  }

  uint32_t trialStartUs = micros();
  uint32_t lastSampleUs = trialStartUs;
  bool trialRunning = true;

  // Read initial nose poke state at session start
  bool lastNosePresent = (digitalRead(nosePokePin) == HIGH);

  // Send initial state once at the start so Python knows the starting condition
  sendNosePokeEvent(lastNosePresent);

  while (trialRunning) {
    uint32_t now = micros();

    // ------------------------------------------------------------
    // Check serial commands from Python
    // ------------------------------------------------------------
    if (Serial.available()) {
      char serialCmd = Serial.read();

      if (serialCmd == 'E') {
        camRunning = false;
        digitalWrite(camTriggerPin, LOW);
        trialRunning = false;
        break;
      }

      if (serialCmd == 'C') {
        camRunning = true;
        lastCamTriggerUs = now;
      }

      if (serialCmd == 'c') {
        camRunning = false;
        digitalWrite(camTriggerPin, LOW);
      }

      // Ignore any other characters
    }

    // ------------------------------------------------------------
    // Camera TTL triggering
    // ------------------------------------------------------------
    if (camRunning && (now - lastCamTriggerUs >= CAM_TRIGGER_INTERVAL_US)) {
      lastCamTriggerUs += CAM_TRIGGER_INTERVAL_US;
      digitalWrite(camTriggerPin, HIGH);
      delayMicroseconds(50);
      digitalWrite(camTriggerPin, LOW);
    }

    // ------------------------------------------------------------
    // Nose poke state-change detection
    // Send event string only when state changes
    // ------------------------------------------------------------
    bool nosePresent = (digitalRead(nosePokePin) == HIGH);

    if (nosePresent != lastNosePresent) {
      sendNosePokeEvent(nosePresent);
      lastNosePresent = nosePresent;
    }

    // ------------------------------------------------------------
    // Fixed-rate joystick sampling
    // ------------------------------------------------------------
    if (now - lastSampleUs >= SAMPLE_INTERVAL_US) {
      lastSampleUs += SAMPLE_INTERVAL_US;

      int X = readMedian(joystickX) - x_base;
      int Y = readMedian(joystickY) - y_base;

      Serial.print(X);
      Serial.print(",");
      Serial.println(Y);
    }
  }
}