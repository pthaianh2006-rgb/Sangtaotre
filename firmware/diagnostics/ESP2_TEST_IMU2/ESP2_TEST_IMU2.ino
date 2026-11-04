// ESP2 DOIT ESP32 DEVKIT V1: diagnostic for local IMU2 only.
// Wiring: SDA=GPIO21, SCL=GPIO22, common GND.
// This sketch checks both legal MPU6050 addresses and does not use ESP-NOW.

#include <Arduino.h>
#include <Wire.h>

static constexpr uint8_t SDA_PIN = 21;
static constexpr uint8_t SCL_PIN = 22;
static constexpr uint8_t ADDRESSES[] = {0x68, 0x69};
bool active[2] = {false, false};

uint8_t ping(uint8_t address) {
  Wire.beginTransmission(address);
  return Wire.endTransmission();
}

bool writeRegister(uint8_t address, uint8_t reg, uint8_t value) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool readRegisters(uint8_t address, uint8_t reg, uint8_t* data, size_t length) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((int)address, (int)length, 1) != length) return false;
  for (size_t i = 0; i < length; ++i) data[i] = Wire.read();
  return true;
}

bool initializeMpu(uint8_t address) {
  if (!writeRegister(address, 0x6B, 0x80)) return false;
  delay(100);
  if (!writeRegister(address, 0x6B, 0x01)) return false;
  delay(10);
  return writeRegister(address, 0x1A, 0x04) &&
         writeRegister(address, 0x1B, 0x00) &&
         writeRegister(address, 0x1C, 0x00);
}

void scanSensors() {
  Serial.println("\n[SCAN] Testing MPU6050 addresses 0x68 and 0x69...");
  int count = 0;
  for (int i = 0; i < 2; ++i) {
    uint8_t address = ADDRESSES[i];
    uint8_t error = ping(address);
    active[i] = error == 0;
    Serial.printf("[SCAN] 0x%02X: %s (I2C error=%u)\n",
                  address, active[i] ? "ACK" : "NO ACK", error);
    if (!active[i]) continue;
    ++count;

    uint8_t who = 0xFF;
    if (readRegisters(address, 0x75, &who, 1))
      Serial.printf("[SCAN] 0x%02X WHO_AM_I=0x%02X (informational only)\n", address, who);
    else
      Serial.printf("[WARN] 0x%02X ACKs, but WHO_AM_I could not be read.\n", address);

    if (initializeMpu(address))
      Serial.printf("[OK] MPU-compatible sensor initialized at 0x%02X.\n", address);
    else {
      Serial.printf("[ERROR] Device at 0x%02X ACKs, but register setup failed.\n", address);
      active[i] = false;
    }
  }
  if (count == 0) {
    Serial.println("[RESULT] No device at 0x68 or 0x69.");
    Serial.println("[CHECK] Sensor power, common GND, SDA->GPIO21, SCL->GPIO22.");
  } else if (count == 1) {
    Serial.println("[RESULT] Found one I2C device, as expected for one IMU per ESP32.");
  } else {
    Serial.println("[RESULT] Both addresses responded; two devices are still on this I2C bus.");
  }
}

void printMotion(uint8_t address) {
  uint8_t raw[14];
  if (!readRegisters(address, 0x3B, raw, sizeof(raw))) {
    Serial.printf("[READ] 0x%02X failed.\n", address);
    return;
  }
  auto value = [&](int index) -> int16_t {
    return (int16_t)((uint16_t(raw[index]) << 8) | raw[index + 1]);
  };
  float ax = value(0) / 16384.0f;
  float ay = value(2) / 16384.0f;
  float az = value(4) / 16384.0f;
  float gx = value(8) / 131.0f;
  float gy = value(10) / 131.0f;
  float gz = value(12) / 131.0f;
  Serial.printf("[DATA 0x%02X] A=%.3f,%.3f,%.3f g  G=%.2f,%.2f,%.2f deg/s\n",
                address, ax, ay, az, gx, gy, gz);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);
  Wire.setTimeOut(50);
  Serial.println("=== ESP2 / IMU2 I2C TEST ===");
  Serial.println("[ROLE] ESP2 DOIT ESP32 DEVKIT V1 is testing IMU2.");
  scanSensors();
}

void loop() {
  bool any = false;
  for (int i = 0; i < 2; ++i) {
    if (!active[i]) continue;
    any = true;
    printMotion(ADDRESSES[i]);
  }
  if (!any) scanSensors();
  delay(1000);
}
