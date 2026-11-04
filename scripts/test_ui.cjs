const { chromium } = require(
  process.env.UI_PLAYWRIGHT_MODULE || "playwright-core",
);
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const out = path.resolve(
  process.env.UI_SCREENSHOT_DIR ||
    path.join(__dirname, "../.test-artifacts/ui-preview"),
);
fs.mkdirSync(out, { recursive: true });
const root = process.env.UI_PREVIEW_URL || "http://127.0.0.1:5003";
const failures = [];
async function ready(page) {
  await page.evaluate(() => document.fonts.ready);
}
async function fits(page, label) {
  await ready(page);
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
  const size = await page.evaluate(() => ({
    width: innerWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  if (size.scroll > size.width + 1)
    console.log(
      await page.evaluate(() =>
        [...document.querySelectorAll("body *")]
          .filter(
            (el) =>
              el.getBoundingClientRect().right > innerWidth + 1 &&
              !el.closest(".table-scroll,.record-table-wrap"),
          )
          .slice(0, 12)
          .map((el) => ({
            tag: el.tagName,
            id: el.id,
            class: el.className,
            right: el.getBoundingClientRect().right,
            width: el.getBoundingClientRect().width,
          })),
      ),
    );
  assert.ok(
    size.scroll <= size.width + 1,
    label + ": horizontal overflow " + JSON.stringify(size),
  );
}
async function login(page, role) {
  await page.goto(root);
  await page.locator("#" + role + "-login-tab").click();
  await page.locator("#" + role + "-login-username").fill(role + "_demo");
  await page.locator("#" + role + "-login-password").fill("preview-only-2026");
  await Promise.all([
    page.waitForURL("**/" + (role === "doctor" ? "admin" : "dashboard")),
    page.locator("#" + role + "-login-panel button[type=submit]").click(),
  ]);
}
(async () => {
  const browser = await chromium.launch({
    executablePath:
      process.env.UI_BROWSER_PATH ||
      "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    headless: true,
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => failures.push(error.message));
  page.on("response", (response) => {
    if (response.url().includes("/static/") && response.status() >= 400)
      failures.push(response.status() + " " + response.url());
  });
  for (const [width, height, label] of [
    [1440, 1000, "desktop"],
    [390, 844, "mobile"],
    [320, 780, "small"],
  ]) {
    await page.setViewportSize({ width, height });
    await page.goto(root);
    await fits(page, "login " + label);
    assert.equal(
      await page.locator("img").count(),
      0,
      "login must not contain images",
    );
    await page.locator("#doctor-login-tab").click();
    assert.equal(await page.locator("#doctor-login-panel").isVisible(), true);
    await page.locator("#doctor-login-tab").press("ArrowLeft");
    assert.equal(await page.locator("#patient-login-panel").isVisible(), true);
    await page.locator("#register-tab").click();
    assert.equal(await page.locator("#register-panel").isVisible(), true);
    await fits(page, "register " + label);
    await page.locator(".auth-return").click();
    await page.locator("[data-toggle-password=patient-login-password]").click();
    assert.equal(
      await page.locator("#patient-login-password").getAttribute("type"),
      "text",
    );
    await page.locator("[data-toggle-password=patient-login-password]").click();
    if (label !== "small")
      await page.screenshot({
        path: path.join(out, "login-" + label + ".png"),
        fullPage: true,
      });
  }
  await login(page, "patient");
  for (const [width, height, label] of [
    [1440, 1000, "desktop"],
    [1024, 768, "laptop"],
    [390, 844, "mobile"],
    [320, 780, "small"],
  ]) {
    await page.setViewportSize({ width, height });
    await fits(page, "patient " + label);
    assert.equal(await page.locator("#startMeasurement").isVisible(), true);
    assert.equal(await page.locator("#finishCombined").isDisabled(), true);
    if (width < 761) {
      const bounds = await page.locator("#startMeasurement").boundingBox();
      assert.ok(
        bounds.y + bounds.height < height - 70,
        "start is visible above mobile navigation",
      );
    }
    if (label !== "small")
      await page.screenshot({
        path: path.join(out, "patient-" + label + ".png"),
        fullPage: true,
      });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.route("**/measurement/status", (route) =>
    route.fulfill({
      json: {
        active: true,
        camera: {
          angle: 87.4,
          max_rom: 87.4,
          tracking: true,
          quality: { ready: true, message: "Khớp chân rõ trong hình." },
          snapshot_ready: true,
          stream_ready: false,
          camera: {},
        },
        imu: {
          angle: 86.8,
          max_rom: 86.8,
          receiving: true,
          connected: true,
          calibrating: false,
          jitter: 1.2,
        },
      },
    }),
  );
  const result = {
    can_save: true,
    draft_token: "ui-qa-only",
    camera_rom: 87.4,
    imu_rom: 86.8,
    time: "2026-11-04 09:42:00",
    snapshot_url: "/static/icons/camera.svg",
    captured_at: "2026-11-04 09:42:00",
  };
  await page.route("**/measurement/stop", (route) =>
    route.fulfill({ json: result }),
  );
  await page.route("**/measurement/save_combined", (route) =>
    route.fulfill({ json: { ...result, camera_id: 101, imu_id: 102 } }),
  );
  await page.locator("#startMeasurement").click();
  await page.waitForFunction(
    () => document.getElementById("liveAngle").textContent === "87.4°",
  );
  assert.equal(await page.locator("#liveImuAngle").textContent(), "86.8°");
  assert.equal(await page.locator("#finishCombined").isDisabled(), true);
  await page.locator("#stopMeasurement").click();
  await page.locator("#resultPanel").waitFor({ state: "visible" });
  assert.equal(await page.locator("#finishCombined").isEnabled(), true);
  await page.locator("#finishCombined").click();
  await page.waitForFunction(() =>
    document.getElementById("resultStatus").textContent.startsWith("Đã lưu"),
  );
  assert.equal(await page.locator("#finishCombined").isDisabled(), true);
  const csrf = await page
    .locator("meta[name=csrf-token]")
    .getAttribute("content");
  await page.request.post(root + "/measurement/stop", {
    headers: { "X-CSRF-Token": csrf },
  });
  await page.unrouteAll();
  for (const route of ["/profile", "/my_records", "/care"]) {
    await page.goto(root + route);
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 900 });
      await fits(page, "secondary " + route + " " + width);
    }
  }
  await page.goto(root + "/logout");
  await login(page, "doctor");
  for (const [width, height, label] of [
    [1440, 1000, "desktop"],
    [1024, 768, "laptop"],
    [390, 844, "mobile"],
    [320, 780, "small"],
  ]) {
    await page.setViewportSize({ width, height });
    await fits(page, "doctor " + label);
    assert.equal(await page.locator("#rom-trend svg").count(), 1);
    await page.locator("#patient-search").fill("le hoai");
    assert.equal(await page.locator("[data-patient-row]:visible").count(), 1);
    await page.locator("#patient-search").fill("zzzz");
    assert.equal(await page.locator("#patient-search-empty").isVisible(), true);
    await page.locator("#patient-search").fill("");
    assert.equal(await page.locator("[data-patient-row]:visible").count(), 4);
    if (label !== "small")
      await page.screenshot({
        path: path.join(out, "doctor-" + label + ".png"),
        fullPage: true,
      });
  }
  await page.goto(root + "/admin/patient/1");
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await fits(page, "doctor patient detail " + width);
  }
  await context.close();
  await browser.close();
  assert.deepEqual(failures, [], "browser console or asset errors");
  fs.writeFileSync(
    path.join(out, "report.json"),
    JSON.stringify(
      {
        screens: 8,
        viewports: [320, 390, 1024, 1440],
        checks: [
          "separate logins",
          "registration",
          "password visibility",
          "no login image",
          "no horizontal overflow",
          "combined start-stop-save",
          "doctor search",
          "real-data chart",
          "secondary patient and doctor pages",
          "no browser errors",
        ],
        passed: true,
      },
      null,
      2,
    ),
  );
  console.log(
    "PASS: responsive UI at 320/390/1024/1440 px; login/register; one shared measurement; doctor search and chart; no browser errors.",
  );
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
