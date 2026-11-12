/********************************************************************************
 * @file PowerManager.cpp
 * @brief Power management and deep sleep supervisor for E-Bike Controller.
 *        Controls the Vedder Switch latching, physical power button events,
 *        motion inactivity timeouts, and thermal shutdown via NTC thermistor.
 * @project E-Bike Controller
 * @author PEF EBIKE OT26
 * @date 2026-11-08
 ********************************************************************************/

#include "PowerManager.h"
#include <math.h>

/* Pin Definitions */
#define THERMISTOR_PIN 35            // ADC1_CH7 input for MOSFET NTC sensor
#define TEMP_THRESHOLD_CELSIUS 60.0f // Max safe operating temperature (°C)

/* NTC Thermistor Steinhart-Hart (Beta) Parameters */
#define SERIES_RESISTOR 100000.0f    // 100k fixed pull-down resistor to GND
#define NOMINAL_RESISTANCE 100000.0f // 100k NTC nominal resistance at 25°C
#define NOMINAL_TEMPERATURE 25.0f    // Nominal reference temperature (°C)
#define B_COEFFICIENT 3950.0f        // Beta coefficient (B25/50)
#define NUM_TEMP_SAMPLES 16          // ADC oversampling count to filter inverter noise

/* Timing Constants */
static const uint32_t DEBOUNCE_MS = 30;                // Software debounce delay
static const uint32_t INACTIVITY_TIMEOUT_MS = 1200000; // 20 minutes without movement to sleep
static const uint32_t PERIODIC_CHECK_MS = 100;         // Periodic polling interval for queues and temp

/* RTOS Handles */
static TaskHandle_t xPowerTaskHandle = NULL;
static TimerHandle_t inactivityTimer = NULL;
static volatile bool inactivityFlag = false;

/* External Queues */
extern QueueHandle_t sendMotionDetect_queue;

/* Static Function Prototypes */
static void setUp(void);
static void waitForReleasedButton(void);
static void inactivityTimerCallback(TimerHandle_t xTimer);
static void TaskPowerManager(void *pvParameters);
static void enterDeepSleep(void);

/**
 * @brief Samples the NTC voltage divider and computes the temperature via Beta equation.
 * @return Temperature in degrees Celsius, or -999.0f if the reading is invalid or unpowered.
 */
float PowerManager_ReadTemperature(void)
{
  uint32_t adcSum = 0;

  // Oversample to reject high-frequency switching noise from the motor phases
  for (int i = 0; i < NUM_TEMP_SAMPLES; i++)
  {
    adcSum += analogRead(THERMISTOR_PIN);
    delayMicroseconds(100);
  }

  float rawValue = (float)adcSum / (float)NUM_TEMP_SAMPLES;

  // Boundary check: unpowered rail (0V) or open circuit (3.3V saturation)
  if (rawValue <= 10.0f || rawValue >= 4085.0f)
  {
    return -999.0f;
  }

  // Voltage divider formula: NTC tied to stage VCC, fixed 100k resistor to GND
  float resistance = SERIES_RESISTOR * ((4095.0f / rawValue) - 1.0f);

  // Steinhart-Hart simplified Beta equation: 1/T = 1/To + (1/B) * ln(R/Ro)
  float steinhart = resistance / NOMINAL_RESISTANCE;   // (R / Ro)
  steinhart = logf(steinhart);                         // ln(R / Ro)
  steinhart /= B_COEFFICIENT;                          // (1 / B) * ln(R / Ro)
  steinhart += 1.0f / (NOMINAL_TEMPERATURE + 273.15f); // + (1 / To)
  steinhart = 1.0f / steinhart;                        // Invert to absolute Kelvin

  return (steinhart - 273.15f); // Convert to Celsius
}

/**
 * @brief Cuts off the power stage gate, locks RTC levels, and halts the core in Deep Sleep.
 *        The board will remain asleep indefinitely until the user presses the button.
 */
static void enterDeepSleep(void)
{
  // 1. Immediately cut off the power stage gate (Vedder switch) and turn off LED
  digitalWrite(POWER_STAGE_PIN, LOW);
  digitalWrite(LED_PIN, LOW);

  // 2. Lock GPIO 25 in solid LOW using RTC domain to prevent gate floating while asleep
  rtc_gpio_init(GPIO_NUM_25);
  rtc_gpio_set_direction(GPIO_NUM_25, RTC_GPIO_MODE_OUTPUT_ONLY);
  rtc_gpio_set_level(GPIO_NUM_25, 0);
  rtc_gpio_hold_en(GPIO_NUM_25);

  // 3. Ensure the push-button is fully released before configuring sleep
  waitForReleasedButton();

  // 4. Configure push-button wake-up source (ext0, active HIGH on 3.3V)
  rtc_gpio_pullup_dis(ON_OFF_BUTTON_PIN);
  rtc_gpio_pulldown_en(ON_OFF_BUTTON_PIN);
  esp_sleep_enable_ext0_wakeup(ON_OFF_BUTTON_PIN, 1);

  // 5. Flush serial logs and halt core
  Serial.println("Entering DEEP SLEEP mode. Halting core.");
  Serial.flush();
  esp_deep_sleep_start();
}

/**
 * @brief Configures GPIO modes, clears RTC pin hold states, and latches the power stage.
 */
static void setUp(void)
{
  // Release RTC latch on power stage pin in case the system just woke up
  rtc_gpio_hold_dis(GPIO_NUM_25);

  pinMode(ON_OFF_BUTTON_PIN, INPUT_PULLDOWN);
  pinMode(POWER_STAGE_PIN, OUTPUT);
  pinMode(LED_PIN, OUTPUT);

  // Configure thermistor pin on ADC1
  pinMode(THERMISTOR_PIN, INPUT);
  analogSetPinAttenuation(THERMISTOR_PIN, ADC_11db);

  // Prevent immediate toggle on the same button press that woke the micro
  waitForReleasedButton();

  // Direct power latch: Energize Vedder switch and status LED
  // Actuator manipulation starts at 0%, so idle quiescent current is safe (<60mA)
  digitalWrite(POWER_STAGE_PIN, HIGH);
  digitalWrite(LED_PIN, HIGH);
}

/**
 * @brief Blocks execution until the physical power button is fully released.
 */
static void waitForReleasedButton(void)
{
  while (digitalRead(ON_OFF_BUTTON_PIN) == HIGH)
  {
    vTaskDelay(pdMS_TO_TICKS(10));
  }
  vTaskDelay(pdMS_TO_TICKS(DEBOUNCE_MS));
}

/**
 * @brief Interrupt Service Routine (ISR) triggered when the power button is pressed.
 */
void IRAM_ATTR buttonISR()
{
  BaseType_t xHigherPriorityTaskWoken = pdFALSE;

  if (xPowerTaskHandle != NULL)
  {
    vTaskNotifyGiveFromISR(xPowerTaskHandle, &xHigherPriorityTaskWoken);
    portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
  }
}

/**
 * @brief FreeRTOS software timer callback executed upon vehicle inactivity timeout.
 */
static void inactivityTimerCallback(TimerHandle_t xTimer)
{
  (void)xTimer;
  inactivityFlag = true;
}

/**
 * @brief Main power management loop: supervises button events, inactivity, and thermals.
 */
static void TaskPowerManager(void *pvParameters)
{
  (void)pvParameters;
  uint8_t movementFlag = 0;

  waitForReleasedButton();

  // Attach rising-edge interrupt for the power button
  attachInterrupt(digitalPinToInterrupt(ON_OFF_BUTTON_PIN), buttonISR, RISING);

  // Start the inactivity countdown timer
  xTimerStart(inactivityTimer, 0);

  for (;;)
  {
    // Wait for button notification or unblock every PERIODIC_CHECK_MS (100 ms)
    uint32_t notified = ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(PERIODIC_CHECK_MS));

    // 1. Check physical power button shutdown request
    if (notified > 0 && digitalRead(ON_OFF_BUTTON_PIN) == HIGH)
    {
      vTaskDelay(pdMS_TO_TICKS(DEBOUNCE_MS));
      if (digitalRead(ON_OFF_BUTTON_PIN) == HIGH)
      {
        enterDeepSleep();
      }
    }

    // 2. Check thermal limit on power MOSFETs
    float mosfetTemp = PowerManager_ReadTemperature();
    if (mosfetTemp >= TEMP_THRESHOLD_CELSIUS)
    {
      // Rapid re-read confirmation filter (guards against EMI / switching spikes)
      vTaskDelay(pdMS_TO_TICKS(20));
      if (PowerManager_ReadTemperature() >= TEMP_THRESHOLD_CELSIUS)
      {
        Serial.printf("[CRITICAL] MOSFET Overheat detected: %.1f C! Cutting power.\n", mosfetTemp);
        enterDeepSleep();
      }
    }

    // 3. Monitor vehicle movement to reset the inactivity timer
    if (sendMotionDetect_queue != NULL)
    {
      xQueuePeek(sendMotionDetect_queue, &movementFlag, 0);
    }

    if (movementFlag == 1)
    {
      xTimerReset(inactivityTimer, 0);
    }

    // 4. Check inactivity timer expiration
    if (inactivityFlag == true)
    {
      Serial.println("Inactivity timeout reached, shutting down system.");
      enterDeepSleep();
    }
  }
}

/**
 * @brief Creates the RTOS task and software timer for power management.
 */
uint8_t initPowerManager(void)
{
  setUp();

  // Create one-shot software timer for inactivity shutdown
  inactivityTimer = xTimerCreate(
      "InactivityTimer",
      pdMS_TO_TICKS(INACTIVITY_TIMEOUT_MS),
      pdFALSE,
      NULL,
      inactivityTimerCallback);

  if (inactivityTimer == NULL)
  {
    return 0;
  }

  // Create PowerManager task pinned to Core 1
  BaseType_t powerManager = xTaskCreatePinnedToCore(
      TaskPowerManager,
      "TaskPowerManager",
      2048,
      NULL,
      2, // Priority 2: Above background tasks for fast emergency shutdown
      &xPowerTaskHandle,
      1);

  return (powerManager == pdPASS) ? 1 : 0;
}