'use strict';
let cameraResult = 0, sensorResult = 0, currentStep = 1, stopTelemetry = null;
const $ = id => document.getElementById(id);
const post = (url, options = {}) => Rehab.api(url, {method: 'POST', ...options});
const degrees = value => Number.isFinite(Number(value)) ? Number(value).toFixed(1).replace(/\.0$/, '') + '°' : '—';

function showStep(n) {
    currentStep = n;
    if (stopTelemetry) stopTelemetry();
    for (let i = 1; i <= 3; i++) {
        $('step' + i).style.display = i === n ? 'block' : 'none';
        $('ind' + i).className = 'step ' + (i < n ? 'done' : i === n ? 'active' : '');
        $('ind' + i).setAttribute('aria-current', i === n ? 'step' : 'false');
    }
    const video = $('cameraFeed');
    if (n === 1 && !document.hidden) video.src = '/video_feed';
    else video.removeAttribute('src');
    $('calibBanner').style.display = 'none';
    if (n < 3) stopTelemetry = Rehab.poll(async signal => {
        if (n === 1) {
            const [angle, max] = await Promise.all([Rehab.api('/get_angle', {signal}), Rehab.api('/get_max_rom', {signal})]);
            if (signal.aborted) return;
            $('liveAngle').textContent = angle.tracking === false ? 'Chưa thấy chân' : degrees(angle.angle);
            $('maxRomSession').textContent = degrees(max.max_rom);
            $('cameraStatus').textContent = angle.tracking === false ? 'Đưa chân vào khung hình và kiểm tra kết nối camera.' : 'Camera đang theo dõi chuyển động.';
        } else {
            const [angle, max, status] = await Promise.all([
                Rehab.api('/get_imu_angle', {signal}), Rehab.api('/get_imu_max_rom', {signal}), Rehab.api('/imu_status', {signal})
            ]);
            if (signal.aborted) return;
            $('liveImuAngle').textContent = status.receiving ? degrees(angle.angle) : '—';
            $('maxImuSession').textContent = degrees(max.max_rom);
            $('imuStatus').textContent = status.receiving ? '● đang nhận dữ liệu' : status.connected ? '● đã kết nối, đang chờ dữ liệu' : '● mất kết nối';
            $('imuStatus').style.background = status.receiving ? '#166534' : '#7f1d1d';
            $('calibBanner').style.display = status.calibrating ? 'block' : 'none';
            const stability = $('stability');
            stability.textContent = !status.receiving ? 'Chưa nhận được tín hiệu cảm biến' : status.jitter > 5
                ? 'Cảm biến đang rung (' + degrees(status.jitter) + '). Giữ yên chân trước khi hiệu chỉnh.'
                : 'Tín hiệu ổn định (' + degrees(status.jitter) + ').';
            stability.style.color = status.receiving && status.jitter <= 5 ? '#166534' : '#B45309';
        }
    }, 300, error => {
        if (n === 1) { $('liveAngle').textContent = '—'; $('cameraStatus').textContent = error.message; }
        else { $('liveImuAngle').textContent = '—'; $('imuStatus').textContent = '● mất kết nối'; $('imuStatus').style.background = '#7f1d1d'; }
        Rehab.notify(error.message);
    });
}

function finishCamera(button) {
    Rehab.action(button, async () => {
        const data = await post('/save_rom/camera');
        cameraResult = data.saved_rom;
        insertRowToTable(data.saved_rom, data.time, 'camera', data.id);
        showStep(2);
        await post('/reset_rom/imu');
    });
}
function finishSensor(button) {
    Rehab.action(button, async () => {
        const data = await post('/save_rom/imu');
        sensorResult = data.saved_rom;
        insertRowToTable(data.saved_rom, data.time, 'imu', data.id);
        const diff = Math.abs(cameraResult - sensorResult);
        $('resCam').textContent = degrees(cameraResult);
        $('resImu').textContent = degrees(sensorResult);
        $('resDiff').textContent = degrees(diff);
        $('resVerdict').textContent = diff <= 5 ? 'Chênh lệch giữa hai lượt đo không quá 5°.'
            : diff <= 10 ? 'Chênh lệch giữa hai lượt đo từ 5° đến 10°.'
            : 'Chênh lệch lớn hơn 10°. Kiểm tra vị trí cảm biến và góc camera trước lượt đo tiếp theo.';
        showStep(3);
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
    Rehab.action(button, async () => { await post('/reset_rom/camera'); cameraResult = sensorResult = 0; showStep(1); });
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
        ['serial', 'wifi'].forEach(value => {
            $('modeBtn-' + value).classList.toggle('legbtn-active', mode === value);
            $('modeBtn-' + value).setAttribute('aria-pressed', String(mode === value));
        });
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
    const video = $('cameraFeed');
    if (document.hidden) video.removeAttribute('src');
    else if (currentStep === 1) video.src = '/video_feed';
});
window.addEventListener('pagehide', () => $('cameraFeed').removeAttribute('src'));
window.addEventListener('DOMContentLoaded', () => {
    $('cameraFeed').addEventListener('error', () => { $('cameraStatus').textContent = 'Không nhận được hình ảnh. Kiểm tra camera rồi tải lại trang.'; });
    showStep(1);
    // Opening a tab must not erase an unfinished measurement; reset only on explicit restart.
});
