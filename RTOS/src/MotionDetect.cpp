#include "MotionDetect.h"

/* Motion detection state */
static uint8_t movementFlag = 0;
static uint8_t directionFlagMotor = 1;
static uint8_t directionMotor = 1;
static uint16_t rpm = 0;

// Schmitt-trigger thresholds and signal smoothing parameters for stable motion detection.
static const uint8_t rpmThresholdOn = 180;  // Activate motion when the filtered RPM rises above this value.
static const uint8_t rpmThresholdOff = 120; // Deactivate motion once the filtered RPM falls below this level.
static float filteredRpm = 0.0f;        // Smoothed RPM value after applying the IIR filter.
static const float betaRpm = 0.25f;     // IIR smoothing factor between 0.0 and 1.0.

static float ppm = 0.0f;
static uint32_t startTime = 0;
static uint32_t prevTime = 0;
static uint32_t pulseTimeW = 0;
static uint32_t pulseTimeU = 0;
static uint32_t pulseTimeV = 0;
static uint32_t AvPulseTime = 0;
static uint32_t printTime = 0;

static bool HSU_Val = false;
static bool HSV_Val = false;
static bool HSW_Val = false;
static bool HSU_Val_old = false;
static bool HSV_Val_old = false;
static bool HSW_Val_old = false;

// Static prototypes.
static void hallSetUp(void);
static void HallSensorW(void);
static void HallSensorV(void);
static void HallSensorU(void);
static void TaskToMotionDetect(void *pvParameters);

/* Input queue */
extern QueueHandle_t motionDetectToggle_queue;

/* Output queue */
QueueHandle_t sendMotionDetect_queue;
QueueHandle_t sendRPM_queue;

static void hallSetUp(void)
{
  // The Hall sensor pins are input-only; they require external pull-ups to 3.3 V.
  pinMode(HALL_SENSOR_U_PIN, INPUT);
  pinMode(HALL_SENSOR_V_PIN, INPUT);
  pinMode(HALL_SENSOR_W_PIN, INPUT);
}

// Initialize the motion-detection task and the queue used to report movement status.
uint8_t initMotionDetect()
{
  sendMotionDetect_queue = xQueueCreate(1, sizeof(uint8_t));
  sendRPM_queue = xQueueCreate(1, sizeof(uint16_t));
  
  hallSetUp();
  BaseType_t isMotionDetect = xTaskCreatePinnedToCore(
      TaskToMotionDetect, // Task function
      "TaskMotionDetect", // Task name
      2048,              // Stack size
      NULL,               // Task parameter
      4,                  // Task priority
      NULL,               // Task handle
      1                   // Core where the task runs
  );

  // Confirm that the task was created successfully.
  if (isMotionDetect == pdPASS)
  {
    return 1;
  }
  else
  {
    return 0;
  }
}

// Motion-detection task: read Hall sensor transitions, estimate motor RPM, and publish movement state.
static void TaskToMotionDetect(void *pvParameters)
{
  (void)pvParameters;

  // Capture the initial sensor states to detect the first transition correctly.
  HSU_Val = digitalRead(HALL_SENSOR_U_PIN);
  HSV_Val = digitalRead(HALL_SENSOR_V_PIN);
  HSW_Val = digitalRead(HALL_SENSOR_W_PIN);

  for (;;)
  {
    // If no Hall pulse is detected for 100 ms, assume the motor has stopped and reset the speed estimate.
    if ((micros() - prevTime) > 100000)
    {
      rpm = 0;
    };

    /* Apply a first-order IIR filter to the instantaneous speed */
    filteredRpm = filteredRpm + betaRpm * ((float)rpm - filteredRpm);

    if (sendRPM_queue != NULL)
    {
      uint16_t rpmFilteredToSend = (uint16_t)(filteredRpm + 0.5f);
      xQueueOverwrite(sendRPM_queue, &rpmFilteredToSend);
    }

    // Read the current sensor state and compare it to the previous reading to detect a transition.
    HSU_Val_old = HSU_Val;
    HSV_Val_old = HSV_Val;
    HSW_Val_old = HSW_Val;
    HSU_Val = digitalRead(HALL_SENSOR_U_PIN);
    HSV_Val = digitalRead(HALL_SENSOR_V_PIN);
    HSW_Val = digitalRead(HALL_SENSOR_W_PIN);

    // Invoke the appropriate Hall-sensor handler when a phase change is detected.
    if (HSW_Val_old != HSW_Val)
    {
      HallSensorW();
    }
    else if (HSV_Val_old != HSV_Val)
    {
      HallSensorV();
    }
    else if (HSU_Val_old != HSU_Val)
    {
      HallSensorU();
    }

    /* Apply double-threshold (Schmitt trigger) motion detection */
    if ((filteredRpm >= rpmThresholdOn) && (directionMotor == 1) && (directionFlagMotor == 1))
    {
      movementFlag = 1;
      directionFlagMotor = 0;
      if (sendMotionDetect_queue != NULL)
      {
        xQueueOverwrite(sendMotionDetect_queue, &movementFlag);
      }
    }
    else if ((filteredRpm >= rpmThresholdOn) && (directionFlagMotor == 0))
    {
      movementFlag = 1;
      if (sendMotionDetect_queue != NULL)
      {
        xQueueOverwrite(sendMotionDetect_queue, &movementFlag);
      }
    }
    else if ((filteredRpm <= rpmThresholdOff) || (directionMotor != 1))
    {
      /* Deactivate only below the lower RPM threshold */
      movementFlag = 0;
      directionFlagMotor = 1;
      if (sendMotionDetect_queue != NULL)
      {
        xQueueOverwrite(sendMotionDetect_queue, &movementFlag);
      }
    }

    vTaskDelay(pdMS_TO_TICKS(1)); // Prevent the ESP32 watchdog from resetting
  }
}

/* Calculate RPM and direction from the Hall sensor transitions */
static void HallSensorW(void)
{
  startTime = micros(); // Measure the time between state transitions
  directionMotor = ((!HSW_Val) == HSV_Val) ? CW : CCW; // Compare W inverted without changing its value
  pulseTimeW = startTime - prevTime;
  AvPulseTime = ((pulseTimeW + pulseTimeU + pulseTimeV) / 3);

  if (AvPulseTime > 0)
  {
    ppm = (1000000.0f / AvPulseTime) * 60.0f;
    rpm = (int)(ppm / 90.0f); // The wheel produces 90 pulses per revolution.
  }
  prevTime = startTime;
}

static void HallSensorV(void)
{
  startTime = micros();
  directionMotor = (HSV_Val == HSU_Val) ? CW : CCW;
  pulseTimeV = startTime - prevTime;
  AvPulseTime = ((pulseTimeW + pulseTimeU + pulseTimeV) / 3);

  if (AvPulseTime > 0)
  {
    ppm = (1000000.0f / AvPulseTime) * 60.0f;
    rpm = (int)(ppm / 90.0f);
  }
  prevTime = startTime;
}

static void HallSensorU(void)
{
  startTime = micros();
  directionMotor = (HSU_Val == (!HSW_Val)) ? CW : CCW;
  pulseTimeU = startTime - prevTime;
  AvPulseTime = ((pulseTimeW + pulseTimeU + pulseTimeV) / 3);

  if (AvPulseTime > 0)
  {
    ppm = (1000000.0f / AvPulseTime) * 60.0f;
    rpm = (int)(ppm / 90.0f);
  }
  prevTime = startTime;
}