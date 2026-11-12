/********************************************************************************
 * @file Control.h
 * @brief
 * @project E-Bike Controller
 * @author PEF EBIKE OT26
 * @date 2026-08-21
 ********************************************************************************/

#ifndef CONTROL
#define CONTROL

#include "Common.h"

#ifdef __cplusplus
extern "C"
{
#endif

  /**
   * @brief Function for RTOS task set up and creation
   * @param[out] void
   */
  uint8_t initController();

#ifdef __cplusplus
}
#endif

#endif // CONTROL