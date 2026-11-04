// ESP2 FINAL - nguon rieng + IMU2; mot file duy nhat.
// Board: DOIT ESP32 DEVKIT V1. IMU SDA=21, SCL=22. ESP-NOW channel 1.

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

#include <Arduino.h>
#include <Wire.h>

// One MPU6050 on this ESP32's private I2C bus. Accept either AD0 state:
// LOW = 0x68, HIGH = 0x69. ESP1 and ESP2 have separate I2C buses.
class ImuOne {
  uint8_t address=0,busFailures=0;
  bool busError() { if(busFailures<255) busFailures++; return false; }
  bool writeReg(uint8_t reg,uint8_t value) {
    Wire.beginTransmission(address); Wire.write(reg); Wire.write(value);
    return Wire.endTransmission()==0;
  }
  bool responds(uint8_t a) {
    Wire.beginTransmission(a);
    return Wire.endTransmission()==0;
  }
public:
  bool needsRecovery() const { return busFailures>=3; }
  bool begin() {
    address=0;busFailures=0;
    // Match the working legacy firmware: require an I2C ACK, but do not reject
    // MPU6050-compatible clones because their WHO_AM_I value differs.
    if(responds(0x68)) address=0x68;
    else if(responds(0x69)) address=0x69;
    if(!address) {
      Serial.println("[IMU] MPU6050 not found at 0x68 or 0x69. Check power, GND and SDA21/SCL22.");
      return false;
    }
    if(!writeReg(0x6B,0x80)) return false;
    delay(100);
    if(!writeReg(0x6B,0x01)) return false;
    delay(10);
    // Same as original: DLPF 21 Hz, gyro +/-250 deg/s, accel +/-2 g.
    if(!writeReg(0x1A,0x04)||!writeReg(0x1B,0x00)||!writeReg(0x1C,0x00)) return false;
    delay(10);
    Serial.printf("[IMU] Local MPU6050 at 0x%02X, SDA21/SCL22.\n",address);
    return true;
  }
  bool read(uint8_t* raw,uint32_t& sampleUs) {
    if(!address) return busError();
    uint32_t start=micros();
    Wire.beginTransmission(address); Wire.write(0x3B);
    if(Wire.endTransmission(false)!=0 || Wire.requestFrom((int)address,14,1)!=14) return busError();
    busFailures=0;
    for(int i=0;i<14;i++) raw[i]=Wire.read();
    sampleUs=start+(micros()-start)/2;
    float a[3],g[3]; const float zero[3]={0,0,0};
    return decode(raw,a,g,zero);
  }
  static bool decode(const uint8_t* b,float* ac,float* gy,const float* bias) {
    for(int i=0;i<3;i++) {
      ac[i]=(int16_t)((b[2*i]<<8)|b[2*i+1])/16384.0f;
      gy[i]=(int16_t)((b[8+2*i]<<8)|b[9+2*i])*(1.0f/131.0f)*(M_PI/180.0f)-bias[i];
    }
    float am=sqrtf(ac[0]*ac[0]+ac[1]*ac[1]+ac[2]*ac[2]);
    return am>=0.2f && am<=4.0f; // Same rejected accel range as original.
  }
};

// ESP2: 68:FE:71:12:5B:68, independent power, ONE shank MPU6050.
// ESP1 requests samples; ESP2 returns raw accel/gyro + timestamp.
// All bias, fusion, C/F/L/R and knee calculations are coordinated on ESP1.

ImuOne localImu;
bool imuReady=false;
uint32_t lastHello=0, lastProbe=0, lastRequest=0, lastStatus=0;

void setup() {
  Serial.begin(115200);
  // Let the local MPU6050 power settle, as proven by the standalone test.
  delay(500);
  Wire.begin(21,22); Wire.setClock(100000); Wire.setTimeOut(50);
  // Detect and initialize ESP2's IMU2 before starting ESP-NOW.
  imuReady=localImu.begin();
  if(!KneeRadio::begin(false)) KneeRadio::fatal();
  Serial.println("[ROLE] ESP2 SHANK: one MPU6050. Waiting for sample requests from ESP1.");
}

void loop() {
  uint32_t now=millis();
  if(!imuReady && now-lastProbe>=2000) {
    lastProbe=now; imuReady=localImu.begin();
  }
  if(now-lastHello>=500) {
    lastHello=now;
    KneeRadio::Packet p;
    p.kind=KneeRadio::HELLO; p.session=KneeRadio::bootId; p.status=imuReady?0:1;
    KneeRadio::send(p);
  }
  KneeRadio::Received rx;
  while(KneeRadio::receive(rx)) {
    const auto& request=rx.packet;
    if(request.kind!=KneeRadio::REQUEST || request.target!=KneeRadio::bootId ||
       millis()-rx.receivedAt>15) continue;
    lastRequest=millis();
    KneeRadio::Packet reply;
    reply.kind=KneeRadio::SAMPLE; reply.session=KneeRadio::bootId;
    reply.target=request.session; reply.sequence=request.sequence;
    reply.status=(imuReady && localImu.read(reply.raw,reply.sampleUs))?0:1;
    if(localImu.needsRecovery()) imuReady=false; // One rejected sample must not reset the IMU.
    KneeRadio::send(reply,true);
  }
  if(now-lastStatus>=3000) {
    lastStatus=now;
    if(KneeRadio::txFault) Serial.println("[ERROR] Radio stalled. Reset ESP2.");
    else if(!imuReady) Serial.println("[ERROR] Check local shank MPU6050 and SDA21/SCL22.");
    else Serial.println(now-lastRequest<2000 && lastRequest ?
      "[ESPNOW] Receiving requests and replying with IMU2 samples." :
      "[ESPNOW] Waiting for ESP1. IMU2 ready.");
  }
  delay(1);
}
