// ESP1: wireless PC hub + ONE thigh MPU6050; all knee fusion runs here.
// ESP2 supplies ONE shank MPU6050 via numbered ESP-NOW request/reply.
// Original fusion retained; output filtering is time-aware and adaptive.
#include "SplitAcquisition.h"
#include "KneeOutputFilter.h"
#include "PcWifiLink.h"
static constexpr float BETA=0.10f, AXIS_SIGN=-1.0f;
static constexpr uint32_t PRINT_MS=25; // Target 40 Hz; fusion about 100 Hz.
float qTh[4]={1,0,0,0}, qSh[4]={1,0,0,0};
float gbTh[3]={0,0,0},gbSh[3]={0,0,0};
float qRel0[4]={1,0,0,0},kneeAxis[3]={0,0,1};
KneeOutputFilter outputFilter;
bool calibrated=false,axisCalibrated=false,logOn=false,biasReady=false;
uint32_t lastThUs=0,lastShUs=0,lastPrintMs=0,biasEpoch=0;
SplitAcquisition acquisition;

void broadcast(const char* text) { PcWifiLink::print(text); }
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
  PcWifiLink::clearCommands(); // Never apply commands typed during bias later.
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
  if(!PcWifiLink::begin(KneeRadio::CHANNEL)) {
    Serial.println("[WARN] Wireless PC link unavailable; USB Serial fallback remains active.");
  }
  broadcast("[ROLE] ESP1 WIRELESS HUB + THIGH IMU. ESP2 holds SHANK IMU. Commands C/F/L/R.\n");
}

void loop() {
  acquisition.service();
  acquisition.report();
  char cmd=PcWifiLink::readCommand();
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
      char error[160];
      snprintf(error,sizeof(error),"[ERROR] %s; mat mau >100ms / doi phien. Dung xuat goc; can bias va C/F lai.\n",acquisition.lastFault);
      broadcast(error);
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
