/********************************************************************************
 * @file PowerManager.h
 * @brief 
 * @project E-Bike Controller
 * @author PEF EBIKE OT26
 * @date 2026-08-21
 ********************************************************************************/

#ifndef POWER_MANAGER
#define POWER_MANAGER

#include "Common.h"
#include "esp_sleep.h"
#include "driver/rtc_io.h" // ESPRESSIF LIBRARY TO CONTROL DEEP SLEEP
#include "freertos/timers.h" // FREERTOS LIBRARY FOR SOFTWARE TIMERS

#ifdef __cplusplus
extern "C"
{
#endif

  /**
   * @brief Function for RTOS task set up and creation
   * @param[out] void
   */
  uint8_t initPowerManager(void);

#ifdef __cplusplus
}
#endif

#endif // POWER_MANAGER