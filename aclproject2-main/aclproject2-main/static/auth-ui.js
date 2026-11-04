"use strict";
function switchTab(panelId, trigger) {
  const register = panelId === "register-panel";
  document.querySelectorAll(".form-panel").forEach((panel) => {
    panel.hidden = panel.id !== panelId;
    panel.classList.toggle("active", panel.id === panelId);
  });
  document.querySelectorAll(".auth-tabs .tab").forEach((tab) => {
    const active = tab === trigger;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  });
  document.querySelector(".auth-tabs").hidden = register;
  document.getElementById("auth-footer").hidden = register;
  document
    .getElementById("register-tab")
    .setAttribute("aria-expanded", String(register));
  document.getElementById("auth-title").textContent = register
    ? "Đăng ký bệnh nhân"
    : "Đăng nhập";
  document.getElementById("auth-subtitle").textContent = register
    ? "Tạo tài khoản để lưu kết quả và theo dõi phục hồi."
    : "Chọn cổng tài khoản để tiếp tục.";
  document.body.dataset.portal = trigger.dataset.portal;
}
document.addEventListener("DOMContentLoaded", () => {
  const initial = ["patient", "doctor", "register"].includes(
    document.body.dataset.initialPortal,
  )
    ? document.body.dataset.initialPortal
    : "patient";
  const trigger = document.querySelector(`[data-portal="${initial}"]`);
  switchTab(trigger.getAttribute("aria-controls"), trigger);
  document.querySelector(".auth-tabs").addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = [...document.querySelectorAll(".auth-tabs .tab")];
    const current = Math.max(0, tabs.indexOf(document.activeElement));
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? tabs.length - 1
          : (current + (event.key === "ArrowLeft" ? -1 : 1) + tabs.length) %
            tabs.length;
    switchTab(tabs[next].getAttribute("aria-controls"), tabs[next]);
    tabs[next].focus();
  });
  document.querySelectorAll("[data-toggle-password]").forEach((button) =>
    button.addEventListener("click", () => {
      const input = document.getElementById(button.dataset.togglePassword);
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      button.setAttribute("aria-pressed", String(show));
      button.setAttribute("aria-label", show ? "Ẩn mật khẩu" : "Hiện mật khẩu");
    }),
  );
});
