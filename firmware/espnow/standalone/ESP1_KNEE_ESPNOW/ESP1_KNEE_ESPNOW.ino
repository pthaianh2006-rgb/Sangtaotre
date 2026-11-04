// ESP1 FINAL - USB PC + IMU1 + tinh goc goi; mot file duy nhat.
// Board: NodeMCU-32S. IMU SDA=21, SCL=22. ESP-NOW channel 1.

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


// Each numbered response is used once. Both boards read after request dispatch.
// Readings are close in time, but are not hardware-synchronized.
struct ImuPair {
  uint8_t thigh[14],shank[14];
  uint32_t thighUs=0,shankUs=0,roundTripUs=0,epoch=0;
};
class SplitAcquisition {
  uint32_t lastAttemptUs=0,sequence=0,lastProbe=0;
  uint32_t reportAt=0,reportedAccepted=0;
  void hello(const KneeRadio::Packet& p) {
    if(p.kind!=KneeRadio::HELLO) return;
    if(remoteBoot!=p.session) {
      remoteBoot=p.session;epoch++;
      Serial.println("[ESPNOW] ESP2 boot detected. New bias and C/F required.");
    }
    remoteImuReady=p.status==0;lastHello=millis();
  }
public:
  ImuOne local;
  bool localReady=false,remoteImuReady=false;
  uint32_t remoteBoot=0,epoch=0,lastHello=0,lastGood=0;
  uint32_t accepted=0,rejected=0,maxRoundTripUs=0;
  uint32_t attempted=0,timeouts=0,localErrors=0,remoteErrors=0;
  uint32_t late=0,wrongId=0,badFrame=0,sendBusy=0;
  float meanRttUs=0;
  const char* lastFault="waiting for first sample";
  void begin() { localReady=local.begin(); }
  void service() {
    KneeRadio::Received rx;
    while(KneeRadio::receive(rx)) {
      if(millis()-rx.receivedAt<=1000) hello(rx.packet);
    }
    if(!localReady && millis()-lastProbe>=2000) {
      lastProbe=millis();localReady=local.begin();
    }
  }
  void report() {
    uint32_t now=millis(),elapsed=now-reportAt;
    if(elapsed<3000) return;
    float hz=(accepted-reportedAccepted)*1000.0f/elapsed;
    Serial.printf("[LINK] %.1f Hz; RTT %.1f/max %.1f ms; timeout=%lu local=%lu remote=%lu late=%lu bad=%lu busy=%lu; %s\n",
      hz,meanRttUs/1000,maxRoundTripUs/1000.0f,(unsigned long)timeouts,
      (unsigned long)localErrors,(unsigned long)remoteErrors,(unsigned long)late,
      (unsigned long)badFrame,(unsigned long)sendBusy,lastFault);
    reportAt=now;reportedAccepted=accepted;maxRoundTripUs=0;
  }
  bool take(ImuPair& pair) {
    service();
    if(KneeRadio::txFault) { lastFault="radio TX stalled: reset board";return false; }
    if(!localReady) { lastFault="IMU1 unavailable";return false; }
    if(!remoteBoot || millis()-lastHello>2000) { lastFault="ESP2 not responding";return false; }
    if(!remoteImuReady) { lastFault="IMU2 unavailable";return false; }
    while(uint32_t(micros()-lastAttemptUs)<10000) delay(1);
    const uint32_t start=micros();lastAttemptUs=start;attempted++;
    const uint32_t targetBoot=remoteBoot,targetEpoch=epoch;
    KneeRadio::Packet req;
    req.kind=KneeRadio::REQUEST;req.session=KneeRadio::bootId;
    req.target=targetBoot;req.sequence=++sequence;
    // TX runs in its own task while this board reads the local I2C bus.
    if(!KneeRadio::send(req,true)) { rejected++;sendBusy++;lastFault="radio send queue busy";return false; }
    if(!local.read(pair.thigh,pair.thighUs)) {
      if(local.needsRecovery()) localReady=false;
      rejected++;localErrors++;lastFault="IMU1 read rejected";return false;
    }
    while(uint32_t(micros()-start)<=20000) {
      KneeRadio::Received rx;
      while(KneeRadio::receive(rx)) {
        const auto& p=rx.packet;
        if(p.kind==KneeRadio::HELLO) {
          hello(p);
          if(epoch!=targetEpoch) { lastFault="ESP2 restarted";return false; }
          continue;
        }
        if(p.kind!=KneeRadio::SAMPLE || p.session!=targetBoot ||
           p.target!=KneeRadio::bootId || p.sequence!=req.sequence) { wrongId++;continue; }
        uint32_t elapsed=micros()-start;
        if(elapsed>20000) { rejected++;late++;lastFault="reply too late";return false; }
        if(p.status) { rejected++;remoteErrors++;lastFault="IMU2 read rejected";return false; }
        float a[3],g[3];const float zero[3]={0,0,0};
        if(!ImuOne::decode(p.raw,a,g,zero)) { rejected++;badFrame++;lastFault="bad IMU2 frame";return false; }
        memcpy(pair.shank,p.raw,14);
        pair.shankUs=p.sampleUs;pair.roundTripUs=elapsed;pair.epoch=epoch;
        lastGood=millis();accepted++;lastFault="OK";
        meanRttUs=accepted==1?elapsed:meanRttUs+0.05f*(elapsed-meanRttUs);
        if(elapsed>maxRoundTripUs)maxRoundTripUs=elapsed;
        return true;
      }
      delay(1);
    }
    rejected++;timeouts++;lastFault="sample reply timeout";return false;
  }
};

#include <Arduino.h>

// Median-5 -> 400 deg/s rate limit -> time-aware adaptive EMA.
// Process each fresh pair, independently of Serial output frequency.
class KneeOutputFilter {
  float window[5]={180,180,180,180,180};
  float limited=180,filtered=180,speed=0;
  uint32_t previousUs=0;
  bool timed=false;
public:
  void reset(float seed=180) {
    for(float& v:window) v=seed;
    limited=filtered=seed;speed=0;timed=false;previousUs=0;
  }
  float value() const { return filtered; }
  bool update(float knee,uint32_t sampleUs) {
    if(!isfinite(knee)) return false;
    float dt=timed ? uint32_t(sampleUs-previousUs)*1e-6f : 0.01f;
    if(dt<=0 || dt>0.1f) return false;
    previousUs=sampleUs;timed=true;
    for(int i=0;i<4;i++) window[i]=window[i+1];
    window[4]=knee;
    float sorted[5];memcpy(sorted,window,sizeof(sorted));
    for(int i=1;i<5;i++) {
      float v=sorted[i];int j=i-1;
      while(j>=0 && sorted[j]>v) { sorted[j+1]=sorted[j];j--; }
      sorted[j+1]=v;
    }
    float delta=sorted[2]-limited;
    const float maxStep=400.0f*dt;
    delta=fmaxf(-maxStep,fminf(maxStep,delta));
    limited+=delta;
    // Low-pass speed so one small noise spike cannot switch the EMA abruptly.
    speed+=(1-expf(-dt/0.08f))*(fabsf(delta)/dt-speed);
    // At rest: same time constant as original EMA 0.25 at 50 ms.
    // During movement: shorten lag, while retaining the median/rate protections.
    float tau=0.173803f/(1+fmaxf(0.0f,speed-20.0f)/20.0f);
    tau=fmaxf(0.035f,tau);
    filtered+=(1-expf(-dt/tau))*(limited-filtered);
    return isfinite(filtered);
  }
};

// ESP1: USB PC + ONE thigh MPU6050; all knee fusion runs here.
// ESP2 supplies ONE shank MPU6050 via numbered ESP-NOW request/reply.
// Original fusion retained; output filtering is time-aware and adaptive.
static constexpr float BETA=0.10f, AXIS_SIGN=-1.0f;
static constexpr uint32_t PRINT_MS=25; // Target 40 Hz; fusion about 100 Hz.
float qTh[4]={1,0,0,0}, qSh[4]={1,0,0,0};
float gbTh[3]={0,0,0},gbSh[3]={0,0,0};
float qRel0[4]={1,0,0,0},kneeAxis[3]={0,0,1};
KneeOutputFilter outputFilter;
bool calibrated=false,axisCalibrated=false,logOn=false,biasReady=false;
uint32_t lastThUs=0,lastShUs=0,lastPrintMs=0,biasEpoch=0;
SplitAcquisition acquisition;

void broadcast(const char* text) { Serial.print(text); }
void resetOutput() { outputFilter.reset(); }
void invalidate() {
  biasReady=calibrated=axisCalibrated=false;
  resetOutput();
}
bool stale() {
  return acquisition.epoch!=biasEpoch || millis()-acquisition.lastGood>100;
}
void madgwick(float*q, float gx,float gy,float gz, float ax,float ay,float az, float dt){
  float q0=q[0],q1=q[1],q2=q[2],q3=q[3],n;
  float qd1=0.5f*(-q1*gx-q2*gy-q3*gz),qd2=0.5f*(q0*gx+q2*gz-q3*gy),qd3=0.5f*(q0*gy-q1*gz+q3*gx),qd4=0.5f*(q0*gz+q1*gy-q2*gx);
  if(!(ax==0&&ay==0&&az==0)){
    n=1.0f/sqrtf(ax*ax+ay*ay+az*az); ax*=n;ay*=n;az*=n;
    float _2q0=2*q0,_2q1=2*q1,_2q2=2*q2,_2q3=2*q3,_4q0=4*q0,_4q1=4*q1,_4q2=4*q2,_8q1=8*q1,_8q2=8*q2;
    float q0q0=q0*q0,q1q1=q1*q1,q2q2=q2*q2,q3q3=q3*q3;
    float s0=_4q0*q2q2+_2q2*ax+_4q0*q1q1-_2q1*ay;
    float s1=_4q1*q3q3-_2q3*ax+4*q0q0*q1-_2q0*ay-_4q1+_8q1*q1q1+_8q1*q2q2+_4q1*az;
    float s2=4*q0q0*q2+_2q0*ax+_4q2*q3q3-_2q3*ay-_4q2+_8q2*q1q1+_8q2*q2q2+_4q2*az;
    float s3=4*q1q1*q3-_2q1*ax+4*q2q2*q3-_2q2*ay;
    float grad=sqrtf(s0*s0+s1*s1+s2*s2+s3*s3);
    if(grad>1e-9f) {
      n=1.0f/grad; s0*=n;s1*=n;s2*=n;s3*=n;
      qd1-=BETA*s0;qd2-=BETA*s1;qd3-=BETA*s2;qd4-=BETA*s3;
    }
  }
  q0+=qd1*dt;q1+=qd2*dt;q2+=qd3*dt;q3+=qd4*dt;
  n=1.0f/sqrtf(q0*q0+q1*q1+q2*q2+q3*q3); q[0]=q0*n;q[1]=q1*n;q[2]=q2*n;q[3]=q3*n;
}
void qmul(const float*a,const float*b,float*r){
  r[0]=a[0]*b[0]-a[1]*b[1]-a[2]*b[2]-a[3]*b[3]; r[1]=a[0]*b[1]+a[1]*b[0]+a[2]*b[3]-a[3]*b[2];
  r[2]=a[0]*b[2]-a[1]*b[3]+a[2]*b[0]+a[3]*b[1]; r[3]=a[0]*b[3]+a[1]*b[2]-a[2]*b[1]+a[3]*b[0];
}
void qconj(const float*a,float*r){ r[0]=a[0];r[1]=-a[1];r[2]=-a[2];r[3]=-a[3]; }

// Khi cam bien DUNG YEN: (1) tinh chinh lai bias, (2) KHOA gyro=0 (ZUPT)
// -> goc khong the tu tang khi de yen, dong thoi diet yaw drift
void stationaryUpdate(float* gb, float* g, const float* a, float dt){
  float gmag=sqrtf(g[0]*g[0]+g[1]*g[1]+g[2]*g[2]);          // toc do xoay con lai (rad/s)
  float amag=sqrtf(a[0]*a[0]+a[1]*a[1]+a[2]*a[2]);          // do lon gia toc (g)
  if(gmag<0.06f && fabsf(amag-1.0f)<0.08f){                 // dung yen + chi co trong luc
    float gain=1.0f-powf(0.98f,dt/0.01f);
    for(int k=0;k<3;k++){ gb[k]+=gain*g[k]; g[k]=0.0f; }   // tinh chinh bias + khoa gyro
  }
}

bool stepFusion(float* qRel) {
  ImuPair pair;
  if(!acquisition.take(pair)) return false;
  if(pair.epoch!=biasEpoch) { acquisition.lastFault="ESP2 session changed"; invalidate(); return false; }
  float aTh[3],gTh[3],aSh[3],gSh[3];
  if(!ImuOne::decode(pair.thigh,aTh,gTh,gbTh) ||
     !ImuOne::decode(pair.shank,aSh,gSh,gbSh)) return false;
  // Each IMU uses its OWN sample interval. Never subtract clocks of different ESPs.
  float dtTh=uint32_t(pair.thighUs-lastThUs)/1e6f;
  float dtSh=uint32_t(pair.shankUs-lastShUs)/1e6f;
  lastThUs=pair.thighUs; lastShUs=pair.shankUs;
  if(dtTh<=0 || dtSh<=0 || dtTh>0.1f || dtSh>0.1f) { acquisition.lastFault="sample time discontinuity"; invalidate(); return false; }
  stationaryUpdate(gbTh,gTh,aTh,dtTh); stationaryUpdate(gbSh,gSh,aSh,dtSh);
  int steps=(int)ceilf(fmaxf(dtTh,dtSh)/0.01f);
  for(int i=0;i<steps;i++) {
    madgwick(qTh,gTh[0],gTh[1],gTh[2],aTh[0],aTh[1],aTh[2],dtTh/steps);
    madgwick(qSh,gSh[0],gSh[1],gSh[2],aSh[0],aSh[1],aSh[2],dtSh/steps);
  }
  for(int k=0;k<4;k++) {
    if(!isfinite(qTh[k])||!isfinite(qSh[k])) { acquisition.lastFault="non-finite quaternion"; invalidate(); return false; }
  }
  float conjugate[4]; qconj(qTh,conjugate); qmul(conjugate,qSh,qRel);
  return true;
}

// Same gravity-based initialization as the original initQuat().
void initFromGravity(const float* a,float* q) {
  float ax=a[0],ay=a[1],az=a[2];
  float n=sqrtf(ax*ax+ay*ay+az*az);
  if(n<1e-6f) { q[0]=1; q[1]=q[2]=q[3]=0; return; }
  ax/=n;ay/=n;az/=n;
  float roll=atan2f(ay,az),pitch=atan2f(-ax,sqrtf(ay*ay+az*az));
  float cr=cosf(roll*0.5f),sr=sinf(roll*0.5f);
  float cp=cosf(pitch*0.5f),sp=sinf(pitch*0.5f);
  q[0]=cr*cp; q[1]=sr*cp; q[2]=cr*sp; q[3]=-sr*sp;
}

bool initializePair() {
  broadcast("[INFO] Giu YEN ca IMU dui va cang chan: dang lay 300 mau gyro bias...\n");
  float sumTh[3]={},sumSh[3]={},zero[3]={},accTh[3]={},accSh[3]={};
  uint32_t start=millis(),epoch=acquisition.epoch;
  int count=0;
  ImuPair pair;
  while(count<300 && millis()-start<8000) {
    if(!acquisition.take(pair)) { delay(1); continue; }
    if(pair.epoch!=epoch) return false;
    float at[3],gt[3],as[3],gs[3];
    if(!ImuOne::decode(pair.thigh,at,gt,zero)||!ImuOne::decode(pair.shank,as,gs,zero)) continue;
    for(int k=0;k<3;k++) {
      if(fabsf(gt[k])>0.15f || fabsf(gs[k])>0.15f) return false;
      sumTh[k]+=gt[k]; sumSh[k]+=gs[k];
    }
    count++;
  }
  if(count!=300) return false;
  for(int k=0;k<3;k++) { gbTh[k]=sumTh[k]/300; gbSh[k]=sumSh[k]/300; }
  // Collect 60 fresh pairs for gravity initialization, as in the old firmware.
  count=0; start=millis();
  while(count<60 && millis()-start<3000) {
    if(!acquisition.take(pair)) { delay(1); continue; }
    if(pair.epoch!=epoch) return false;
    float at[3],gt[3],as[3],gs[3];
    if(!ImuOne::decode(pair.thigh,at,gt,gbTh)||!ImuOne::decode(pair.shank,as,gs,gbSh)) continue;
    for(int k=0;k<3;k++) {
      if(fabsf(gt[k])>0.15f || fabsf(gs[k])>0.15f) return false;
      accTh[k]+=at[k]/60; accSh[k]+=as[k]/60;
    }
    count++;
  }
  if(count!=60) return false;
  initFromGravity(accTh,qTh);initFromGravity(accSh,qSh);
  lastThUs=pair.thighUs;lastShUs=pair.shankUs;biasEpoch=epoch;
  biasReady=true;calibrated=axisCalibrated=false;
  resetOutput();
  while(Serial.available()) Serial.read(); // Do not apply commands typed during bias later.
  broadcast("[INFO] bias OK ca hai IMU. DUOI THANG bam C; sau do F va gap-duoi 6s.\n");
  return true;
}

void functionalCalib() {
  if(!calibrated) { broadcast("[ERROR] Bam C truoc F.\n"); return; }
  broadcast("[F] Tu DUOI THANG: tu tu GAP xuong roi DUOI ra (6s)...\n");
  float qRef[4];
  if(!stepFusion(qRef)) { broadcast("[F] Chua co cap mau moi. Thu F lai.\n"); return; }
  float qRefc[4];qconj(qRef,qRefc);
  float axisSum[3]={0,0,0};uint32_t t0=millis();
  axisCalibrated=false;
  while(millis()-t0<6000) {
    float qRel[4];
    if(!stepFusion(qRel)) {
      if(!biasReady || stale()) { invalidate(); broadcast("[F] Huy: mat mau IMU/ESP2. Can bias va C lai.\n"); return; }
      delay(1);continue;
    }
    float dq[4];qmul(qRefc,qRel,dq);
    float qv[3]={dq[1],dq[2],dq[3]};
    float mag=sqrtf(qv[0]*qv[0]+qv[1]*qv[1]+qv[2]*qv[2]);
    if(mag>0.02f) {
      float dot=qv[0]*axisSum[0]+qv[1]*axisSum[1]+qv[2]*axisSum[2];
      float sign=(dot<0)?-1.0f:1.0f;
      for(int k=0;k<3;k++) axisSum[k]+=sign*qv[k];
    }
    delay(3);
  }
  float n=sqrtf(axisSum[0]*axisSum[0]+axisSum[1]*axisSum[1]+axisSum[2]*axisSum[2]);
  if(n<0.05f) { broadcast("[F] That bai - gap manh hon roi thu lai\n"); return; }
  for(int k=0;k<3;k++) kneeAxis[k]=axisSum[k]/n;
  axisCalibrated=true;
  char b[80];snprintf(b,sizeof(b),"[F] truc gap=%.2f,%.2f,%.2f OK\n",kneeAxis[0],kneeAxis[1],kneeAxis[2]);broadcast(b);
}

void setup() {
  Serial.begin(115200);
  // Let the local MPU6050 power settle, as proven by the standalone test.
  delay(500);
  Wire.begin(21,22);Wire.setClock(100000);Wire.setTimeOut(50);
  // Detect and initialize ESP1's IMU1 before starting ESP-NOW.
  acquisition.begin();
  if(!KneeRadio::begin(true)) KneeRadio::fatal();
  broadcast("[ROLE] ESP1 THIGH + KNEE FUSION. ESP2 holds SHANK IMU. Commands C/F/L/R.\n");
}

void loop() {
  acquisition.service();
  acquisition.report();
  char cmd=0;
  if(Serial.available()) { cmd=Serial.read(); if(cmd>='a'&&cmd<='z') cmd-=32; }
  if(cmd=='L') { logOn=!logOn;broadcast(logOn?"[LOG] on (millis,knee)\n":"[LOG] off\n"); }
  if(cmd=='R') { calibrated=axisCalibrated=false;resetOutput();broadcast("[RESET]\n"); }
  static uint32_t lastNotice=0,lastBiasAttempt=0;
  if(!biasReady) {
    if(cmd=='C'||cmd=='F') broadcast("[CMD] Wait for bias OK before C/F.\n");
    if(acquisition.localReady && acquisition.remoteImuReady &&
       millis()-acquisition.lastHello<2000 && millis()-lastBiasAttempt>=2000) {
      if(!initializePair()) broadcast("[WARN] Bias chua dat: giu yen ca hai IMU, se thu lai.\n");
      lastBiasAttempt=millis();
    } else if(millis()-lastNotice>=1500) {
      lastNotice=millis();
      broadcast("[WAIT] Can IMU1 tai ESP1 va IMU2 tai ESP2. Kiem tra nguon/day, giu yen.\n");
    }
    delay(1);return;
  }
  float qRel[4];
  if(!stepFusion(qRel)) {
    if(!biasReady || stale()) {
      invalidate();
      Serial.printf("[ERROR] %s; mat mau >100ms / doi phien. Dung xuat goc; can bias va C/F lai.\n",acquisition.lastFault);
    }
    if(cmd=='C'||cmd=='F') broadcast("[CMD] No fresh paired sample. Retry C/F when link is ready.\n");
    delay(1);return;
  }
  if(cmd=='C') {
    for(int k=0;k<4;k++) qRel0[k]=qRel[k];
    calibrated=true;resetOutput();broadcast("[CALIB] 180deg set!\n");
  }
  if(cmd=='F') {
    functionalCalib();
    outputFilter.reset(outputFilter.value());
    return; // Never output the pre-F quaternion as a post-F measurement.
  }
  float knee=180.0f;
  if(calibrated){
    float qR0c[4],dq[4]; qconj(qRel0,qR0c); qmul(qR0c,qRel,dq);
    // q and -q represent the same orientation. Choose a consistent hemisphere.
    if(dq[0]<0) for(int k=0;k<4;k++) dq[k]=-dq[k];
    float flex;
    if(axisCalibrated){
      float qv[3]={dq[1],dq[2],dq[3]};
      float d=qv[0]*kneeAxis[0]+qv[1]*kneeAxis[1]+qv[2]*kneeAxis[2];
      flex=AXIS_SIGN*2.0f*atan2f(d,dq[0])*(180.0f/M_PI);   // swing-twist: chi lay xoay quanh truc gap
    } else {
      float w=fabsf(dq[0]); w=w>1?1:w; flex=2.0f*acosf(w)*(180.0f/M_PI);  // fallback Tang1
    }
    knee=180.0f-flex;

    // ===== HUONG DAN CAN CHINH khi gan duoi thang (chi khi KHONG ghi) =====
    static uint32_t lastAlign=0;
    if(!logOn && fabsf(flex)<20.0f && millis()-lastAlign>=400){
      lastAlign=millis();
      float rx=2.0f*dq[1]*57.2958f, ry=2.0f*dq[2]*57.2958f, rz=2.0f*dq[3]*57.2958f; // do lech 3 truc (do)
      float dev=sqrtf(rx*rx+ry*ry+rz*rz);                                            // tong do lech
      char ab[90];
      if(dev<=3.0f){
        snprintf(ab,sizeof(ab),"[ALIGN] OK - thang hang (lech %.1f do)\n",dev);
      } else {
        float ax=fabsf(rx),ay=fabsf(ry),az=fabsf(rz); int mi=0; float mv=ax;          // chon truc lech lon nhat
        if(ay>mv){mi=1;mv=ay;} if(az>mv){mi=2;mv=az;}
        const char* dir;
        if(mi==0)      dir=(rx>0)?"HA dau cam bien xuong":"NANG dau cam bien len";
        else if(mi==1) dir=(ry>0)?"NGHIENG sang TRAI":"NGHIENG sang PHAI";
        else           dir=(rz>0)?"XOAY sang PHAI":"XOAY sang TRAI";
        snprintf(ab,sizeof(ab),"[ALIGN] Lech %.1f do -> %s\n",dev,dir);
      }
      broadcast(ab);
    }
  }

  if(!outputFilter.update(knee,lastThUs)) {
    invalidate(); broadcast("[ERROR] Invalid filter input/timing. Recalibration required.\n"); return;
  }
  uint32_t now=millis();
  if(now-lastPrintMs>=PRINT_MS) {
    // Preserve cadence instead of accumulating 5-10ms rounding on every output.
    lastPrintMs+=PRINT_MS;
    if(now-lastPrintMs>=PRINT_MS) lastPrintMs=now;
    char buf[48];
    float kOut=roundf(outputFilter.value()*10.0f)/10.0f;
    if(!calibrated) snprintf(buf,sizeof(buf),"... send C\n");
    else if(logOn) snprintf(buf,sizeof(buf),"%lu,%.1f\n",(unsigned long)now,kOut);
    else snprintf(buf,sizeof(buf),"Knee:%.1f %s\n",kOut,axisCalibrated?"[axis]":"[no-axis]");
    broadcast(buf);
  }
}
