#pragma once
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
