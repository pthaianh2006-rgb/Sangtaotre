"use strict";
let cameraResult = 0,
  sensorResult = 0,
  stopTelemetry = null;
let measurementActive = false,
  measurementBusy = false,
  saveInProgress = false;
let draftToken = null,
  resultSaved = false,
  recordingGeneration = 0;
let cameraRetryTimer = null,
  cameraRetryCount = 0;
const $ = (id) => document.getElementById(id);
const post = (url, options = {}) =>
  Rehab.api(url, { method: "POST", ...options });
const jsonPost = (url, body) =>
  post(url, {
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
const degrees = (value) =>
  value !== null && value !== undefined && Number.isFinite(Number(value))
    ? Number(value).toFixed(1).replace(/\.0$/, "") + "°"
    : "—";

function updateMeasurementControls() {
  $("startMeasurement").disabled =
    measurementActive || measurementBusy || saveInProgress || !!draftToken;
  $("stopMeasurement").disabled =
    !measurementActive || measurementBusy || saveInProgress;
  $("finishCombined").disabled =
    measurementActive ||
    measurementBusy ||
    saveInProgress ||
    !draftToken ||
    resultSaved;
}
function showMeasurementView(results = false) {
  $("cameraPanel").style.display = results ? "none" : "block";
  $("imuPanel").style.display = results ? "none" : "block";
  $("resultPanel").style.display = results ? "block" : "none";
}
function disconnectCameraFeed() {
  clearTimeout(cameraRetryTimer);
  cameraRetryTimer = null;
  $("cameraFeed").removeAttribute("src");
}
function connectCameraFeed() {
  const video = $("cameraFeed");
  if (
    !measurementActive ||
    document.hidden ||
    cameraRetryTimer ||
    video.getAttribute("src")
  )
    return;
  video.src = "/video_feed?stream=" + Date.now();
}
function retryCameraFeed() {
  if (!measurementActive || document.hidden) return;
  disconnectCameraFeed();
  const delay = Math.min(8000, 1000 * 2 ** Math.min(cameraRetryCount++, 3));
  $("cameraStatus").textContent =
    "Đang kết nối lại hình từ Camo. Giữ Camo Camera trên điện thoại đang mở.";
  cameraRetryTimer = setTimeout(() => {
    cameraRetryTimer = null;
    connectCameraFeed();
  }, delay);
}
function endLocalMeasurement() {
  measurementActive = false;
  recordingGeneration++;
  if (stopTelemetry) {
    stopTelemetry();
    stopTelemetry = null;
  }
  disconnectCameraFeed();
  cameraRetryCount = 0;
  $("calibBanner").style.display = "none";
  updateMeasurementControls();
}
function beginTelemetry() {
  const generation = ++recordingGeneration;
  stopTelemetry = Rehab.poll(
    async (signal) => {
      const data = await Rehab.api("/measurement/status", { signal });
      if (
        signal.aborted ||
        !measurementActive ||
        generation !== recordingGeneration
      )
        return;
      if (data.active === false) {
        endLocalMeasurement();
        $("liveAngle").textContent = $("liveImuAngle").textContent = "—";
        $("measurementStatus").textContent = data.message;
        $("combinedHint").textContent =
          "Lượt đo đã kết thúc. Bấm Bắt đầu đo để đo lại cả hai nguồn.";
        return;
      }
      const camera = data.camera,
        imu = data.imu;
      if (camera.stream_ready) connectCameraFeed();
      const ready = camera.tracking === true && camera.quality?.ready === true;
      $("liveAngle").textContent = ready ? degrees(camera.angle) : "—";
      $("maxRomSession").textContent = degrees(camera.max_rom);
      $("cameraQuality").textContent =
        camera.message ||
        camera.quality?.message ||
        "Đang chờ khớp chân rõ trong hình.";
      $("cameraQuality").dataset.ready = String(ready);
      const device = camera.camera;
      $("cameraStatus").textContent =
        camera.message ||
        (device?.width
          ? `${device.name}: ${device.width} × ${device.height}`
          : "Đang khởi động Camo.");
      $("liveImuAngle").textContent = imu.receiving ? degrees(imu.angle) : "—";
      $("maxImuSession").textContent = degrees(imu.max_rom);
      $("imuStatus").textContent = imu.receiving
        ? "Đang nhận dữ liệu"
        : imu.connected
          ? "Đang chờ dữ liệu"
          : "Mất kết nối";
      $("imuStatus").dataset.state = imu.receiving ? "receiving" : "waiting";
      $("calibBanner").style.display = imu.calibrating ? "block" : "none";
      $("stability").textContent = !imu.receiving
        ? "Kiểm tra nguồn và kết nối cảm biến."
        : imu.jitter > 5
          ? "Cảm biến đang rung. Kiểm tra dây đeo."
          : "Tín hiệu cảm biến ổn định.";
      const bothReady =
        ready && camera.snapshot_ready && imu.receiving && !imu.calibrating;
      $("combinedHint").textContent = !ready
        ? "Camera chưa đạt: " + $("cameraQuality").textContent
        : !imu.receiving || imu.calibrating
          ? "Đang chờ cảm biến. Chưa thể lưu đủ hai nguồn."
          : "Hai nguồn đang đo cùng lúc. Bấm Dừng đo khi kết thúc bài tập.";
      $("measurementStatus").textContent = bothReady
        ? "Camera và cảm biến đang đo trong cùng một lượt."
        : "Đang chờ cả camera và cảm biến sẵn sàng.";
      updateMeasurementControls();
    },
    300,
    (error) => {
      if (!measurementActive || generation !== recordingGeneration) return;
      $("liveAngle").textContent = $("liveImuAngle").textContent = "—";
      $("combinedHint").textContent = error.message;
      updateMeasurementControls();
      Rehab.notify(error.message);
    },
  );
}
function showSessionResult(data, saved) {
  cameraResult = data.camera_rom;
  sensorResult = data.imu_rom;
  $("resCam").textContent = degrees(cameraResult);
  $("resImu").textContent = degrees(sensorResult);
  $("resDiff").textContent = degrees(Math.abs(cameraResult - sensorResult));
  $("resVerdict").textContent = "Hai ROM lớn nhất trong cùng lượt tập.";
  $("resultStatus").textContent = saved
    ? "Đã lưu camera, cảm biến và ảnh trong cùng lượt đo."
    : "Đã dừng cả hai nguồn. Kiểm tra rồi bấm Lưu kết quả.";
  $("captureImage").src = data.snapshot_url;
  $("captureCaption").textContent =
    "Ảnh tại ROM camera lớn nhất: " +
    degrees(cameraResult) +
    " · " +
    data.captured_at;
  $("captureResult").hidden = false;
  $("captureCareLink").hidden = !saved;
  showMeasurementView(true);
}
function startMeasurement(button) {
  if (measurementActive || measurementBusy || saveInProgress || draftToken)
    return;
  measurementBusy = true;
  return Rehab.action(button, async () => {
    await jsonPost("/measurement/start", { combined: true });
    resultSaved = false;
    cameraResult = sensorResult = 0;
    $("captureResult").hidden = true;
    ["liveAngle", "liveImuAngle", "maxRomSession", "maxImuSession"].forEach(
      (id) => ($(id).textContent = "—"),
    );
    measurementActive = true;
    $("measurementStatus").textContent =
      "Đang mở camera và cảm biến cho cùng một lượt đo.";
    showMeasurementView();
    beginTelemetry();
  }).finally(() => {
    measurementBusy = false;
    updateMeasurementControls();
  });
}
function stopMeasurement(button) {
  if (!measurementActive || measurementBusy || saveInProgress) return;
  measurementBusy = true;
  return Rehab.action(button, async () => {
    const data = await jsonPost("/measurement/stop", { preserve: true });
    endLocalMeasurement();
    draftToken = data.can_save ? data.draft_token : null;
    resultSaved = false;
    if (draftToken) {
      showSessionResult(data, false);
      $("measurementStatus").textContent = "Đã dừng đo. Kết quả chưa lưu.";
      $("combinedHint").textContent =
        "Kết quả được giữ tạm 10 phút. Bấm Lưu kết quả để lưu cả hai nguồn và ảnh vào hồ sơ.";
    } else {
      $("liveAngle").textContent = $("liveImuAngle").textContent = "—";
      $("measurementStatus").textContent =
        data.message || "Đã dừng đo; chưa đủ dữ liệu từ cả hai nguồn.";
      $("combinedHint").textContent =
        "Chưa lưu kết quả. Kiểm tra camera và cảm biến rồi đo lại.";
    }
  }).finally(() => {
    measurementBusy = false;
    updateMeasurementControls();
  });
}
function finishCombined(button) {
  if (
    measurementActive ||
    measurementBusy ||
    saveInProgress ||
    !draftToken ||
    resultSaved
  )
    return;
  saveInProgress = true;
  return Rehab.action(button, async () => {
    const data = await jsonPost("/measurement/save_combined", {
      draft_token: draftToken,
    });
    insertRowToTable(data.camera_rom, data.time, "camera", data.camera_id);
    insertRowToTable(data.imu_rom, data.time, "imu", data.imu_id);
    resultSaved = true;
    draftToken = null;
    showSessionResult(data, true);
    $("measurementStatus").textContent = "Đã lưu kết quả của lượt đo chung.";
    $("combinedHint").textContent =
      "Đã lưu cả hai nguồn và ảnh. Bạn có thể mở hồ sơ hoặc đo lượt mới.";
  }).finally(() => {
    saveInProgress = false;
    updateMeasurementControls();
  });
}
function renumberRows() {
  const body = $("realtime-log-table").tBodies[0];
  [...body.rows]
    .filter((row) => row.cells.length === 5)
    .forEach((row, index) => (row.cells[0].textContent = index + 1));
  if (!body.rows.length) {
    const row = body.insertRow();
    row.id = "no-data-row";
    const cell = row.insertCell();
    cell.colSpan = 5;
    cell.className = "empty-state";
    cell.textContent = "Chưa có lượt đo nào.";
  }
}
function deleteMeasurement(id, button) {
  if (!confirm("Xóa lượt đo này?")) return;
  Rehab.action(button, async () => {
    await post("/delete_measurement/" + encodeURIComponent(id));
    button.closest("tr").remove();
    renumberRows();
  });
}
function deleteAll(button) {
  if (!confirm("Xóa TẤT CẢ lượt đo của bạn? Hành động này KHÔNG thể hoàn tác."))
    return;
  Rehab.action(button, async () => {
    await post("/delete_all_me");
    location.reload();
  });
}
function restart(button) {
  if (measurementActive || measurementBusy || saveInProgress) return;
  if (
    draftToken &&
    !confirm("Kết quả chưa lưu. Bỏ kết quả này để đo lượt mới?")
  )
    return;
  draftToken = null;
  resultSaved = false;
  return startMeasurement(button);
}
function imuCmd(command, button) {
  Rehab.action(button, async () => {
    const data = await post("/imu_cmd/" + command);
    Rehab.notify(data.message || "Đã gửi lệnh hiệu chỉnh.", "info");
  });
}
function toggleCrowd(button) {
  Rehab.action(button, async () => {
    const data = await post("/toggle_crowd");
    button.classList.toggle("legbtn-active", data.crowd);
    button.setAttribute("aria-pressed", String(data.crowd));
    button.textContent = data.crowd
      ? "Chế độ đông người: đang bật"
      : "Chế độ đông người";
  });
}
function setLeg(leg, button) {
  Rehab.action(button, async () => {
    await post("/set_leg/" + leg);
    ["left", "right", "auto"].forEach((value) => {
      $("legBtn-" + value).classList.toggle("legbtn-active", value === leg);
      $("legBtn-" + value).setAttribute("aria-pressed", String(value === leg));
    });
  });
}
async function loadPorts(button) {
  return Rehab.action(button, async () => {
    const data = await Rehab.api("/imu_ports");
    const select = $("portSelect");
    select.replaceChildren();
    for (const port of data.ports)
      select.add(
        new Option(
          port.device + " — " + port.desc,
          port.device,
          false,
          port.device === data.current,
        ),
      );
    if (!data.ports.length) select.add(new Option("(Không thấy cổng nào)", ""));
  });
}
function connectPort(button) {
  if (!$("portSelect").value) {
    Rehab.notify(
      "Chưa tìm thấy cổng cảm biến. Cắm thiết bị rồi làm mới danh sách.",
    );
    return;
  }
  Rehab.action(button, async () => {
    const data = await post(
      "/imu_connect/" + encodeURIComponent($("portSelect").value),
    );
    Rehab.notify("Đang kết nối " + data.port + "…", "info");
  });
}
function reconnectImu(button) {
  Rehab.action(button, async () => {
    await post("/imu_reconnect");
    Rehab.notify("Đang kết nối lại cảm biến…", "info");
  });
}
function setImuMode(mode, button) {
  Rehab.action(button, async () => {
    await post("/set_imu_mode/" + mode);
    ["ble", "serial", "wifi"].forEach((value) => {
      $("modeBtn-" + value).classList.toggle("legbtn-active", mode === value);
      $("modeBtn-" + value).setAttribute(
        "aria-pressed",
        String(mode === value),
      );
    });
    $("bleBox").style.display = mode === "ble" ? "flex" : "none";
    $("usbBox").style.display = mode === "serial" ? "flex" : "none";
    $("wifiBox").style.display = mode === "wifi" ? "flex" : "none";
    if (mode === "serial") await loadPorts();
  });
}
function insertRowToTable(angle, time, source, id) {
  $("no-data-row")?.remove();
  const row = $("realtime-log-table").tBodies[0].insertRow(0);
  ["", time, degrees(angle), source === "imu" ? "Cảm biến" : "Camera"].forEach(
    (value) => (row.insertCell().textContent = value),
  );
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Xóa";
  button.className = "text-button danger";
  button.title = "Xóa lượt đo này";
  button.setAttribute("aria-label", button.title);
  button.addEventListener("click", () => deleteMeasurement(id, button));
  row.insertCell().appendChild(button);
  renumberRows();
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden) disconnectCameraFeed();
  // The next status poll reconnects once the camera is ready and this account owns it.
});
window.addEventListener("pagehide", () => {
  disconnectCameraFeed();
  if (!measurementActive) return;
  fetch("/measurement/stop", {
    method: "POST",
    credentials: "same-origin",
    keepalive: true,
    headers: {
      "X-CSRF-Token":
        document.querySelector('meta[name="csrf-token"]')?.content || "",
    },
  }).catch(() => {});
});
window.addEventListener("DOMContentLoaded", () => {
  $("cameraFeed").addEventListener("error", retryCameraFeed);
  $("cameraFeed").addEventListener("load", () => {
    cameraRetryCount = 0;
  });
  showMeasurementView();
  updateMeasurementControls();
  // Opening a tab never reserves hardware or resets another session.
});
