// ESP2: 68:FE:71:12:5B:68, independent power, ONE shank MPU6050.
// ESP1 requests samples; ESP2 returns raw accel/gyro + timestamp.
// All bias, fusion, C/F/L/R and knee calculations are coordinated on ESP1.
#include "KneeRadio.h"
#include "ImuOne.h"

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
