"use strict";
document.addEventListener("DOMContentLoaded", () => {
  const search = document.getElementById("patient-search");
  const rows = [...document.querySelectorAll("[data-patient-row]")];
  const normalize = (value) =>
    value
      .toLocaleLowerCase("vi")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/đ/g, "d");
  search.addEventListener("input", () => {
    const query = normalize(search.value.trim());
    let count = 0;
    rows.forEach((row) => {
      row.hidden = !normalize(row.textContent).includes(query);
      if (!row.hidden) count++;
    });
    document.getElementById("patient-search-empty").hidden =
      !rows.length || count > 0;
    document.getElementById("patient-search-status").textContent =
      count + " bệnh nhân phù hợp.";
  });
  const navigation = [
    ...document.querySelectorAll(".doctor-nav a, .doctor-mobile-nav a"),
  ];
  const syncNavigation = () => {
    const active = location.hash || "#overview";
    navigation.forEach((link) => {
      const selected = link.getAttribute("href") === active;
      link.classList.toggle("active", selected);
      if (selected) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
  };
  window.addEventListener("hashchange", syncNavigation);
  syncNavigation();
  const chart = document.getElementById("rom-trend");
  const data = document.getElementById("rom-trend-data");
  if (!chart || !data) return;
  const points = JSON.parse(data.textContent);
  if (!points.length) return;
  const NS = "http://www.w3.org/2000/svg";
  const make = (tag, attrs, text) => {
    const element = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([key, value]) =>
      element.setAttribute(key, String(value)),
    );
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const svg = make("svg", { viewBox: "0 0 600 230", "aria-hidden": "true" });
  const maximum = Math.max(
    90,
    Math.ceil(Math.max(...points.map((p) => p.angle)) / 30) * 30,
  );
  for (let tick = 0; tick <= 3; tick++) {
    const y = 190 - tick * 54;
    svg.appendChild(
      make("line", { x1: 48, x2: 580, y1: y, y2: y, stroke: "#EAEAEA" }),
    );
    svg.appendChild(
      make(
        "text",
        {
          x: 38,
          y: y + 4,
          "text-anchor": "end",
          fill: "#59646C",
          "font-size": 12,
        },
        Math.round((maximum * tick) / 3) + "°",
      ),
    );
  }
  const dates = [...new Set(points.map((p) => p.time))];
  const x = (time) =>
    dates.length === 1
      ? 305
      : 56 + (dates.indexOf(time) * 516) / (dates.length - 1);
  ["camera", "imu"].forEach((source) => {
    const values = points.filter((p) => p.source === source);
    const color = source === "camera" ? "#285E91" : "#6F8598";
    if (values.length > 1)
      svg.appendChild(
        make("polyline", {
          points: values
            .map((p) => x(p.time) + "," + (190 - (p.angle / maximum) * 162))
            .join(" "),
          fill: "none",
          stroke: color,
          "stroke-width": 2.5,
        }),
      );
    values.forEach((p) =>
      svg.appendChild(
        make("circle", {
          cx: x(p.time),
          cy: 190 - (p.angle / maximum) * 162,
          r: 4,
          fill: "#FFFFFF",
          stroke: color,
          "stroke-width": 2,
        }),
      ),
    );
  });
  dates.forEach((date, index) => {
    if (dates.length > 5 && index % 2 && index !== dates.length - 1) return;
    svg.appendChild(
      make(
        "text",
        {
          x: x(date),
          y: 217,
          "text-anchor": "middle",
          fill: "#59646C",
          "font-size": 12,
        },
        date.slice(8, 10) + "/" + date.slice(5, 7),
      ),
    );
  });
  chart.appendChild(svg);
});
