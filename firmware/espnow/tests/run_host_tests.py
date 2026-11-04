"""Run the actual ESP1 acquisition/fusion code on a host with fake I2C/radio."""
from pathlib import Path
import os, shutil, subprocess
root=Path(__file__).resolve().parents[3]
base=root/'firmware/espnow'
work=root/'.test-artifacts/espnow-host'
work.mkdir(parents=True, exist_ok=True)
for name in ('esp1_usb_bridge.ino','SplitAcquisition.h','ImuOne.h','KneeOutputFilter.h'):
    source='esp1_wireless_hub.ino' if name=='esp1_usb_bridge.ino' else name
    shutil.copyfile(base/'esp1_wireless_hub'/source,work/name)
(work/'Arduino.h').write_text(r'''
#pragma once
#include <cstdint>
#include <cstring>
#include <cmath>
#include <cstdio>
#include <algorithm>
#include <string>
using std::isfinite;
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
inline uint32_t fakeUs=100000;
inline uint32_t micros(){return fakeUs;}
inline uint32_t millis(){return fakeUs/1000;}
inline void delay(uint32_t n){fakeUs+=n*1000;}
struct SerialFake {
 std::string output,input;
 void begin(int){}
 template<class... T> void printf(const char* f,T... args){char b[512];std::snprintf(b,sizeof(b),f,args...);output+=b;}
 void print(const char* s){output+=s;}
 void println(const char* s){output+=s;output+='\n';}
 int available(){return (int)input.size();}
 int read(){if(input.empty())return -1;int c=input[0];input.erase(0,1);return c;}
};
inline SerialFake Serial;
''')
(work/'Wire.h').write_text(r'''
#pragma once
#include "Arduino.h"
inline bool failReads=false;
inline uint8_t localRaw[14]={0,0,0,0,0x40,0};
struct WireFake {
 int count=0,index=0;
 void begin(int,int){} void setClock(int){} void setTimeOut(int){}
 void beginTransmission(uint8_t){} void write(uint8_t){}
 int endTransmission(bool=true){return 0;}
 int requestFrom(int,int n,int){count=n;index=0;if(n==14){fakeUs+=1000;if(failReads)return 0;}return n;}
 int read(){return count==1?0x68:localRaw[index++];}
};
inline WireFake Wire;
''')
(work/'PcWifiLink.h').write_text(r'''
#pragma once
#include "Arduino.h"
namespace PcWifiLink {
inline bool begin(uint8_t){return true;}
inline void print(const char* text){Serial.print(text);}
inline char readCommand(){
 if(!Serial.available())return 0;
 char command=static_cast<char>(Serial.read());
 if(command>='a'&&command<='z')command-=('a'-'A');
 return command=='C'||command=='F'||command=='L'||command=='R'?command:0;
}
inline void clearCommands(){while(Serial.available())Serial.read();}
}
''')
h=(base/'esp1_wireless_hub/KneeRadio.h').read_text()
types=h[h.index('enum Kind'):h.index('struct Outgoing')]
(work/'KneeRadio.h').write_text(r'''
#pragma once
#include "Arduino.h"
#include <vector>
namespace KneeRadio {
inline constexpr uint8_t CHANNEL=1;
'''+types+r'''
inline uint32_t bootId=111;
inline bool txFault=false;
inline uint32_t remote=222;
inline int mode=0;
inline bool rotating=false;
struct Event{uint32_t at;Packet p;};
inline std::vector<Event> events;
inline void enqueue(Packet p,uint32_t delayUs=0){events.push_back({fakeUs+delayUs,p});}
inline bool begin(bool){return true;}
inline void fatal(){std::abort();}
inline bool receive(Received& rx) {
 for(size_t i=0;i<events.size();i++) if(int32_t(fakeUs-events[i].at)>=0) {
   rx.packet=events[i].p;rx.receivedAt=millis();
   events.erase(events.begin()+i);return true;
 }
 return false;
}
inline void hello(){
 Packet p;p.kind=HELLO;p.session=remote;enqueue(p);
}
inline bool send(const Packet& req,bool=false) {
 if(mode==1)return true; // dropped packet
 if(mode==3){remote++;hello();return true;} // ESP2 reboot during request
 hello();
 Packet p;p.kind=SAMPLE;p.session=remote;p.target=req.session;
 p.sequence=req.sequence;p.sampleUs=fakeUs+500;p.raw[4]=0x40;
 if(rotating){int16_t z=11790;p.raw[12]=(z>>8)&255;p.raw[13]=z&255;}
 if(mode==4)p.status=1;
 if(mode==2){p.sequence--;enqueue(p,200);p.sequence++;p.target++;enqueue(p,400);p.target--;}
 enqueue(p,1000);
 if(mode==2)enqueue(p,1500); // duplicate must not become another integration step
 return true;
}
}
''')
(work/'test.cpp').write_text(r'''
#include <cassert>
#include <cstdlib>
#include "esp1_usb_bridge.ino"

void reset() {
 fakeUs=100000;KneeRadio::events.clear();KneeRadio::mode=0;
 failReads=false;memset(localRaw,0,14);localRaw[4]=0x40;
 Serial.output.clear();Serial.input.clear();logOn=false;resetOutput();
 KneeRadio::remote=222;KneeRadio::rotating=false;KneeRadio::txFault=false;
 acquisition=SplitAcquisition{};acquisition.begin();KneeRadio::hello();
 acquisition.service();
 biasReady=calibrated=axisCalibrated=false;
 for(int k=0;k<3;k++)gbTh[k]=gbSh[k]=0;
}
int main() {
 reset();
 ImuPair p;
 assert(acquisition.take(p));
 assert(p.epoch==acquisition.epoch && p.roundTripUs<=20000);
 auto count=acquisition.accepted;
 KneeRadio::mode=2;
 assert(acquisition.take(p)); // wrong sequence and target ignored, valid reply selected
 assert(acquisition.accepted==count+1);
 assert(acquisition.take(p)); // queued duplicate cannot satisfy next request
 assert(acquisition.accepted==count+2);
 KneeRadio::mode=1;
 auto t=micros();
 assert(!acquisition.take(p)); // missing sample: bounded timeout, no reuse
 assert(uint32_t(micros()-t)<35000);
 assert(acquisition.accepted==count+2);
 KneeRadio::mode=4;
 assert(!acquisition.take(p)); // remote I2C failure
 KneeRadio::mode=3;
 auto epoch=acquisition.epoch;
 assert(!acquisition.take(p));
 assert(acquisition.epoch!=epoch); // boot change detected mid-request
 reset();
 float q[4]={1,0,0,0};
 for(int i=0;i<1000;i++)madgwick(q,0,0,0,0,0,1,0.01f);
 for(float f:q)assert(std::isfinite(f));
 assert(fabsf(q[0]-1)<1e-6f); // zero gradient does not create NaN
 assert(initializePair()); // real calibration code: 300 unique pairs + 60 gravity pairs
 assert(acquisition.accepted==360);
 assert(biasReady);
 KneeRadio::rotating=true;
 float rel[4];
 for(int i=0;i<100;i++)assert(stepFusion(rel));
 float angle=2*acosf(fminf(1.0f,fabsf(rel[0])))*180/M_PI;
 assert(angle>88 && angle<92); // 90 deg/s shank vs stationary thigh, one second
 KneeRadio::mode=3;
 assert(!stepFusion(rel));
 // The main loop must invalidate after a boot change; old qRel is not reused.
 assert(stale());
 reset();KneeRadio::rotating=true;
 assert(!initializePair()); // movement rejected instead of saved as gyro bias

 // A single I2C error must not start a 2-second probe/recalibration cascade.
 reset();
 assert(acquisition.take(p));
 failReads=true;
 assert(!acquisition.take(p));assert(acquisition.localReady);
 failReads=false;
 assert(acquisition.take(p));assert(acquisition.localReady);
 // Only three consecutive bus failures request sensor recovery.
 failReads=true;
 assert(!acquisition.take(p));assert(acquisition.localReady);
 assert(!acquisition.take(p));assert(acquisition.localReady);
 assert(!acquisition.take(p));assert(!acquisition.localReady);
 // An acceleration outlier is dropped, but is not a broken I2C bus.
 reset();memset(localRaw,0,14);
 assert(!acquisition.take(p));assert(acquisition.localReady);
 localRaw[4]=0x40;assert(acquisition.take(p));

 // No angle output after a long gap. L must still be processed while offline.
 reset();assert(initializePair());calibrated=true;
 KneeRadio::mode=1;fakeUs+=150000;
 Serial.input="L";Serial.output.clear();loop();
 assert(logOn && !biasReady && !calibrated);
 assert(Serial.output.find("Knee:")==std::string::npos);
 assert(Serial.output.find("[ERROR]")!=std::string::npos);

 // Brief loss: skip output during loss; retain bias when the next pair is timely.
 reset();assert(initializePair());calibrated=true;
 KneeRadio::mode=1;Serial.output.clear();loop();assert(biasReady);
 KneeRadio::mode=0;loop();assert(biasReady && calibrated);

 // Median still rejects two consecutive isolated spikes.
 KneeOutputFilter f;
 for(uint32_t i=1;i<=20;i++)assert(f.update(180,i*10000));
 assert(f.update(80,210000));assert(f.update(80,220000));
 assert(f.update(180,230000));assert(fabsf(f.value()-180)<0.001f);
 float before=f.value();
 assert(!f.update(NAN,240000));assert(f.value()==before);


 // Clock wrap must not invalidate an otherwise fresh output sample.
 f.reset();assert(f.update(180,0xfffff000u));assert(f.update(180,uint32_t(0xfffff000u+10000u)));

 // F pauses output for 6s: resuming must not treat that as stale filter timing.
 reset();assert(initializePair());calibrated=true;Serial.input="F";loop();
 assert(biasReady && calibrated);loop();assert(biasReady && calibrated);

 // Healthy data is published at the requested 40Hz cadence.
 Serial.output.clear();
 for(int i=0;i<100;i++)loop();
 size_t pos=0;int published=0;
 while((pos=Serial.output.find("Knee:",pos))!=std::string::npos){published++;pos+=5;}
 assert(published>=38 && published<=42);
 // Compare lag against the former median5 + EMA .25 running only at 20Hz.
 f.reset();float oldWin[5]={180,180,180,180,180},oldLimited=180,oldValue=180;
 for(uint32_t i=1;i<=100;i++) {
   float input=180-0.9f*i;assert(f.update(input,i*10000));
   if(i%5==0) {
     for(int j=0;j<4;j++)oldWin[j]=oldWin[j+1];oldWin[4]=input;
     float a[5];memcpy(a,oldWin,sizeof(a));std::sort(a,a+5);
     oldLimited+=fmaxf(-20.0f,fminf(20.0f,a[2]-oldLimited));
     oldValue+=0.25f*(oldLimited-oldValue);
   }
 }
 float newLag=fabsf(f.value()-90),oldLag=fabsf(oldValue-90);
 assert(newLag<8 && newLag<oldLag*0.5f);
 // Standing noise remains attenuated.
 f.reset();float sum=0;
 for(uint32_t i=1;i<=200;i++) {
   float noise=(i%7-3.0f)*0.15f;assert(f.update(180+noise,i*10000));
   if(i>100)sum+=(f.value()-180)*(f.value()-180);
 }
 assert(sqrtf(sum/100)<0.15f);
 // Same ramp with variable sample intervals, no fixed-dt dependency.
 f.reset();uint32_t stamp=0;
 for(int i=0;i<100;i++){stamp+=(i%2?12000:8000);assert(f.update(180-90*stamp/1e6f,stamp));}
 assert(fabsf(f.value()-90)<8);
 std::printf("Ramp lag at 90 deg/s: old=%.2f deg, new=%.2f deg. Standing RMS noise=%.3f deg.\n",oldLag,newLag,sqrtf(sum/100));
 puts("PASS: protocol, reboot, timeout, I2C recovery threshold, dropout/output, filtering, jitter, bias and rotation.");

}
''')
compiler=shutil.which('g++') or r'C:\msys64\ucrt64\bin\g++.exe'
env=os.environ.copy()
env['PATH']=str(Path(compiler).parent)+os.pathsep+env.get('PATH','')
subprocess.run([compiler,'-std=c++17','-O2','-I',str(work),str(work/'test.cpp'),'-o',str(work/'tests.exe')],check=True,env=env)
subprocess.run([str(work/'tests.exe')],check=True,env=env)
