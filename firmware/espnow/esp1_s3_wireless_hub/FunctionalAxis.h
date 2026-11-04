#pragma once
#include <Arduino.h>

// Dominant hinge axis, weighted by rotation magnitude. First-frame jitter
// cannot choose its sign. A valid bend during F defines positive flexion.
class FunctionalAxis {
  float covariance[3][3]={},peak[3]={};
  float maximum=0,trace=0;
  uint32_t count=0;
public:
  void add(const float* dq) {
    float n=sqrtf(dq[0]*dq[0]+dq[1]*dq[1]+dq[2]*dq[2]+dq[3]*dq[3]);
    if(!isfinite(n)||n<1e-6f) return;
    float sign=dq[0]<0?-1.0f:1.0f;
    float v[3]={sign*dq[1]/n,sign*dq[2]/n,sign*dq[3]/n};
    float square=v[0]*v[0]+v[1]*v[1]+v[2]*v[2];
    if(square<0.007596f) return; // Below 10 degrees: do not learn jitter near zero.
    if(square>maximum) { maximum=square;memcpy(peak,v,sizeof(peak)); }
    for(int i=0;i<3;i++) for(int j=0;j<3;j++) covariance[i][j]+=v[i]*v[j];
    trace+=square;count++;
  }
  bool finish(float* axis) const {
    if(count<20 || maximum<0.046846f || trace<1e-6f) return false; // At least 25 deg excursion.
    float n=sqrtf(maximum);
    for(int k=0;k<3;k++) axis[k]=peak[k]/n;
    for(int iteration=0;iteration<12;iteration++) {
      float next[3]={};
      for(int i=0;i<3;i++) for(int j=0;j<3;j++) next[i]+=covariance[i][j]*axis[j];
      n=sqrtf(next[0]*next[0]+next[1]*next[1]+next[2]*next[2]);
      if(!isfinite(n)||n<1e-6f) return false;
      for(int k=0;k<3;k++) axis[k]=next[k]/n;
    }
    float eigenvalue=0,dot=0;
    for(int i=0;i<3;i++) {
      dot+=axis[i]*peak[i];
      for(int j=0;j<3;j++) eigenvalue+=axis[i]*covariance[i][j]*axis[j];
    }
    if(eigenvalue/trace<0.90f) return false; // Mixed axes: ask for another F, not a bad calibration.
    if(dot<0) for(int k=0;k<3;k++) axis[k]=-axis[k];
    return true;
  }
};
