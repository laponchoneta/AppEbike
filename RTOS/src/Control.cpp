#include "Control.h"
#include "CommsBLE.h"

static Servo myServo;

/* Motion Detect State Defines */
#define MANUAL_MODE                     0   // Manual mode selected by user
#define AUTOMATIC_MODE                  1   // Automatic mode selected by user
#define MOTION_DETECT_DISABLED          2   // Switch is OFF, detection not considered
#define MOTION_DETECT_ACTIVE_MOVEMENT   3   // Switch ON and movement detected then motor allowed
#define MOTION_DETECT_ENABLED_NO_MOVE   4   // Switch ON but no movement detected then motor blocked

/* TIMING */
#define CONTROL_PERIOD_MS               20    // Matches the 50 Hz ESC frame, so a new app command reaches the motor within one PWM frame.
#define PID_SAMPLE_TIME_MS              1000  // Closed-loop speed update period (the phone GPS speed arrives at ~1 Hz).
#define PID_CYCLES                      (PID_SAMPLE_TIME_MS / CONTROL_PERIOD_MS)

/* RAMP UP ELEMENTS */
#define TOTAL_RAMP_UP_STEPS             10
#define RAMP_STEP_TIME_MS               40    // Time each gain step is held: 0% to 100% takes ~0.4 s.
#define RAMP_STEP_CYCLES                (RAMP_STEP_TIME_MS / CONTROL_PERIOD_MS)
#define RAMP_LAST_CYCLE                 ((TOTAL_RAMP_UP_STEPS - 1) * RAMP_STEP_CYCLES)

static_assert(RAMP_STEP_CYCLES >= 1, "RAMP_STEP_TIME_MS must be >= CONTROL_PERIOD_MS");
static_assert((PID_CYCLES >= 1) && (PID_CYCLES <= 255), "PID_CYCLES must fit in the uint8_t cycle counter");

// Exponential gain profile for a smooth 0% to 100% ramp-up over several control cycles.
static const float manipulationGainProfile[TOTAL_RAMP_UP_STEPS] = {0.15f, 0.19f, 0.24f, 0.30f, 0.38f, 0.48f, 0.60f, 0.76f, 0.95f, 1.00f};
static float progManipulation;
static uint8_t step = 0; // Control cycles elapsed since the ramp started (saturates at RAMP_LAST_CYCLE).

/* Input queues */
extern QueueHandle_t controlParameters_queue;
extern QueueHandle_t sendMotionDetect_queue;

/* Output queues */
QueueHandle_t sendMk_queue;

// RTOS task handle for the control loop.
static TaskHandle_t control_handle = NULL;

// Static prototypes.
static void setPWM(float manipulation);
static void stopMotor(void);
static uint8_t isMotionDetectActive(uint8_t motionDetectAppToggle, uint8_t movementFlagEnable);
static void TaskControl(void *pvParameters);
static float sendProgressiveManipulation(float targetManipulation);

// Convert the computed duty percentage into the servo pulse width required by the motor controller.
static void setPWM(float manipulation)
{
  if (manipulation > 100.0f)
  {
    manipulation = 100.0f;
  }
  else if (manipulation < 00.f)
  {
    manipulation = 0.0f;
  }
  else
  {
    /* No action is needed, keep manipulation value as it is */
  }

  // The ESC expects a pulse width in microseconds, typically between 1000 and 2000 us.
  uint16_t pulse = (uint16_t)(1000.0f + (manipulation * 10.0f) + 0.5f);
  myServo.writeMicroseconds(pulse);
}

// Apply a gradual ramp so the rider does not feel a sudden torque jump when the app requests a larger value.
static float sendProgressiveManipulation(float targetManipulation)
{
  if (targetManipulation <= 0)
  {
    step = 0;
    progManipulation = 0;
    return 0;
  }

  progManipulation = targetManipulation * manipulationGainProfile[step / RAMP_STEP_CYCLES];

  if (step < RAMP_LAST_CYCLE)
  {
    step++;
  }
  else
  {
    /* Nothing happens, step has arrived to its upper limit */
  }

  return progManipulation;
}

// Stop the motor by forcing the minimum valid servo pulse width.
static void stopMotor()
{
  myServo.writeMicroseconds(1000);
}

/* Determines motion detection state: returns DISABLED if the switch is off,
 * ACTIVE_MOVEMENT if the switch is on and movement is detected, or
 * ENABLED_NO_MOVE if the switch is on but no movement is detected. */
static uint8_t isMotionDetectActive(uint8_t motionDetectAppToggle, uint8_t movementFlagEnable)
{
  uint8_t rVal = 0;

  /* Motion Detect Switch was activated in App */
  if (motionDetectAppToggle == 1)
  {
    /* Motion Detect Task detected movement */
    if (movementFlagEnable == 1)
    {
      rVal = (uint8_t)MOTION_DETECT_ACTIVE_MOVEMENT;
    }
    else
    {
      rVal = (uint8_t)MOTION_DETECT_ENABLED_NO_MOVE;
    }
  }
  else
  {
    rVal = (uint8_t)MOTION_DETECT_DISABLED;
  }
  return rVal;
}


/* Read commands, calculate motor output, and publish control data periodically */
static void TaskControl(void *pvParameters)
{
  BleCommands_t receiveBleCommands = {0};
  uint8_t movementFlag = 0;
  uint8_t cycleCounter = 0; // The close-loop controller updates every PID_CYCLES control cycles (PID_SAMPLE_TIME_MS).
  uint8_t motionDetectState = 0;
  uint8_t controlMode = 0;

  // PID state variables used for the closed-loop speed controller.
  float Mk = 0;      // Current motor output command.
  float Mk1 = 0;     // Previous motor output command.
  float E = 0;       // Current speed error.
  float E1 = 0;      // Previous speed error.
  float E2 = 0;      // Speed error from two cycles earlier.
  float Kc = 3;      // Proportional gain.
  float Taui = 20;    // Integral time constant.
  float Taud = 0.05;  // Derivative time constant.
  float T = PID_SAMPLE_TIME_MS / 1000.0f; // PID sampling period in seconds.

  // Discrete PID coefficients.
  float BC1 = Kc * (1 + (T / Taui) + (Taud / T));
  float BC2 = Kc * (-1 - (2 * Taud / T));
  float BC3 = Kc * Taud / T;

  // Save the initial tick count so the task can run on a deterministic schedule.
  TickType_t xLastWakeTime = xTaskGetTickCount();

  for (;;)
  {
    // Execute the controller every CONTROL_PERIOD_MS so app commands are applied with minimal latency.
    vTaskDelayUntil(&xLastWakeTime, pdMS_TO_TICKS(CONTROL_PERIOD_MS));

    // Receive the most recent user settings from the BLE task if a new update exists.
    if (controlParameters_queue != NULL)
    {
      if (xQueueReceive(controlParameters_queue, &receiveBleCommands, 0) == pdPASS)
      {
        /* A new command was received */
      }
      else
      {
        /* No new command; continue using the previous values */
      }
    }
    
    /* Read the latest motion detection state */
    if (sendMotionDetect_queue != NULL)
    {
      if (xQueueReceive(sendMotionDetect_queue, &movementFlag, 0) == pdPASS)
      {
        /* A new movement state was received */
      }
      else
      {
        /* No new state; continue using the previous value */
      }
    }

    motionDetectState = isMotionDetectActive(receiveBleCommands.motionDetectEnabled, movementFlag);
    controlMode = receiveBleCommands.automaticFlag;

    switch (controlMode)
    {
    case MANUAL_MODE:
      // Manual mode uses the rider-selected slider value directly.
      if ((motionDetectState == (uint8_t)MOTION_DETECT_DISABLED) || (motionDetectState == (uint8_t)MOTION_DETECT_ACTIVE_MOVEMENT))
      {
        Mk = sendProgressiveManipulation((float)receiveBleCommands.sliderValue);
        Mk1 = Mk;
        E2 = 0;
        E1 = 0;
        E = 0;
        cycleCounter = 0;
      }
      else if (motionDetectState == (uint8_t)MOTION_DETECT_ENABLED_NO_MOVE)
      {
        Mk = 0.0f;
        Mk1 = 0.0f;
        E = 0.0f;
        E1 = 0.0f;
        E2 = 0.0f;
        cycleCounter = 0;
        step = 0;
        progManipulation = 0;
      }
      break;

    case AUTOMATIC_MODE:
      cycleCounter++;
      step = 0; // Reset the ramp so the next manual command starts from a clean state.
      progManipulation = 0;

      if ((motionDetectState == (uint8_t)MOTION_DETECT_DISABLED) || (motionDetectState == (uint8_t)MOTION_DETECT_ACTIVE_MOVEMENT))
      {
        if (receiveBleCommands.velocityPerKm >= 0 && receiveBleCommands.pidFlag == 1)
        {
          if (cycleCounter >= PID_CYCLES)
          {
            // Update the speed controller once per second using the latest GPS-derived speed and setpoint.
            cycleCounter = 0;

            E = receiveBleCommands.sliderReferenceKm - receiveBleCommands.velocityPerKm;
            Mk = Mk1 + (BC1 * E) + (BC2 * E1) + (BC3 * E2);

            /* Avoid Mk1 saves values bigger than 100 */
            if (Mk > 100.0f)
            {
              Mk = 100.0f;
            }
            else if (Mk < 0.0f)
            {
              Mk = 0.0f;
            }
            Mk1 = Mk;
            E2 = E1;
            E1 = E;
          }
        }
        else
        {
          Mk = 0;
          Mk1 = Mk;
          E2 = E1;
          E1 = E;
        }
      }
      else if (motionDetectState == (uint8_t)MOTION_DETECT_ENABLED_NO_MOVE)
      {
        Mk = 0.0f;
        Mk1 = 0.0f;
        E = 0.0f;
        E1 = 0.0f;
        E2 = 0.0f;
        cycleCounter = 0;
      }
      break;

    default:
      Mk = 0.0f;
      Mk1 = 0.0f;
      E = E1 = E2 = 0.0f;
      cycleCounter = 0;
      step = 0;
      progManipulation = 0.0f;
      break;
    }

    // Write the final duty-cycle command to the motor output.
    setPWM(Mk);

    // Publish the latest computed motor command so the app can display it.
    if (sendMk_queue != NULL)
    {
      xQueueOverwrite(sendMk_queue, &Mk);
    }
  }
}

uint8_t initController()
{
  // Attach the servo to the configured PWM pin and force the output to neutral before control starts.
  myServo.attach(PWM_PIN, 1000, 2000);
  stopMotor();

  sendMk_queue = xQueueCreate(1, sizeof(float));

  BaseType_t controller = xTaskCreatePinnedToCore(
      TaskControl,     // Task function
      "TaskControl",   // Task name
      4096,            // Stack size
      NULL,            // Task parameter
      3,               // Task priority
      &control_handle, // Task handle
      1                // Core where the task runs
  );

  // Confirm that the controller task was created successfully.
  if (controller == pdPASS)
  {
    return 1;
  }
  else
  {
    return 0;
  }
}
