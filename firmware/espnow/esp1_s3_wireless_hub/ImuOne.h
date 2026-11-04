#pragma once
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
      Serial.println("[IMU] MPU6050 not found at 0x68 or 0x69. Check power, GND and SDA2/SCL1.");
      return false;
    }
    if(!writeReg(0x6B,0x80)) return false;
    delay(100);
    if(!writeReg(0x6B,0x01)) return false;
    delay(10);
    // Same as original: DLPF 21 Hz, gyro +/-250 deg/s, accel +/-2 g.
    if(!writeReg(0x1A,0x04)||!writeReg(0x1B,0x00)||!writeReg(0x1C,0x00)) return false;
    delay(10);
    Serial.printf("[IMU] Local MPU6050 at 0x%02X, SDA2/SCL1.\n",address);
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
