/********************************************************************************
 * @file Common.h
 * @brief Shared board configuration, GPIO mapping, and common definitions for the e-bike controller.
 * @project E-Bike Controller
 * @author PEF EBIKE OT26
 * @date 2026-08-21
 ********************************************************************************/
#ifndef COMMON_H
#define COMMON_H

// FreeRTOS library used by the RTOS tasks and queues.
#include "freertos/FreeRTOS.h"

#include <Arduino.h>
#include "ESP32Servo.h"
// #include <ESP32Tone.h>
// #include <ESP32PWM.h>
// #include <analogWrite.h>

#ifdef __cplusplus
extern "C"
{
#endif

constexpr int BAUD_RATE = 115200;

/********************* DEFINES *****************************/
#define CW -1 // Clockwise motor rotation convention used by the control logic.
#define CCW 1 // Counter-clockwise motor rotation convention used by the control logic.

/********************* FIREBEETLE PINS **********************/
#define LED_PIN             2
#define PWM_PIN             4
#define POWER_STAGE_PIN     5
#define HALL_SENSOR_U_PIN   21
#define BATTERY_READ_FLAG   25
#define ON_OFF_BUTTON_PIN   GPIO_NUM_26
#define BATTERY_PIN         34
#define THERMISTOR_PIN      35
#define HALL_SENSOR_V_PIN   36
#define HALL_SENSOR_W_PIN   39

/********************* DEBUGGER PINS ************************/
#define JTAG_TDI_PIN        12
#define JTAG_TCK_PIN        13
#define JTAG_TMS_PIN        14
#define JTAG_TDO_PIN        15

#ifdef __cplusplus
}
#endif

#endif // COMMON_H
