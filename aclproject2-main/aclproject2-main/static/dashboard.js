'use strict';
let cameraResult = 0, sensorResult = 0, currentStep = 1, stopTelemetry = null;
let measurementActive = false;
let saveInProgress = false;
let cameraRetryTimer = null, cameraRetryCount = 0;
const $ = id => document.getElementById(id);
const post = (url, options = {}) => Rehab.api(url, {method: 'POST', ...options});
const degrees = value => Number.isFinite(Number(value)) ? Number(value).toFixed(1).replace(/\.0$/, '') + '°' : '—';

function disconnectCameraFeed() {
    clearTimeout(cameraRetryTimer);
    cameraRetryTimer = null;
    $('cameraFeed').removeAttribute('src');
}
function connectCameraFeed() {
    const video = $('cameraFeed');
    if (!measurementActive || currentStep !== 1 || document.hidden || cameraRetryTimer || video.getAttribute('src')) return;
    // A new URL creates a fresh streaming request after a failed/closed connection.
    video.src = '/video_feed?stream=' + Date.now();
}
function retryCameraFeed() {
    if (!measurementActive || currentStep !== 1 || document.hidden) return;
    disconnectCameraFeed();
    const delay = Math.min(8000, 1000 * 2 ** Math.min(cameraRetryCount++, 3));
    $('cameraStatus').textContent = 'Đang kết nối lại hình từ Camo… Giữ Camo Camera trên điện thoại đang mở.';
    cameraRetryTimer = setTimeout(() => { cameraRetryTimer = null; connectCameraFeed(); }, delay);
}

function showStep(n) {
    currentStep = n;
    if (stopTelemetry) { stopTelemetry(); stopTelemetry = null; }
    $('step1').style.display = n === 3 ? 'none' : 'block';
    $('step2').style.display = n === 3 ? 'none' : 'block';
    $('step3').style.display = n === 3 ? 'block' : 'none';
    $('finishCombined').style.display = n === 3 ? 'none' : 'block';
    disconnectCameraFeed();
    if (n !== 3 && measurementActive) stopTelemetry = Rehab.poll(async signal => {
        const data = await Rehab.api('/measurement/status', {signal});
        if (signal.aborted) return;
        if (data.active === false) {
            endLocalMeasurement();
            $('measurementStatus').textContent = data.message;
            return;
        }
        const camera = data.camera, imu = data.imu, status = data.imu;
        if (camera.stream_ready) connectCameraFeed();
        const ready = camera.tracking === true && camera.quality?.ready === true;
        $('liveAngle').textContent = ready ? degrees(camera.angle) : '—';
        $('maxRomSession').textContent = degrees(camera.max_rom);
        $('cameraQuality').textContent = camera.quality?.message || 'Đang chờ khớp chân rõ trong hình.';
        $('cameraQuality').dataset.ready = String(ready);
        const device = camera.camera;
        $('cameraStatus').textContent = camera.message || (device?.width ? `${device.name}: ${device.width} × ${device.height}` : 'Đang khởi động Camo…');
        $('liveImuAngle').textContent = status.receiving ? degrees(imu.angle) : '—';
        $('maxImuSession').textContent = degrees(imu.max_rom);
        $('imuStatus').textContent = status.receiving ? '● đang nhận dữ liệu' : status.connected ? '● đang chờ dữ liệu' : '● mất kết nối';
        $('imuStatus').style.background = status.receiving ? '#166534' : '#7f1d1d';
        $('calibBanner').style.display = status.calibrating ? 'block' : 'none';
        $('stability').textContent = !status.receiving ? 'Kiểm tra nguồn và kết nối cảm biến.' : status.jitter > 5 ? 'Cảm biến đang rung. Kiểm tra dây đeo.' : 'Tín hiệu cảm biến ổn định.';
        const canSave = !saveInProgress && ready && camera.snapshot_ready && status.receiving && !status.calibrating;
        $('finishCombined').disabled = !canSave;
        $('combinedHint').textContent = !ready ? 'Camera chưa đạt: ' + $('cameraQuality').textContent : !status.receiving ? 'Chờ tín hiệu IMU để lưu đủ hai kết quả.' : 'Hai nguồn đang đo song song. Bấm Hoàn tất khi kết thúc bài tập.';
    }, 300, error => {
        $('finishCombined').disabled = true;
        $('liveAngle').textContent = $('liveImuAngle').textContent = '—';
        $('combinedHint').textContent = error.message;
        Rehab.notify(error.message);
    });
}

function endLocalMeasurement() {
    measurementActive = false;
    $('finishCombined').disabled = true;
    if (stopTelemetry) { stopTelemetry(); stopTelemetry = null; }
    disconnectCameraFeed();
    cameraRetryCount = 0;
    $('startMeasurement').disabled = false;
    $('stopMeasurement').disabled = true;
}
async function releaseMeasurement() {
    endLocalMeasurement();
    await post('/measurement/stop');
    $('measurementStatus').textContent = 'Đã dừng đo và trả lại thiết bị.';
}
function startMeasurement(button) {
    Rehab.action(button, async () => {
        await post('/measurement/start', {headers:{'Content-Type':'application/json'}, body:JSON.stringify({combined:true})});
        $('captureResult').hidden = true;
        $('finishCombined').disabled = true;
        measurementActive = true;
        $('stopMeasurement').disabled = false;
        $('measurementStatus').textContent = 'Lượt đo đang hoạt động trên tài khoản của bạn.';
        showStep(currentStep === 3 ? 1 : currentStep);
    });
}
function stopMeasurement(button) {
    Rehab.action(button, releaseMeasurement);
}
function finishCombined(button) {
    Rehab.action(button, async () => {
        saveInProgress = true;
        try {
        const data = await post('/measurement/save_combined');
        cameraResult = data.camera_rom;
        sensorResult = data.imu_rom;
        insertRowToTable(cameraResult, data.time, 'camera', data.camera_id);
        insertRowToTable(sensorResult, data.time, 'imu', data.imu_id);
        $('resCam').textContent = degrees(cameraResult);
        $('resImu').textContent = degrees(sensorResult);
        $('resDiff').textContent = degrees(Math.abs(cameraResult-sensorResult));
        $('resVerdict').textContent = 'So sánh hai ROM lớn nhất trong cùng lượt tập.';
        $('captureImage').src = data.snapshot_url;
        $('captureCaption').textContent = 'Ảnh tại ROM camera lớn nhất: ' + degrees(cameraResult) + ' · ' + data.captured_at;
        $('captureResult').hidden = false;
        showStep(3);
        await releaseMeasurement();
        } finally { saveInProgress = false; }
    });
}
function renumberRows() {
    const body = $('realtime-log-table').tBodies[0];
    [...body.rows].filter(row => row.cells.length === 5).forEach((row, index) => row.cells[0].textContent = index + 1);
    if (!body.rows.length) {
        const row = body.insertRow(); row.id = 'no-data-row';
        const cell = row.insertCell(); cell.colSpan = 5; cell.className = 'empty-state'; cell.textContent = 'Chưa có lượt đo nào.';
    }
}
function deleteMeasurement(id, button) {
    if (!confirm('Xóa lượt đo này?')) return;
    Rehab.action(button, async () => { await post('/delete_measurement/' + encodeURIComponent(id)); button.closest('tr').remove(); renumberRows(); });
}
function deleteAll(button) {
    if (!confirm('Xóa TẤT CẢ lượt đo của bạn? Hành động này KHÔNG thể hoàn tác.')) return;
    Rehab.action(button, async () => { await post('/delete_all_me'); location.reload(); });
}
function restart(button) {
    cameraResult = sensorResult = 0;
    $('captureResult').hidden = true;
    showStep(1);
    startMeasurement(button);
}
function imuCmd(command, button) {
    Rehab.action(button, async () => { const data = await post('/imu_cmd/' + command); Rehab.notify(data.message || 'Đã gửi lệnh hiệu chỉnh.', 'info'); });
}
function toggleCrowd(button) {
    Rehab.action(button, async () => {
        const data = await post('/toggle_crowd');
        button.classList.toggle('legbtn-active', data.crowd);
        button.setAttribute('aria-pressed', String(data.crowd));
        button.textContent = data.crowd ? '👥 Đông người: ĐANG BẬT' : '👥 Chế độ đông người';
    });
}
function setLeg(leg, button) {
    Rehab.action(button, async () => {
        await post('/set_leg/' + leg);
        ['left', 'right', 'auto'].forEach(value => {
            $('legBtn-' + value).classList.toggle('legbtn-active', value === leg);
            $('legBtn-' + value).setAttribute('aria-pressed', String(value === leg));
        });
    });
}
async function loadPorts(button) {
    return Rehab.action(button, async () => {
        const data = await Rehab.api('/imu_ports');
        const select = $('portSelect');
        select.replaceChildren();
        for (const port of data.ports) select.add(new Option(port.device + ' — ' + port.desc, port.device, false, port.device === data.current));
        if (!data.ports.length) select.add(new Option('(Không thấy cổng nào)', ''));
    });
}
function connectPort(button) {
    if (!$('portSelect').value) { Rehab.notify('Chưa tìm thấy cổng cảm biến. Cắm thiết bị rồi làm mới danh sách.'); return; }
    Rehab.action(button, async () => { const data = await post('/imu_connect/' + encodeURIComponent($('portSelect').value)); Rehab.notify('Đang kết nối ' + data.port + '…', 'info'); });
}
function reconnectImu(button) {
    Rehab.action(button, async () => { await post('/imu_reconnect'); Rehab.notify('Đang kết nối lại cảm biến…', 'info'); });
}
function setImuMode(mode, button) {
    Rehab.action(button, async () => {
        await post('/set_imu_mode/' + mode);
        ['ble', 'serial', 'wifi'].forEach(value => {
            $('modeBtn-' + value).classList.toggle('legbtn-active', mode === value);
            $('modeBtn-' + value).setAttribute('aria-pressed', String(mode === value));
        });
        $('bleBox').style.display = mode === 'ble' ? 'flex' : 'none';
        $('usbBox').style.display = mode === 'serial' ? 'flex' : 'none';
        $('wifiBox').style.display = mode === 'wifi' ? 'flex' : 'none';
        if (mode === 'serial') await loadPorts();
    });
}
function insertRowToTable(angle, time, source, id) {
    $('no-data-row')?.remove();
    const row = $('realtime-log-table').tBodies[0].insertRow(0);
    ['', time, degrees(angle), source === 'imu' ? '🦵 Cảm biến' : '📷 Camera'].forEach(value => row.insertCell().textContent = value);
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = '🗑️'; button.title = 'Xóa lượt đo này'; button.setAttribute('aria-label', button.title);
    button.addEventListener('click', () => deleteMeasurement(id, button));
    row.insertCell().appendChild(button);
    renumberRows();
}
document.addEventListener('visibilitychange', () => {
    if (document.hidden) disconnectCameraFeed();
    // The next status poll reconnects once the camera is ready and this account owns it.
});
window.addEventListener('pagehide', () => {
    disconnectCameraFeed();
    if (!measurementActive) return;
    fetch('/measurement/stop', {method:'POST', credentials:'same-origin', keepalive:true,
        headers:{'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]')?.content || ''}}).catch(()=>{});
});
window.addEventListener('DOMContentLoaded', () => {
    $('cameraFeed').addEventListener('error', retryCameraFeed);
    $('cameraFeed').addEventListener('load', () => { cameraRetryCount = 0; });
    showStep(1);
    // Opening a tab must not erase an unfinished measurement; reset only on explicit restart.
});
