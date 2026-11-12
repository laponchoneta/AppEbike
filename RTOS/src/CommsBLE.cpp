#include "CommsBLE.h"
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <ArduinoJson.h>

// UUIDs for the service and the BLE characteristicE, standard Bluetooth-SIG defined UUID (16-bits) or a vendor specific UUID (128-bits)
#define SERVICE_UUID "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define CHARACTERISTIC_UUID "00002A3D-0000-1000-8000-00805f9b34fb"

// Telemetry notification period sent to the app.
#define TELEMETRY_PERIOD_MS 50

// Requested BLE connection parameters. Phones usually default to a 30-50 ms connection interval, which delays
// every app command; 15 ms (12 x 1.25 ms) is the shortest interval accepted by both Android and iOS.
#define BLE_CONN_INTERVAL_MIN 12  // 15 ms, in 1.25 ms units
#define BLE_CONN_INTERVAL_MAX 12  // 15 ms, in 1.25 ms units
#define BLE_CONN_LATENCY      0   // Peripheral latency (connection events the ESP32 may skip)
#define BLE_CONN_TIMEOUT      400 // 4 s supervision timeout, in 10 ms units

static BLEServer *pServer = NULL;
static BLECharacteristic *pCharacteristic = NULL;
static bool deviceConnected = false;
static bool oldDeviceConnected = false;

// JSON document used to package telemetry for the Android app.
static DynamicJsonDocument jsonDoc(512);
static DynamicJsonDocument jsonreceived(256);
static BleCommands_t sendBleCommands = {0};
static uint8_t motionDetectToggle = 0; // App switch that enables or disables motion-based assistance.

// RTOS task handle for the Bluetooth communication task.
static TaskHandle_t xCommsBleHandle = NULL;

// Output Queues
QueueHandle_t motionDetectToggle_queue;
QueueHandle_t controlParameters_queue;

// Static prototypes.
static void TaskCommsBLE(void *pvParameters);
static void setUpBLE(void);

// BLE server callbacks handle connect and disconnect events.
class MyServerCallbacks : public BLEServerCallbacks
{
  void onConnect(BLEServer *pServer) override
  {
    deviceConnected = true;
  };

  void onConnect(BLEServer *pServer, esp_ble_gatts_cb_param_t *param) override
  {
    // Ask the phone for a short connection interval so commands and telemetry are exchanged faster.
    pServer->updateConnParams(param->connect.remote_bda, BLE_CONN_INTERVAL_MIN, BLE_CONN_INTERVAL_MAX, BLE_CONN_LATENCY, BLE_CONN_TIMEOUT);
  }

  void onDisconnect(BLEServer *pServer) override
  {
    deviceConnected = false;
  }
};

class MyCallbacks : public BLECharacteristicCallbacks
{
  void onWrite(BLECharacteristic *pCharacteristic) override
  {
    // The app sends a JSON payload with motor settings, speed reference, and motion settings.
    std::string receivedValue = pCharacteristic->getValue();
    deserializeJson(jsonreceived, receivedValue.c_str());

    // Each field is decoded from the JSON payload sent by the mobile application.
    String sliderPercentageString = jsonreceived["slider_per"]; // Open-loop manual drive percentage selected by the rider.
    String sliderKmString = jsonreceived["slider_km"];          // Closed-loop speed target selected in the app.
    String velocityValid = jsonreceived["speed"];               // Current bike speed reported by the phone GPS.
    String motionDetectString = jsonreceived["adapt"];          // Toggle that enables or disables motion detection.
    String accelerationString = jsonreceived["acceleration_y"]; // Vehicle acceleration data used by the control logic.

    // Manual control: the rider can set a direct motor manipulation percentage.
    if (sliderPercentageString != "null")
    {
      sendBleCommands.automaticFlag = 0;
      sendBleCommands.sliderValue = (uint8_t)sliderPercentageString.toInt();
    }

    // Automatic control: the rider sets a target speed in km/h.
    if (sliderKmString != "null")
    {
      sendBleCommands.sliderReferenceKm = sliderKmString.toDouble();
      sendBleCommands.automaticFlag = 1;
    }

    // Only update the measured speed when close-loop mode is active.
    if (velocityValid != "null")
    {
      if (sendBleCommands.automaticFlag == 1)
      {
        sendBleCommands.velocityPerKm = velocityValid.toInt();
        sendBleCommands.pidFlag = 1;
      }
    }

    // Store the most recent motion-detection configuration selected by the user.
    if (motionDetectString != "null")
    {
      motionDetectToggle = motionDetectString.toInt();
      sendBleCommands.motionDetectEnabled = motionDetectToggle;
    }

    sendBleCommands.acceleration = accelerationString.toFloat();

    // Publish the latest app settings to the tasks that need them.
    if (motionDetectToggle_queue != NULL)
    {
      xQueueOverwrite(motionDetectToggle_queue, &motionDetectToggle);
    }
    if (controlParameters_queue != NULL)
    {
      xQueueOverwrite(controlParameters_queue, &sendBleCommands);
    }
  }
};

// RTOS task that exchanges telemetry with the connected smartphone app.
static void TaskCommsBLE(void *pvParameters)
{
  (void)pvParameters;

  float Mk = 0.0f;
  float lecture = 0.0f;
  uint16_t rpm = 0;
  uint8_t moveState = 0;

  // Use a fixed task period so the app receives updates at a predictable rate.
  TickType_t xLastWakeTime = xTaskGetTickCount();

  for (;;)
  {
    /* Set the task exact cycle in miliseconds (enforces precise periodic execution) */
    vTaskDelayUntil(&xLastWakeTime, pdMS_TO_TICKS(TELEMETRY_PERIOD_MS));

    // Restart advertising after a disconnect so the phone can reconnect automatically.
    if (!deviceConnected && oldDeviceConnected)
    {
      vTaskDelay(pdMS_TO_TICKS(500));
      pServer->startAdvertising();
      oldDeviceConnected = deviceConnected;
    }
    if (deviceConnected && !oldDeviceConnected)
    {
      oldDeviceConnected = deviceConnected;
    }

    // Publish telemetry only while a client is actively connected.
    if (deviceConnected && pCharacteristic != NULL)
    {
      // Receive the latest battery percentage reported by the battery-monitor task.
      if (batteryMonitor_queue != NULL)
      {
        xQueueReceive(batteryMonitor_queue, &lecture, 0);
      }
      else
      {
        /* Nothing happens, lecture already set as 0 */
      }
      
      /* Receive the information sent by Control Task */
      if (sendMk_queue != NULL)
      {
        xQueueReceive(sendMk_queue, &Mk, 0);
      }
      else
      {
        /* Nothing happens, Mk already set as 0 */
      }
      /* Receive the information sent by Motion Detect Task */
      if (sendRPM_queue != NULL)
      {
        xQueueReceive(sendRPM_queue, &rpm, 0);
      }
      else
      {
        /* Nothing happens, rpm already set as 0 */
      }
      /* Receive the information sent by Motion Detect Task */
      if (sendMotionDetect_queue != NULL)
      {
        xQueueReceive(sendMotionDetect_queue, &moveState, 0);
      }
      else
      {
        /* Nothing happens, moveState already set as 0 */
      }

      // Build the app payload with battery level and current motor manipulation.
      jsonDoc.clear();
      jsonDoc["battery"] = String(lecture);
      jsonDoc["manipulation"] = (int)Mk;
      jsonDoc["rpm"] = (int)rpm;
      jsonDoc["moving"] = (int)moveState;

      // Serialize the JSON payload so it can be sent as a BLE characteristic value.
      String jsonString;
      serializeJson(jsonDoc, jsonString);

      // Send the latest telemetry to the connected smart device.
      pCharacteristic->setValue(jsonString.c_str());
      pCharacteristic->notify();
    }
  }
}

static void setUpBLE(void)
{
  // Initialize the local BLE device that exposes the app service.
  BLEDevice::init("ESP32");

  /* Create and initializate BLE server */
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());

  // Create the BLE service and characteristic that the phone app will use.
  BLEService *pService = pServer->createService(SERVICE_UUID);

  /* Create BLE characteristic */
  pCharacteristic = pService->createCharacteristic(
      CHARACTERISTIC_UUID,
      BLECharacteristic::PROPERTY_READ |
      BLECharacteristic::PROPERTY_WRITE |
      BLECharacteristic::PROPERTY_NOTIFY |
      BLECharacteristic::PROPERTY_INDICATE);

  // Add the descriptor needed for notifications and subscribe support.
  pCharacteristic->addDescriptor(new BLE2902());

  /* Assign a write callback */
  pCharacteristic->setCallbacks(new MyCallbacks());

  /* Start the server */
  pService->start();

  /* Start publicy process */
  // :: is the scope resolution operator - typically used between a class name and a class method.
  BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(false);
  pAdvertising->setMinPreferred(0x0);
  BLEDevice::startAdvertising();
}

// Initialize the BLE communication task and its output queues.
uint8_t initCommsBLE(void)
{
  /* Create output queues */
  motionDetectToggle_queue = xQueueCreate(1, sizeof(uint8_t));
  controlParameters_queue = xQueueCreate(1, sizeof(BleCommands_t));

  if (motionDetectToggle_queue == NULL || controlParameters_queue == NULL)
  {
    return 0;
  }

  setUpBLE();

  BaseType_t commsBLE = xTaskCreatePinnedToCore(
      TaskCommsBLE,     // Function
      "TaskCommsBLE",   // Name of the task
      4096,             // Stack size
      NULL,             // Paramater of the task (pvParamaters, NULL if the task does not need a parameter)
      5,                // Task priority (Range 1 - 32)
      &xCommsBleHandle, // Handle of the task (we can refer then to the task by calling the Handle)
      1                 // Microcontroller's core where the task is going to be executed
  );

  // Confirm that the BLE communication task was created successfully.
  if (commsBLE == pdPASS)
  {
    return 1;
  }
  else
  {
    return 0;
  }
}