#include "BatteryMonitor.h"

// Queue used to publish the last measured battery percentage to other tasks.
QueueHandle_t batteryMonitor_queue;

// Reference to the battery-monitor RTOS task so it can be controlled from other code.
static TaskHandle_t batteryMonitor_handle = NULL;

/* =========== STATIC PROTOTYPES ================= */
static void adcSetUp(void);
static float adcReader(void);
static void TaskBatteryMonitor(void *pvParameters);

static void adcSetUp(void)
{
  // Use the ESP32 ADC in 12-bit mode so the raw reading spans 0 to 4095 counts.
  analogReadResolution(12);

  // Configure the battery-sense pin as an analog input.
  pinMode(BATTERY_PIN, INPUT);

  // MOSFET that closes the voltage divider; keep it open (LOW) while not measuring to avoid draining the battery.
  pinMode(BATTERY_READ_FLAG, OUTPUT);
  digitalWrite(BATTERY_READ_FLAG, LOW);

  // The ADC input is configured for a lower full-scale voltage to keep the battery readings
  // inside the ESP32's most accurate linear region before the signal saturates.
  analogSetPinAttenuation(BATTERY_PIN, ADC_11db);
}

static float adcReader(void)
{
  int raw = analogRead(BATTERY_PIN); // Consider using analogReadMilliVolts() API, it uses a lookup table so the read is linear all the time.

  // As ESP32 ADC is not linear, keep the ADC value at max 2.1V instead of 3.3V to take advantage of the "sweet range"
  float pin_voltage = ((float)raw / 2415.0f) * 2.5f;

  // The voltage divider math is explained in docs/CodeDocumentation.md.
  float battery_voltage = pin_voltage * DIVIDER_FACTOR;

  // Convert the measured battery voltage to a remaining-capacity percentage.
  float percentage = ((battery_voltage - BATTERY_MIN_VOLTS) / (BATTERY_MAX_VOLTS - BATTERY_MIN_VOLTS)) * 100.0f;

  // Clamp the calculated percentage so it stays within a valid 0% to 100% range.
  if (percentage > 100.0f)
  {
    percentage = 100.0f;
  }
  if (percentage < 0.0f)
  {
    percentage = 0.0f;
  }

  return percentage;
}

static void TaskBatteryMonitor(void *pvParameters)
{
  (void)pvParameters;
  float currentBatteryVoltage = 0.0f;

  // Track the last wake time so the task executes at a fixed interval without drift.
  TickType_t xLastWakeTime = xTaskGetTickCount();

  for(;;)
  {
    // Sample the battery level every 20 seconds to keep the telemetry fresh without overloading the ADC.
    vTaskDelayUntil(&xLastWakeTime, pdMS_TO_TICKS(20000));

    // Close the voltage divider only for the duration of the measurement.
    digitalWrite(BATTERY_READ_FLAG, HIGH);
    // Let the MOSFET switch and the divider voltage settle before sampling.
    vTaskDelay(pdMS_TO_TICKS(BATTERY_SETTLE_TIME_MS));
    currentBatteryVoltage = adcReader();
    digitalWrite(BATTERY_READ_FLAG, LOW);

    // Publish the latest battery percentage to any subscriber that needs it.
    if (batteryMonitor_queue != NULL)
    {
      xQueueOverwrite(batteryMonitor_queue, &currentBatteryVoltage);
    }
  }
}

uint8_t initBatteryMonitor()
{
  // Create a one-slot queue that stores the most recent battery value for other tasks.
  batteryMonitor_queue = xQueueCreate(1, sizeof(float));

  // Verify that the queue was successfully allocated before creating the RTOS task.
  if (batteryMonitor_queue == NULL)
  {
    return 0;
  }

  adcSetUp();
  BaseType_t controller = xTaskCreatePinnedToCore(
      TaskBatteryMonitor,     // Function
      "TaskBatteryControl",   // Name of the task
      2048,                   // Stack size
      NULL,                   // Paramater of the task (pvParamaters, NULL if the task does not need a parameter)
      1,                      // Task priority (Range 1 - 32)
      &batteryMonitor_handle, // Handle of the task (we can refer then to the task by calling the Handle)
      1                       // Microcontroller's core where the task is going to be executed
  );

  // Confirm that the battery-monitor task started successfully.
  if (controller == pdPASS)
  {
    return 1;
  }
  else
  {
    return 0;
  }
}

/* TODO:
 * Add a rolling average over 8 to 10 samples to smooth the reported battery level.
 * Add a low-battery flag and send it along with the voltage and percentage in a structured message.
 */