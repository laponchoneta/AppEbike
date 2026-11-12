#include <Arduino.h>
#include "BatteryMonitor.h"
#include "CommsBLE.h"
#include "Control.h"
#include "Common.h"
#include "MotionDetect.h"
#include "PowerManager.h"

void setup()
{
  // Task Initialization
  Serial.begin(BAUD_RATE);
  uint8_t ble_init = initCommsBLE();
  uint8_t battery_monitor_init = initBatteryMonitor();
  uint8_t controller_init = initController();
  uint8_t power_manager_init = initPowerManager();
  uint8_t motion_detect_init = initMotionDetect();

  if (ble_init != 1 || battery_monitor_init != 1 || controller_init != 1 || motion_detect_init != 1 || power_manager_init != 1)
  {
    esp_restart();
  }
  else
  {
    Serial.println("Setup Complete");
  }
}

void loop()
{
  vTaskDelete(NULL);
}