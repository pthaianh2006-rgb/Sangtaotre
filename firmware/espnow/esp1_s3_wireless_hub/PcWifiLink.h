#pragma once

// ESP1 -> computer transport. ESP-NOW stays on the station interface while
// this server uses a SoftAP on the same 2.4 GHz channel.
#include <Arduino.h>
#include <WiFi.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

namespace PcWifiLink {
constexpr const char* SSID = "ACL_Knee";
constexpr const char* PASSWORD = "12345678";
constexpr uint16_t PORT = 8080;
constexpr size_t MESSAGE_BYTES = 192;

struct Message { char text[MESSAGE_BYTES]; };

inline WiFiServer server(PORT);
inline QueueHandle_t txQueue = nullptr;
inline QueueHandle_t commandQueue = nullptr;
inline uint8_t radioChannel = 1;
inline volatile bool ready = false;
inline volatile uint32_t droppedMessages = 0;

inline bool validCommand(char command) {
  return command == 'C' || command == 'F' || command == 'L' || command == 'R';
}

inline void networkTask(void*) {
  WiFiClient client;
  Message message;
  for (;;) {
    NetworkClient incoming = server.accept();
    if (incoming) {
      if (client) client.stop();
      client = incoming;
      client.setNoDelay(true);
      client.setTimeout(50);
      Serial.printf("[WIFI] PC connected: %s\n", client.remoteIP().toString().c_str());
      client.print("[WIFI] Connected to ESP1 wireless knee hub.\n");
    }

    if (client && !client.connected()) {
      client.stop();
      Serial.println("[WIFI] PC disconnected; waiting for reconnect.");
    }

    if (client && client.connected()) {
      while (client.available()) {
        int value = client.read();
        if (value < 0) break;
        char command = static_cast<char>(value);
        if (command >= 'a' && command <= 'z') command -= ('a' - 'A');
        if (validCommand(command)) xQueueSend(commandQueue, &command, 0);
      }
    }

    // Network writes are isolated here. A slow/dead PC can never delay IMU
    // acquisition, ESP-NOW request/reply, fusion or the 40 Hz output schedule.
    while (xQueueReceive(txQueue, &message, 0) == pdTRUE) {
      if (!client || !client.connected()) continue;
      size_t length = strnlen(message.text, MESSAGE_BYTES);
      if (client.write(reinterpret_cast<const uint8_t*>(message.text), length) != length) {
        client.stop();
        break;
      }
    }
    vTaskDelay(pdMS_TO_TICKS(2));
  }
}

inline bool begin(uint8_t channel) {
  radioChannel = channel;
  txQueue = xQueueCreate(32, sizeof(Message));
  commandQueue = xQueueCreate(16, sizeof(char));
  if (!txQueue || !commandQueue) {
    Serial.println("[WIFI] Cannot allocate PC link queues.");
    return false;
  }
  if (!WiFi.softAP(SSID, PASSWORD, radioChannel, false, 1)) {
    Serial.println("[WIFI] Cannot start ACL_Knee.");
    return false;
  }
  server.begin();
  server.setNoDelay(true);
  if (xTaskCreate(networkTask, "knee-pc-link", 4096, nullptr, 1, nullptr) != pdPASS) {
    Serial.println("[WIFI] Cannot start PC link task.");
    return false;
  }
  ready = true;
  Serial.printf("[WIFI] SSID=%s IP=%s PORT=%u CHANNEL=%u\n",
                SSID, WiFi.softAPIP().toString().c_str(), PORT, radioChannel);
  return true;
}

inline void print(const char* text) {
  Serial.print(text); // USB remains available for upload and diagnostics.
  if (!ready || !text) return;
  Message message = {};
  strncpy(message.text, text, MESSAGE_BYTES - 1);
  if (xQueueSend(txQueue, &message, 0) == pdTRUE) return;
  // Keep recent state useful: discard one old line rather than blocking fusion.
  Message discarded;
  xQueueReceive(txQueue, &discarded, 0);
  xQueueSend(txQueue, &message, 0);
  droppedMessages++;
}

inline char readCommand() {
  char command = 0;
  if (Serial.available()) {
    command = static_cast<char>(Serial.read());
    if (command >= 'a' && command <= 'z') command -= ('a' - 'A');
    return validCommand(command) ? command : 0;
  }
  if (ready && xQueueReceive(commandQueue, &command, 0) == pdTRUE) return command;
  return 0;
}

inline void clearCommands() {
  while (Serial.available()) Serial.read();
  if (!ready) return;
  char command;
  while (xQueueReceive(commandQueue, &command, 0) == pdTRUE) {}
}
} // namespace PcWifiLink
