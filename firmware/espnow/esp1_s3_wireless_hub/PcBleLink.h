#pragma once
// S3 -> laptop BLE UART. ESP-NOW remains on Wi-Fi STA, fixed channel.
#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLE2902.h>
#include <esp_wifi.h>
#include <atomic>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

namespace PcBleLink {
constexpr const char* SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";
constexpr const char* RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e";
constexpr const char* TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";
constexpr size_t MESSAGE_BYTES = 192;
struct Message { uint32_t epoch; char text[MESSAGE_BYTES]; };
inline QueueHandle_t txQueue = nullptr, commandQueue = nullptr;
inline BLEServer* server = nullptr;
inline BLECharacteristic* tx = nullptr;
inline BLE2902* subscription = nullptr;
inline std::atomic<bool> connected{false}, advertiseAgain{false};
inline std::atomic<uint32_t> generation{0};
inline bool ready = false;
inline uint32_t droppedMessages = 0;

inline bool validCommand(char c) { return c=='C'||c=='F'||c=='L'||c=='R'; }
class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer*) override {
    generation.fetch_add(1);
    connected.store(true);
    Serial.println("[BLE] Laptop connected; waiting for notification subscription.");
  }
  void onDisconnect(BLEServer*) override {
    connected.store(false);
    generation.fetch_add(1);
    xQueueReset(commandQueue); // No queued command crosses a connection boundary.
    advertiseAgain.store(true);
    Serial.println("[BLE] Laptop disconnected.");
  }
};
class RxCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* characteristic) override {
    String value=characteristic->getValue();
    if(value.length()!=1 || !connected.load()) return;
    char c=value[0];
    if(c>='a' && c<='z') c-=('a'-'A');
    if(validCommand(c)) xQueueSend(commandQueue,&c,0);
  }
};
inline void bleTask(void*) {
  Message message;
  for(;;) {
    if(advertiseAgain.exchange(false)) {
      vTaskDelay(pdMS_TO_TICKS(200));
      if(!connected.load()) server->startAdvertising();
    }
    if(xQueueReceive(txQueue,&message,pdMS_TO_TICKS(5))!=pdTRUE) continue;
    if(!connected.load() || !subscription->getNotifications() || message.epoch!=generation.load()) continue;
    const uint32_t epoch=generation.load();
    const size_t length=strnlen(message.text,MESSAGE_BYTES);
    // 20 bytes works with the minimum ATT MTU, on all supported laptops.
    for(size_t offset=0;offset<length;offset+=20) {
      if(!connected.load() || generation.load()!=epoch) break;
      const size_t count=(length-offset>20)?20:length-offset;
      tx->setValue(reinterpret_cast<uint8_t*>(message.text)+offset,count);
      tx->notify();
      vTaskDelay(pdMS_TO_TICKS(2));
    }
  }
}
inline bool begin(uint8_t /* ESP-NOW channel stays unchanged */) {
  txQueue=xQueueCreate(16,sizeof(Message));
  commandQueue=xQueueCreate(16,sizeof(char));
  if(!txQueue||!commandQueue) return false;
  uint8_t mac[6];
  if(esp_wifi_get_mac(WIFI_IF_STA,mac)!=ESP_OK) return false;
  char name[24];
  snprintf(name,sizeof(name),"KneeROM_%02X%02X%02X",mac[3],mac[4],mac[5]);
  BLEDevice::init(name);
  server=BLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());
  BLEService* service=server->createService(SERVICE_UUID);
  tx=service->createCharacteristic(TX_UUID,BLECharacteristic::PROPERTY_NOTIFY);
  subscription=new BLE2902();
  tx->addDescriptor(subscription);
  auto* rx=service->createCharacteristic(RX_UUID,BLECharacteristic::PROPERTY_WRITE);
  rx->setCallbacks(new RxCallbacks());
  service->start();
  auto* advertising=server->getAdvertising();
  advertising->addServiceUUID(SERVICE_UUID);
  advertising->setScanResponse(true);
  advertising->setMinPreferred(0x0c); // Request 15-30 ms; laptop negotiates final interval.
  advertising->setMaxPreferred(0x18);
  advertising->start();
  if(xTaskCreate(bleTask,"knee-ble-tx",4096,nullptr,1,nullptr)!=pdPASS) return false;
  ready=true;
  Serial.printf("[BLE] Name=%s. Keep laptop on its normal Internet Wi-Fi.\n",name);
  return true;
}
inline void print(const char* text) {
  if(!text) return;
  Serial.print(text);
  if(!ready || !connected.load() || !subscription->getNotifications()) return;
  Message message={};
  message.epoch=generation.load();
  const size_t length=strnlen(text,MESSAGE_BYTES);
  // Never send a truncated line that could merge with the next knee sample.
  if(length>=MESSAGE_BYTES) { ++droppedMessages; return; }
  memcpy(message.text,text,length);
  if(xQueueSend(txQueue,&message,0)==pdTRUE) return;
  Message discarded;
  xQueueReceive(txQueue,&discarded,0);
  xQueueSend(txQueue,&message,0);
  ++droppedMessages;
}
inline char readCommand() {
  char c=0;
  if(Serial.available()) {
    c=static_cast<char>(Serial.read());
    if(c>='a' && c<='z') c-=('a'-'A');
    return validCommand(c)?c:0;
  }
  if(ready && xQueueReceive(commandQueue,&c,0)==pdTRUE) return c;
  return 0;
}
inline void clearCommands() {
  while(Serial.available()) Serial.read();
  if(ready) xQueueReset(commandQueue);
}
} // namespace PcBleLink
