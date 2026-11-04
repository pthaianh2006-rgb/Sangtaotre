/**
 * Knee Angle - Tang 2: Madgwick + quaternion tuong doi + functional axis (swing-twist)
 * Boot: giu YEN 2s (gyro bias).
 * 'C' = zero luc DUOI THANG (moc 180)
 * 'F' = functional calib: gap-duoi cham 6s de tim truc gap
 * 'L' = bat/tat log CSV (millis,knee)   'R' = reset
 */
#include "Wire.h"
#include <WiFi.h>

// ===== KET NOI WIFI (Access Point - ESP32 tu phat wifi, may tinh noi vao) =====
static const char*    AP_SSID = "ACL_Knee";    // ten wifi ESP32 phat ra
static const char*    AP_PASS = "12345678";    // mat khau (toi thieu 8 ky tu)
static const uint16_t TCP_PORT = 8080;         // cong TCP truyen du lieu
WiFiServer tcpServer(TCP_PORT);
WiFiClient tcpClient;

static constexpr uint8_t SDA_PIN=21, SCL_PIN=22;
static constexpr uint8_t ADDR_THIGH=0x68, ADDR_SHANK=0x69;
static constexpr float   BETA=0.10f;        // tang luc keo cua accel -> giam drift
static constexpr uint32_t PRINT_MS=50;
static constexpr float   AXIS_SIGN=-1.0f;   // gap xuong duoi -> goc gap TANG (doi thanh 1.0f neu bi nguoc)
static constexpr float   SMOOTH=0.25f;     // loc lam muot output (nho hon = muot hon nhung tre hon)

float qTh[4]={1,0,0,0}, qSh[4]={1,0,0,0};
float gbTh[3]={0,0,0}, gbSh[3]={0,0,0};
float qRel0[4]={1,0,0,0}, kneeAxis[3]={0,0,1};
bool  calibrated=false, axisCalibrated=false, logOn=false;
uint32_t lastUs=0, lastPrintMs=0;

void broadcast(const char*m){ Serial.print(m); if(tcpClient && tcpClient.connected()) tcpClient.print(m); }
char readCmd(){ if(Serial.available())return Serial.read(); if(tcpClient && tcpClient.connected() && tcpClient.available())return tcpClient.read(); return 0; }
void writeReg(uint8_t a,uint8_t r,uint8_t v){ Wire.beginTransmission(a);Wire.write(r);Wire.write(v);Wire.endTransmission(); }
void mpuInit(uint8_t a){ writeReg(a,0x6B,0x80);delay(100); writeReg(a,0x6B,0x01);delay(10);
  writeReg(a,0x1A,0x04); writeReg(a,0x1B,0x00); writeReg(a,0x1C,0x00); delay(10); }
  // DLPF 0x04 = 21Hz: loc nhieu cao tan ngay tai cam bien (cu dong dau goi <5Hz nen du nhanh)

bool readImu(uint8_t a, float*ac, float*gy, const float*gb){
  Wire.beginTransmission(a); Wire.write(0x3B);
  if(Wire.endTransmission(false)!=0) return false;
  if(Wire.requestFrom((int)a,14,1)!=14) return false;
  uint8_t b[14]; for(int i=0;i<14;i++) b[i]=Wire.read();
  ac[0]=(int16_t)((b[0]<<8)|b[1])/16384.0f; ac[1]=(int16_t)((b[2]<<8)|b[3])/16384.0f; ac[2]=(int16_t)((b[4]<<8)|b[5])/16384.0f;
  const float K=(1.0f/131.0f)*(M_PI/180.0f);
  gy[0]=(int16_t)((b[8]<<8)|b[9])*K-gb[0]; gy[1]=(int16_t)((b[10]<<8)|b[11])*K-gb[1]; gy[2]=(int16_t)((b[12]<<8)|b[13])*K-gb[2];
  float am=sqrtf(ac[0]*ac[0]+ac[1]*ac[1]+ac[2]*ac[2]);
  if(am<0.2f || am>4.0f) return false;   // khung doc bi loi (nhieu I2C) -> bo, khong cho vao bo loc
  return true;
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
    n=1.0f/sqrtf(s0*s0+s1*s1+s2*s2+s3*s3); s0*=n;s1*=n;s2*=n;s3*=n;
    qd1-=BETA*s0;qd2-=BETA*s1;qd3-=BETA*s2;qd4-=BETA*s3;
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
void stationaryUpdate(float* gb, float* g, const float* a){
  float gmag=sqrtf(g[0]*g[0]+g[1]*g[1]+g[2]*g[2]);          // toc do xoay con lai (rad/s)
  float amag=sqrtf(a[0]*a[0]+a[1]*a[1]+a[2]*a[2]);          // do lon gia toc (g)
  if(gmag<0.06f && fabsf(amag-1.0f)<0.08f){                 // dung yen + chi co trong luc
    for(int k=0;k<3;k++){ gb[k]+=0.02f*g[k]; g[k]=0.0f; }   // tinh chinh bias + khoa gyro
  }
}
bool stepFusion(float* qRel){
  float aTh[3],gTh[3],aSh[3],gSh[3];
  bool ok=readImu(ADDR_THIGH,aTh,gTh,gbTh)&&readImu(ADDR_SHANK,aSh,gSh,gbSh);
  uint32_t us=micros(); float dt=(us-lastUs)/1e6f; lastUs=us; if(dt<=0||dt>0.2f) dt=0.005f;
  if(ok){ stationaryUpdate(gbTh,gTh,aTh); stationaryUpdate(gbSh,gSh,aSh);
          madgwick(qTh,gTh[0],gTh[1],gTh[2],aTh[0],aTh[1],aTh[2],dt);
          madgwick(qSh,gSh[0],gSh[1],gSh[2],aSh[0],aSh[1],aSh[2],dt); }
  float qThc[4]; qconj(qTh,qThc); qmul(qThc,qSh,qRel); return ok;
}
// Lay bias 1 lan, tra ve false neu phat hien RUNG/DONG (de thu lai)
bool calibGyroBiasOnce(){
  float sTh[3]={0,0,0},sSh[3]={0,0,0}; const float z[3]={0,0,0}; int N=300;
  float mx=0;                                                  // bien do xoay lon nhat trong luc do
  for(int i=0;i<N;i++){ float a[3],g[3];
    if(readImu(ADDR_THIGH,a,g,z)) for(int k=0;k<3;k++){ sTh[k]+=g[k]; if(fabsf(g[k])>mx) mx=fabsf(g[k]); }
    if(readImu(ADDR_SHANK,a,g,z)) for(int k=0;k<3;k++){ sSh[k]+=g[k]; if(fabsf(g[k])>mx) mx=fabsf(g[k]); }
    delay(5); }
  if(mx>0.15f) return false;                                   // >~8.6 do/s => dang dong => bo
  for(int k=0;k<3;k++){ gbTh[k]=sTh[k]/N; gbSh[k]=sSh[k]/N; }
  return true;
}
void calibGyroBias(){
  for(int t=0;t<6;t++){
    broadcast("[INFO] Giu YEN khong dong...\n");
    if(calibGyroBiasOnce()){ broadcast("[INFO] bias OK\n"); return; }
    broadcast("[WARN] Co rung - giu yen hon, dang thu lai...\n"); delay(300);
  }
  broadcast("[WARN] Bias chua chuan - nen tat bat lai va giu that yen\n");
}
// Khoi tao quaternion tu gia toc ke -> bo loc bat dau da huong dung -> het troi luc boot
void initQuat(uint8_t addr, float* q, const float* gb){
  float ax=0,ay=0,az=0; int n=0; float a[3],g[3];
  for(int i=0;i<60;i++){ if(readImu(addr,a,g,gb)){ ax+=a[0]; ay+=a[1]; az+=a[2]; n++; } delay(4); }
  if(n==0){ q[0]=1;q[1]=q[2]=q[3]=0; return; }
  ax/=n; ay/=n; az/=n;
  float nrm=sqrtf(ax*ax+ay*ay+az*az); if(nrm<1e-6f){ q[0]=1;q[1]=q[2]=q[3]=0; return; }
  ax/=nrm; ay/=nrm; az/=nrm;
  float roll=atan2f(ay,az);
  float pitch=atan2f(-ax, sqrtf(ay*ay+az*az));
  float cr=cosf(roll*0.5f), sr=sinf(roll*0.5f);
  float cp=cosf(pitch*0.5f), sp=sinf(pitch*0.5f);
  q[0]=cr*cp; q[1]=sr*cp; q[2]=cr*sp; q[3]=-sr*sp;
}
void functionalCalib(){
  broadcast("[F] Tu DUOI THANG: tu tu GAP xuong roi DUOI ra (6s)...\n");
  float qRefF[4]; stepFusion(qRefF); float qRefc[4]; qconj(qRefF,qRefc);
  float axisSum[3]={0,0,0}; uint32_t t0=millis();
  while(millis()-t0<6000){
    float qRel[4]; stepFusion(qRel); float dq[4]; qmul(qRefc,qRel,dq);
    float qv[3]={dq[1],dq[2],dq[3]}; float mag=sqrtf(qv[0]*qv[0]+qv[1]*qv[1]+qv[2]*qv[2]);
    if(mag>0.02f){ float dot=qv[0]*axisSum[0]+qv[1]*axisSum[1]+qv[2]*axisSum[2]; float s=(dot<0)?-1.0f:1.0f;
      for(int k=0;k<3;k++) axisSum[k]+=s*qv[k]; }
    delay(3);
  }
  float n=sqrtf(axisSum[0]*axisSum[0]+axisSum[1]*axisSum[1]+axisSum[2]*axisSum[2]);
  if(n<0.05f){ broadcast("[F] That bai - gap manh hon roi thu lai\n"); return; }
  for(int k=0;k<3;k++) kneeAxis[k]=axisSum[k]/n; axisCalibrated=true;
  char b[64]; snprintf(b,sizeof(b),"[F] truc gap=%.2f,%.2f,%.2f OK\n",kneeAxis[0],kneeAxis[1],kneeAxis[2]); broadcast(b);
}

void setup(){
  Serial.begin(115200); Wire.begin(SDA_PIN,SCL_PIN); Wire.setClock(100000); // 100kHz: on dinh hon cho day dai
  WiFi.mode(WIFI_AP); WiFi.softAP(AP_SSID, AP_PASS); delay(200);
  WiFi.setTxPower(WIFI_POWER_11dBm);   // ha cong suat phat -> bot dong tieu thu khi chay pin
  tcpServer.begin(); tcpServer.setNoDelay(true);
  Serial.print("[WIFI] AP="); Serial.print(AP_SSID);
  Serial.print("  IP="); Serial.print(WiFi.softAPIP());      // luon la 192.168.4.1
  Serial.print("  PORT="); Serial.println(TCP_PORT);
  mpuInit(ADDR_THIGH); mpuInit(ADDR_SHANK); delay(50);
  calibGyroBias();
  initQuat(ADDR_THIGH, qTh, gbTh); initQuat(ADDR_SHANK, qSh, gbSh);
  broadcast("[INFO] San sang - bo loc da huong theo trong luc.\n");
  broadcast("[INFO] B1: DUOI THANG bam 'C'. B2: bam 'F' roi GAP-DUOI 6s. B3: tap.\n");
  lastUs=micros(); lastPrintMs=millis();
}

void loop(){
  if(tcpServer.hasClient()){                      // co may tinh ket noi toi
    if(tcpClient && tcpClient.connected()) tcpClient.stop();
    tcpClient = tcpServer.available();
  }
  float qRel[4]; stepFusion(qRel);

  char cmd=readCmd();
  if(cmd=='c'||cmd=='C'){ for(int k=0;k<4;k++) qRel0[k]=qRel[k]; calibrated=true; broadcast("[CALIB] 180deg set!\n"); }
  if(cmd=='f'||cmd=='F'){ functionalCalib(); }
  if(cmd=='l'||cmd=='L'){ logOn=!logOn; broadcast(logOn?"[LOG] on (millis,knee)\n":"[LOG] off\n"); }
  if(cmd=='r'||cmd=='R'){ calibrated=false; axisCalibrated=false; broadcast("[RESET]\n"); }

  float knee=180.0f;
  if(calibrated){
    float qR0c[4],dq[4]; qconj(qRel0,qR0c); qmul(qR0c,qRel,dq);
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

  if(millis()-lastPrintMs>=PRINT_MS){
    lastPrintMs=millis(); char buf[48];
    // (1) median-of-5: loai gai nhon ke ca khi 2 mau loi lien tiep
    static float win[5]={180,180,180,180,180};
    for(int i=0;i<4;i++) win[i]=win[i+1]; win[4]=knee;
    float s[5]; for(int i=0;i<5;i++) s[i]=win[i];
    for(int i=0;i<5;i++) for(int j=i+1;j<5;j++) if(s[j]<s[i]){ float t=s[i]; s[i]=s[j]; s[j]=t; }
    float med=s[2];
    // (2) gioi han toc do thay doi: chan cu nhay phi ly (>~400 do/s = nhieu)
    static float prevK=180.0f; const float MAXSTEP=20.0f;
    if(med-prevK>MAXSTEP) med=prevK+MAXSTEP; else if(prevK-med>MAXSTEP) med=prevK-MAXSTEP;
    prevK=med;
    // (3) EMA low-pass: lam muot phan con lai
    static float kneeF=180.0f;
    kneeF += SMOOTH*(med-kneeF);
    float kOut = roundf(kneeF*10.0f)/10.0f;
    if(logOn)            snprintf(buf,sizeof(buf),"%lu,%.1f\n",(unsigned long)millis(),kOut);
    else if(calibrated)  snprintf(buf,sizeof(buf),"Knee:%.1f %s\n",kOut,axisCalibrated?"[axis]":"[no-axis]");
    else                 snprintf(buf,sizeof(buf),"... send C\n");
    broadcast(buf);
  }
}