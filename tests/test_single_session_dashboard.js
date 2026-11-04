const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const base = process.env.SINGLE_SESSION_APP || __dirname + '/../aclproject2-main/aclproject2-main';
const source = fs.readFileSync(base + '/static/dashboard.js', 'utf8');
const html = fs.readFileSync(base + '/dashboard.html', 'utf8');
const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]));
const nodes = new Map(), listeners = {}, timers = new Map(), requests = [], notices = [];
let task, nextTimer = 0, saveFailure = false, stopFailure = false, pendingSave;
let state = {active:true, camera:{angle:40,max_rom:82,tracking:false,quality:{ready:false,message:'Chưa rõ'},snapshot_ready:false,stream_ready:false,camera:{}},imu:{angle:42,max_rom:85,receiving:false,calibrating:false}};
const draft = {can_save:true,draft_token:'private-draft',camera_rom:82,imu_rom:85,time:'2026-11-04 12:00:01',snapshot_url:'/measurement/draft/private-draft/image',captured_at:'2026-11-04 12:00:00'};
const body = {rows:[], insertRow(index) {const row={cells:[],insertCell(){const c={appendChild(){}};this.cells.push(c);return c;}};this.rows.splice(index,0,row);return row;}};
function node(id) {
  assert.ok(ids.has(id), 'JS references missing HTML element: '+id);
  if (!nodes.has(id)) nodes.set(id, {style:{},dataset:{},disabled:false,handlers:{},tBodies:[body],
    classList:{toggle(){}},setAttribute(k,v){this[k]=v;},getAttribute(k){return this[k]??null;},
    removeAttribute(k){delete this[k];},remove(){},appendChild(){},
    addEventListener(k,fn){this.handlers[k]=fn;}});
  return nodes.get(id);
}
const document = {hidden:false,getElementById:node,addEventListener:(name,fn)=>listeners[name]=fn,
  querySelector:()=>({content:'test'}),createElement:()=>({setAttribute(){},addEventListener(){}})};
const context = {console,Date,document,confirm:()=>true,window:{addEventListener:(name,fn)=>listeners[name]=fn},
  clearTimeout:id=>timers.delete(id),setTimeout:fn=>{const id=++nextTimer;timers.set(id,fn);return id;},
  Rehab:{api:async (url,options)=>{
      requests.push([url,options]);
      if (url==='/measurement/status') return state;
      if (url==='/measurement/stop') {if(stopFailure) throw new Error('stop failed'); return draft;}
      if (url==='/measurement/save_combined') {if(saveFailure) throw new Error('save failed'); return new Promise(resolve=>pendingSave=resolve);}
      return {success:true};
    },poll:fn=>{task=fn;return ()=>{};},notify:message=>notices.push(message),action:async(button,fn)=>{
      if(button?.disabled) return;
      if(button) button.disabled=true;
      try{return await fn();}catch(error){notices.push(error.message);}finally{if(button)button.disabled=false;}
    }},fetch:()=>Promise.resolve()};
vm.createContext(context);vm.runInContext(source,context);
const run = code => vm.runInContext(code, context);
(async()=>{
  listeners.DOMContentLoaded(); assert.equal(requests.length,0);
  assert.equal(node('finishCombined').disabled,true);
  await run('startMeasurement(document.getElementById("startMeasurement"))');
  assert.equal(JSON.parse(requests[0][1].body).combined,true);
  assert.equal(node('startMeasurement').disabled,true);
  await task({aborted:false});
  assert.equal(node('cameraPanel').style.display,'block');assert.equal(node('imuPanel').style.display,'block');
  state.camera.tracking=state.camera.quality.ready=state.camera.snapshot_ready=state.camera.stream_ready=true;
  state.imu.receiving=true;
  await task({aborted:false});
  assert.equal(node('liveAngle').textContent,'40°');assert.equal(node('liveImuAngle').textContent,'42°');
  assert.equal(node('finishCombined').disabled,true,'save is only offered after stopping both sources');
  const count = requests.length;
  await run('finishCombined(document.getElementById("finishCombined"))');
  assert.equal(requests.length,count,'cannot save while measuring');
  node('cameraFeed').handlers.error();assert.equal(timers.size,1);
  document.hidden=true;listeners.visibilitychange();assert.equal(timers.size,0);
  document.hidden=false;await task({aborted:false});
  stopFailure=true;
  await run('stopMeasurement(document.getElementById("stopMeasurement"))');
  assert.equal(node('stopMeasurement').disabled,false,'failed stop is retryable');
  assert.equal(node('startMeasurement').disabled,true);
  stopFailure=false;
  await run('stopMeasurement(document.getElementById("stopMeasurement"))');
  assert.equal(JSON.parse(requests.filter(([url])=>url==='/measurement/stop').at(-1)[1].body).preserve,true);
  assert.equal(node('stopMeasurement').disabled,true);
  assert.equal(node('finishCombined').disabled,false);
  assert.equal(node('cameraFeed').src,undefined);
  assert.equal(node('resCam').textContent,'82°');assert.equal(node('resImu').textContent,'85°');
  assert.equal(node('resultPanel').style.display,'block');
  assert.equal(node('captureImage').src,draft.snapshot_url);
  saveFailure=true;
  await run('finishCombined(document.getElementById("finishCombined"))');
  assert.equal(node('finishCombined').disabled,false,'failed save keeps immutable draft retryable');
  saveFailure=false;
  const save = run('finishCombined(document.getElementById("finishCombined"))');
  await Promise.resolve();await Promise.resolve();
  assert.equal(node('finishCombined').disabled,true);
  const saves = requests.filter(([url])=>url==='/measurement/save_combined').length;
  await run('finishCombined(document.getElementById("finishCombined"))');
  assert.equal(requests.filter(([url])=>url==='/measurement/save_combined').length,saves,'double click cannot duplicate save');
  pendingSave({...draft,camera_id:1,imu_id:2,snapshot_url:'/care/image/1'});
  await save;
  assert.equal(node('finishCombined').disabled,true,'successful save must remain disabled after action cleanup');
  assert.equal(node('startMeasurement').disabled,false);
  assert.equal(body.rows.length,2);
  assert.equal(body.rows[0].cells[1].textContent,body.rows[1].cells[1].textContent);
  assert.match(node('resultStatus').textContent,/Đã lưu/);
  assert.equal(node('captureImage').src,'/care/image/1');
  assert.equal(requests.filter(([url])=>url==='/measurement/start').length,1);
  assert.ok(!requests.some(([url])=>/\/save_rom\//.test(url)));
  await run("setImuMode('ble', document.getElementById('modeBtn-ble'))");
  await new Promise(setImmediate);
  assert.equal(node('bleBox').style.display,'flex');
  assert.deepEqual(notices, ['stop failed', 'save failed']);
  console.log('PASS: one start/poll, shared stop then save, frozen review, retry safety, duplicate guards, existing BLE controls');
})().catch(error=>{console.error(error);process.exitCode=1;});
