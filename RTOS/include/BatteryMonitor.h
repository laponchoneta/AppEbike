/********************************************************************************
 * @file BatteryMonitor.h
 * @brief Public interface for the battery monitoring subsystem.
 * @project E-Bike Controller
 * @author PEF EBIKE OT26
 * @date 2026-08-21
 ********************************************************************************/
#ifndef BATTERY_MONITOR
#define BATTERY_MONITOR

#include "Common.h"

#ifdef __cplusplus
extern "C"
{
#endif

#define BATTERY_PIN 34

// Time (ms) the divider MOSFET (BATTERY_READ_FLAG) stays closed before sampling the ADC.
#define BATTERY_SETTLE_TIME_MS 10

// The voltage divider reduces the battery voltage to a safe ADC range for the ESP32.
// With R1 = 6.8 kΩ and R2 = 680 Ω, the divider ratio is approximately 11:1.
#define DIVIDER_FACTOR 10.0f

// Battery operating range used to estimate the remaining charge percentage.
// The pack is treated as empty at 15.7 V and full near 24.0 V.
#define BATTERY_MIN_VOLTS 24.0f
#define BATTERY_MAX_VOLTS 25.2f

/**
 * @brief Starts the battery-monitor task and initializes its ADC and queue resources.
 * @return 1 on success, 0 if the task or queue could not be created.
 */
uint8_t initBatteryMonitor(void);

#ifdef __cplusplus
}
#endif

#endif // BATTERY_MONITOR