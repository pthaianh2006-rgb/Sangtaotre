#pragma once
#include "KneeRadio.h"
#include "ImuOne.h"

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
