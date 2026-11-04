#pragma once
#include "ImuOne.h"

// Confirmed mounting: both chip faces point outwards, shank MPU is rotated
// 180 degrees in its PCB plane relative to the thigh MPU at full extension.
// Bring BOTH accelerometer and gyro into the same segment-axis convention.
// Rotate before bias estimation/subtraction; do not flip only the final angle.
namespace Mounting {
constexpr bool SHANK_Z_180 = true;
inline bool decode(const uint8_t* raw,float* a,float* g,const float* bias,bool shank) {
  const float zero[3]={0,0,0};
  if(!ImuOne::decode(raw,a,g,zero)) return false;
  if(shank && SHANK_Z_180) {
    a[0]=-a[0];a[1]=-a[1];
    g[0]=-g[0];g[1]=-g[1];
  }
  for(int k=0;k<3;k++) g[k]-=bias[k];
  return true;
}
} // namespace Mounting
