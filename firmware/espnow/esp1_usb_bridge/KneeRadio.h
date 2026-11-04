#pragma once
// Shared protocol; keep the copy in esp2_knee_sensor identical.
// Arduino ESP32 3.3.4 / ESP-IDF 5.5. Each packet is below 250 bytes.
#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_err.h>
#include <esp_wifi.h>
#include <esp_system.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>

namespace KneeRadio {
constexpr uint8_t CHANNEL = 1;
const uint8_t BRIDGE_MAC[6] = {0xB8,0xD6,0x1A,0xB8,0xA5,0x40};
const uint8_t SENSOR_MAC[6] = {0x68,0xFE,0x71,0x12,0x5B,0x68};
enum Kind : uint8_t { HELLO=1, REQUEST=2, SAMPLE=3 };
struct Packet {
  uint32_t magic = 0x4B4E4533;
  uint32_t session = 0;
  uint32_t sequence = 0;
  uint32_t target = 0;
  uint32_t sampleUs = 0;
  uint8_t version = 3;
  uint8_t kind = 0;
  uint8_t status = 0; // 0 valid, 1 IMU unavailable
  uint8_t reserved = 0;
  uint8_t raw[14] = {};
  uint16_t padding = 0;
};
static_assert(sizeof(Packet) == 40, "Wire layout changed");
struct Received { Packet packet; uint32_t receivedAt; };
struct Outgoing { Packet packet; uint32_t queuedAt; };
static QueueHandle_t rxQueue = nullptr, txQueue = nullptr;
static SemaphoreHandle_t sentSignal = nullptr;
static uint8_t peerMac[6];
static uint32_t bootId;
static bool ready = false;
static volatile bool txFault = false;
static char initFailure[144] = "not started";

inline bool failInit(const char* stage) {
  snprintf(initFailure,sizeof(initFailure),"%s",stage);
  Serial.printf("[ERROR] ESP-NOW init failed at %s.\n",stage);
  return false;
}
inline bool failInit(const char* stage,esp_err_t error) {
  snprintf(initFailure,sizeof(initFailure),"%s: %s (0x%X)",stage,
           esp_err_to_name(error),(unsigned)error);
  Serial.printf("[ERROR] ESP-NOW init failed at %s: %s (0x%X).\n",
                stage,esp_err_to_name(error),(unsigned)error);
  return false;
}

inline bool validCommand(char c) { return c=='C'||c=='F'||c=='L'||c=='R'; }
inline void onReceive(const esp_now_recv_info_t* info, const uint8_t* bytes, int len) {
  if (!info || len != sizeof(Packet) || memcmp(info->src_addr, peerMac, 6)) return;
  Received rx;
  memcpy(&rx.packet, bytes, sizeof(Packet));
  const auto& p = rx.packet;
  if (p.magic != 0x4B4E4533 || p.version != 3 || p.kind < HELLO || p.kind > SAMPLE) return;
  rx.receivedAt = millis();
  xQueueSend(rxQueue, &rx, 0); // Callback only copies; application handles packets.
}
inline void onSent(const esp_now_send_info_t*, esp_now_send_status_t) {
  xSemaphoreGive(sentSignal);
}
inline void transmitTask(void*) {
  Outgoing tx;
  for (;;) {
    if (xQueueReceive(txQueue, &tx, portMAX_DELAY) != pdTRUE) continue;
    if (txFault) continue;
    if (millis()-tx.queuedAt>15) continue; // Drop stale buffered packets.
    const auto& p=tx.packet;
    // One outstanding radio send; never overlap send callbacks.
    if (esp_now_send(peerMac, reinterpret_cast<const uint8_t*>(&p), sizeof(p)) == ESP_OK) {
      if (xSemaphoreTake(sentSignal, pdMS_TO_TICKS(1000)) != pdTRUE) txFault = true;
    }
  }
}
inline bool send(const Packet& p, bool priority=false) {
  if (!ready || txFault) return false;
  Outgoing tx{p,millis()};
  return (priority ? xQueueSendToFront(txQueue,&tx,0) : xQueueSend(txQueue,&tx,0)) == pdTRUE;
}
inline bool receive(Received& rx) { return xQueueReceive(rxQueue,&rx,0) == pdTRUE; }
inline bool begin(bool bridge) {
  const uint8_t* expected = bridge ? BRIDGE_MAC : SENSOR_MAC;
  memcpy(peerMac, bridge ? SENSOR_MAC : BRIDGE_MAC, 6);
  if (!WiFi.mode(WIFI_STA)) return failInit("WiFi.mode(WIFI_STA)");
  delay(100);
  uint8_t actual[6];
  esp_err_t error=esp_wifi_get_mac(WIFI_IF_STA,actual);
  if (error != ESP_OK) return failInit("esp_wifi_get_mac",error);
  Serial.printf("[ESPNOW] MAC=%02X:%02X:%02X:%02X:%02X:%02X channel=%u\n",
                actual[0],actual[1],actual[2],actual[3],actual[4],actual[5],CHANNEL);
  if (memcmp(actual,expected,6)) {
    snprintf(initFailure,sizeof(initFailure),
      "MAC mismatch: actual %02X:%02X:%02X:%02X:%02X:%02X, expected %02X:%02X:%02X:%02X:%02X:%02X",
      actual[0],actual[1],actual[2],actual[3],actual[4],actual[5],
      expected[0],expected[1],expected[2],expected[3],expected[4],expected[5]);
    Serial.printf("[ERROR] %s. Upload the correct ESP1/ESP2 sketch.\n",initFailure);
    return false;
  }
  error=esp_wifi_set_channel(CHANNEL,WIFI_SECOND_CHAN_NONE);
  if (error != ESP_OK) return failInit("esp_wifi_set_channel",error);
  error=esp_wifi_set_ps(WIFI_PS_NONE);
  if (error != ESP_OK)
    Serial.printf("[WARN] Cannot disable WiFi power save: %s (0x%X); continuing.\n",
                  esp_err_to_name(error),(unsigned)error);
  if (!WiFi.setTxPower(WIFI_POWER_11dBm))
    Serial.println("[WARN] Cannot set 11 dBm TX power; continuing with board default.");
  rxQueue=xQueueCreate(24,sizeof(Received));
  txQueue=xQueueCreate(24,sizeof(Outgoing));
  sentSignal=xSemaphoreCreateBinary();
  if (!rxQueue || !txQueue || !sentSignal) return failInit("FreeRTOS queue/semaphore allocation");
  error=esp_now_init();
  if (error!=ESP_OK) return failInit("esp_now_init",error);
  error=esp_now_register_recv_cb(onReceive);
  if (error!=ESP_OK) return failInit("esp_now_register_recv_cb",error);
  error=esp_now_register_send_cb(onSent);
  if (error!=ESP_OK) return failInit("esp_now_register_send_cb",error);
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr,peerMac,6);
  peer.channel=CHANNEL; peer.ifidx=WIFI_IF_STA; peer.encrypt=false;
  error=esp_now_add_peer(&peer);
  if (error!=ESP_OK) return failInit("esp_now_add_peer",error);
  bootId=esp_random(); if (!bootId) bootId=1;
  if (xTaskCreate(transmitTask,"knee-radio-tx",3072,nullptr,2,nullptr)!=pdPASS)
    return failInit("radio transmit task creation");
  ready=true;
  Serial.println("[ESPNOW] Radio ready.");
  return true;
}
inline void fatal() {
  for (;;) {
    Serial.printf("[FATAL] Radio stopped: %s. Fix the cause, then press EN/reset.\n",initFailure);
    delay(5000);
  }
}
} // namespace KneeRadio
