/********************************************************************************
 * @file CommaBLE.h
 * @brief
 * @project E-Bike Controller
 * @author PEF EBIKE OT26
 * @date 2026-08-21
 ********************************************************************************/

#ifndef COMMS_BLE
#define COMMS_BLE

#include "Common.h"

#ifdef __cplusplus
extern "C"
{
#endif

  // Input queue
  extern QueueHandle_t sendMk_queue;
  extern QueueHandle_t batteryMonitor_queue;
  extern QueueHandle_t sendMotionDetect_queue;
  extern QueueHandle_t sendRPM_queue;

  typedef struct
  {
    float acceleration;
    double sliderReferenceKm;
    int velocityPerKm;
    uint8_t sliderValue;
    uint8_t pidFlag;
    uint8_t automaticFlag;
    uint8_t motionDetectEnabled;
  } BleCommands_t;

  // Output Queues
  extern QueueHandle_t motionDetectToggle_queue;
  extern QueueHandle_t controlParameters_queue;

  /**
    * @brief Function for RTOS task set up and creation
    * @param[out] void
    */
  uint8_t initCommsBLE(void);

#ifdef __cplusplus
}
#endif

#endif // COMMS_BLE
