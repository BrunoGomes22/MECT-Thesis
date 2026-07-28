#!/usr/bin/env python3

import argparse
import json
import signal
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lib.xAppBase import xAppBase


HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>OSC xApp Live KPM Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {
      font-family: Arial, sans-serif;
      margin: 20px;
      background: #f7f7f7;
      color: #222;
    }
    h1 {
      margin-bottom: 8px;
    }
    .subtitle {
      margin-bottom: 20px;
      color: #555;
    }
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
    }
    .card {
      background: white;
      border-radius: 10px;
      padding: 14px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    }
    .metric-title {
      font-size: 18px;
      margin-bottom: 8px;
    }
    .metric-title-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .metric-title-row select {
      padding: 4px 8px;
      border: 1px solid #ccc;
      border-radius: 5px;
      background: #fff;
      color: #333;
    }
    .metric-value {
      font-size: 14px;
      color: #444;
      margin-bottom: 8px;
    }
    canvas {
      width: 100%;
      height: 240px;
      border: 1px solid #ddd;
      border-radius: 6px;
      background: #fff;
    }
    .status {
      margin-top: 16px;
      color: #555;
      font-size: 14px;
    }
    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 8px 16px;
      min-height: 20px;
      margin-bottom: 8px;
      color: #444;
      font-size: 13px;
    }
    .legend-item {
      display: inline-flex;
      align-items: center;
      gap: 5px;
    }
    .legend-swatch {
      width: 11px;
      height: 11px;
      border-radius: 50%;
    }
    .ue-current-chart {
      height: 180px;
    }
    .ue-history-chart {
      height: 300px;
    }
    @media (max-width: 900px) {
      .grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <h1>Live KPM Dashboard</h1>
  <div class="subtitle">Real-time OSC xApp metrics from the E2 node</div>

  <div class="grid">
    <div class="card">
      <div class="metric-title">DRB.UEThpDl</div>
      <div class="metric-value" id="value-DRB.UEThpDl">Latest: -</div>
      <canvas id="canvas-DRB.UEThpDl" width="700" height="240"></canvas>
    </div>

    <div class="card">
      <div class="metric-title">DRB.UEThpUl</div>
      <div class="metric-value" id="value-DRB.UEThpUl">Latest: -</div>
      <canvas id="canvas-DRB.UEThpUl" width="700" height="240"></canvas>
    </div>

    <div class="card">
      <div class="metric-title">RRU.PrbUsedDl</div>
      <div class="metric-value" id="value-RRU.PrbUsedDl">Latest: -</div>
      <canvas id="canvas-RRU.PrbUsedDl" width="700" height="240"></canvas>
    </div>

    <div class="card">
      <div class="metric-title">RRU.PrbUsedUl</div>
      <div class="metric-value" id="value-RRU.PrbUsedUl">Latest: -</div>
      <canvas id="canvas-RRU.PrbUsedUl" width="700" height="240"></canvas>
    </div>

    <div class="card">
      <div class="metric-title metric-title-row">
        <span>Current PRBs used by UE</span>
        <select id="ue-prb-direction" aria-label="PRB direction">
          <option value="dl">Downlink</option>
          <option value="ul">Uplink</option>
        </select>
      </div>
      <canvas class="ue-current-chart" id="canvas-ue-prb-current" width="700" height="180"></canvas>
    </div>

    <div class="card">
      <div class="metric-title">PRB usage by UE over time</div>
      <div class="legend" id="legend-ue-prb">Waiting for UE data...</div>
      <canvas class="ue-history-chart" id="canvas-ue-prb-history" width="700" height="300"></canvas>
    </div>

    <div class="card">
      <div class="metric-title">Current DL buffer delay by UE</div>
      <canvas class="ue-current-chart" id="canvas-ue-delay-current" width="700" height="180"></canvas>
    </div>

    <div class="card">
      <div class="metric-title">DL buffer delay by UE over time</div>
      <div class="legend" id="legend-ue-delay">Waiting for UE delay data...</div>
      <canvas class="ue-history-chart" id="canvas-ue-delay-history" width="700" height="300"></canvas>
    </div>
  </div>

  <div class="status" id="status">Waiting for data...</div>

  <script>
    const METRICS = [
      "DRB.UEThpDl",
      "DRB.UEThpUl",
      "RRU.PrbUsedDl",
      "RRU.PrbUsedUl"
    ];

    const UE_COLORS = [
      "#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd",
      "#17becf", "#e377c2", "#8c564b", "#bcbd22", "#7f7f7f"
    ];
    const UE_COLOR_MAP = new Map();
    let nextUeColor = 0;

    function colorForUe(ueId) {
      const key = String(ueId);
      if (UE_COLOR_MAP.has(key)) return UE_COLOR_MAP.get(key);

      const color = nextUeColor < UE_COLORS.length
        ? UE_COLORS[nextUeColor]
        : `hsl(${(nextUeColor * 137.508) % 360}, 65%, 45%)`;
      UE_COLOR_MAP.set(key, color);
      nextUeColor += 1;
      return color;
    }

    function drawPlot(canvasId, points, label) {
      const canvas = document.getElementById(canvasId);
      const ctx = canvas.getContext("2d");

      const w = canvas.width;
      const h = canvas.height;

      ctx.clearRect(0, 0, w, h);

      const marginLeft = 50;
      const marginRight = 15;
      const marginTop = 15;
      const marginBottom = 30;

      const plotW = w - marginLeft - marginRight;
      const plotH = h - marginTop - marginBottom;

      ctx.strokeStyle = "#cccccc";
      ctx.lineWidth = 1;
      ctx.strokeRect(marginLeft, marginTop, plotW, plotH);

      if (!points || points.length === 0) {
        ctx.fillStyle = "#666";
        ctx.fillText("No data yet", marginLeft + 10, marginTop + 20);
        return;
      }

      const values = points.map(p => p.y);
      let minY = Math.min(...values);
      let maxY = Math.max(...values);

      if (minY === maxY) {
        minY -= 1;
        maxY += 1;
      }

      const pad = 0.1 * (maxY - minY);
      minY -= pad;
      maxY += pad;

      function xScale(i) {
        if (points.length === 1) {
          return marginLeft + plotW / 2;
        }
        return marginLeft + (i / (points.length - 1)) * plotW;
      }

      function yScale(v) {
        return marginTop + plotH - ((v - minY) / (maxY - minY)) * plotH;
      }

      ctx.fillStyle = "#333";
      ctx.font = "12px Arial";
      ctx.fillText(maxY.toFixed(2), 5, marginTop + 8);
      ctx.fillText(minY.toFixed(2), 5, marginTop + plotH);

      ctx.strokeStyle = "#1f77b4";
      ctx.lineWidth = 2;
      ctx.beginPath();

      points.forEach((p, i) => {
        const x = xScale(i);
        const y = yScale(p.y);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });

      ctx.stroke();

      const last = points[points.length - 1];
      const lx = xScale(points.length - 1);
      const ly = yScale(last.y);

      ctx.fillStyle = "#d62728";
      ctx.beginPath();
      ctx.arc(lx, ly, 3, 0, 2 * Math.PI);
      ctx.fill();
    }

    function drawCurrentBars(canvasId, seriesByUe, unit, emptyMessage) {
      const canvas = document.getElementById(canvasId);
      const ctx = canvas.getContext("2d");
      const entries = Object.entries(seriesByUe || {}).map(([ueId, points]) => ({
        ueId,
        value: points.length ? points[points.length - 1].y : null
      })).filter(entry => Number.isFinite(entry.value));

      entries.sort((a, b) => b.value - a.value ||
        a.ueId.localeCompare(b.ueId, undefined, {numeric: true})
      );

      const dynamicHeight = Math.max(160, 45 + entries.length * 32);
      canvas.height = dynamicHeight;
      canvas.style.height = `${dynamicHeight}px`;

      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      const marginLeft = 100;
      const marginRight = 75;
      const marginTop = 15;
      const marginBottom = 28;
      const plotW = w - marginLeft - marginRight;
      const plotH = h - marginTop - marginBottom;

      ctx.strokeStyle = "#cccccc";
      ctx.lineWidth = 1;
      ctx.strokeRect(marginLeft, marginTop, plotW, plotH);

      if (!entries.length) {
        ctx.fillStyle = "#666";
        ctx.font = "12px Arial";
        ctx.fillText(emptyMessage, marginLeft + 10, marginTop + 20);
        return;
      }

      const maxValue = Math.max(1, ...entries.map(entry => entry.value)) * 1.1;
      const xScale = value => marginLeft + (value / maxValue) * plotW;
      const rowHeight = plotH / entries.length;
      const barHeight = Math.min(22, rowHeight * 0.68);

      ctx.fillStyle = "#333";
      ctx.font = "12px Arial";
      ctx.fillText("0", marginLeft - 3, h - 7);
      ctx.fillText(`${maxValue.toFixed(1)} ${unit}`, w - marginRight - 55, h - 7);

      entries.forEach((entry, index) => {
        const centerY = marginTop + rowHeight * (index + 0.5);
        const barTop = centerY - barHeight / 2;
        const barWidth = Math.max(1, xScale(entry.value) - marginLeft);

        ctx.fillStyle = "#333";
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        ctx.fillText(`UE ${entry.ueId}`, marginLeft - 10, centerY);

        ctx.fillStyle = colorForUe(entry.ueId);
        ctx.fillRect(marginLeft, barTop, barWidth, barHeight);

        ctx.fillStyle = "#333";
        ctx.textAlign = "left";
        ctx.fillText(entry.value.toFixed(2), marginLeft + barWidth + 7, centerY);
      });

      ctx.textAlign = "start";
      ctx.textBaseline = "alphabetic";
    }

    function drawStackedUeHistory(canvasId, seriesByUe) {
      const canvas = document.getElementById(canvasId);
      const ctx = canvas.getContext("2d");
      const ueIds = Object.keys(seriesByUe || {}).filter(ueId =>
        (seriesByUe[ueId] || []).length > 0
      ).sort((a, b) => a.localeCompare(b, undefined, {numeric: true}));
      const xValues = [...new Set(ueIds.flatMap(ueId =>
        (seriesByUe[ueId] || []).map(point => point.x)
      ))].sort((a, b) => a - b);
      const w = canvas.width;
      const h = canvas.height;

      ctx.clearRect(0, 0, w, h);

      const marginLeft = 55;
      const marginRight = 15;
      const marginTop = 15;
      const marginBottom = 32;
      const plotW = w - marginLeft - marginRight;
      const plotH = h - marginTop - marginBottom;

      ctx.strokeStyle = "#cccccc";
      ctx.lineWidth = 1;
      ctx.strokeRect(marginLeft, marginTop, plotW, plotH);

      if (!ueIds.length || !xValues.length) {
        ctx.fillStyle = "#666";
        ctx.font = "12px Arial";
        ctx.fillText("No per-UE PRB history yet", marginLeft + 10, marginTop + 20);
        return;
      }

      const valueMaps = Object.fromEntries(ueIds.map(ueId => [
        ueId,
        new Map((seriesByUe[ueId] || []).map(point => [point.x, point.y]))
      ]));
      const plottedXValues = xValues.length === 1 ? [xValues[0], xValues[0] + 1] : xValues;
      const sourceX = value => xValues.length === 1 ? xValues[0] : value;
      const totals = plottedXValues.map(x => ueIds.reduce(
        (sum, ueId) => sum + (valueMaps[ueId].get(sourceX(x)) || 0), 0
      ));
      const maxTotal = Math.max(1, ...totals) * 1.1;
      const minX = plottedXValues[0];
      const maxX = plottedXValues[plottedXValues.length - 1];
      const xScale = value => marginLeft + ((value - minX) / (maxX - minX)) * plotW;
      const yScale = value => marginTop + plotH - (value / maxTotal) * plotH;
      let cumulative = new Array(plottedXValues.length).fill(0);

      ueIds.forEach(ueId => {
        const values = plottedXValues.map(x => valueMaps[ueId].get(sourceX(x)) || 0);
        const tops = values.map((value, index) => cumulative[index] + value);

        ctx.beginPath();
        plottedXValues.forEach((x, index) => {
          const px = xScale(x);
          const py = yScale(tops[index]);
          if (index === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });
        for (let index = plottedXValues.length - 1; index >= 0; index -= 1) {
          ctx.lineTo(xScale(plottedXValues[index]), yScale(cumulative[index]));
        }
        ctx.closePath();
        ctx.globalAlpha = 0.78;
        ctx.fillStyle = colorForUe(ueId);
        ctx.fill();
        ctx.globalAlpha = 1;

        ctx.beginPath();
        plottedXValues.forEach((x, index) => {
          const px = xScale(x);
          const py = yScale(tops[index]);
          if (index === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });
        ctx.strokeStyle = colorForUe(ueId);
        ctx.lineWidth = 1.5;
        ctx.stroke();

        cumulative = tops;
      });

      ctx.fillStyle = "#333";
      ctx.font = "12px Arial";
      ctx.fillText(maxTotal.toFixed(2), 5, marginTop + 8);
      ctx.fillText("0", 35, marginTop + plotH);
      ctx.fillText("Older samples", marginLeft, h - 8);
      ctx.textAlign = "right";
      ctx.fillText("Latest", marginLeft + plotW, h - 8);
      ctx.textAlign = "start";
    }

    function drawUeLineHistory(canvasId, seriesByUe, unit, emptyMessage) {
      const canvas = document.getElementById(canvasId);
      const ctx = canvas.getContext("2d");
      const ueIds = Object.keys(seriesByUe || {}).filter(ueId =>
        (seriesByUe[ueId] || []).length > 0
      ).sort((a, b) => a.localeCompare(b, undefined, {numeric: true}));
      const allPoints = ueIds.flatMap(ueId => seriesByUe[ueId] || []);
      const w = canvas.width;
      const h = canvas.height;

      ctx.clearRect(0, 0, w, h);

      const marginLeft = 75;
      const marginRight = 15;
      const marginTop = 15;
      const marginBottom = 32;
      const plotW = w - marginLeft - marginRight;
      const plotH = h - marginTop - marginBottom;

      ctx.strokeStyle = "#cccccc";
      ctx.lineWidth = 1;
      ctx.strokeRect(marginLeft, marginTop, plotW, plotH);

      if (!allPoints.length) {
        ctx.fillStyle = "#666";
        ctx.font = "12px Arial";
        ctx.fillText(emptyMessage, marginLeft + 10, marginTop + 20);
        return;
      }

      const xValues = allPoints.map(point => point.x);
      let minX = Math.min(...xValues);
      let maxX = Math.max(...xValues);
      const dataMaxY = Math.max(1, ...allPoints.map(point => point.y));

      // Build a readable adaptive scale with approximately six intervals.
      const rawTickStep = (dataMaxY * 1.05) / 6;
      const tickMagnitude = 10 ** Math.floor(Math.log10(rawTickStep));
      const normalizedStep = rawTickStep / tickMagnitude;
      let niceStep;
      if (normalizedStep <= 1) niceStep = 1;
      else if (normalizedStep <= 2) niceStep = 2;
      else if (normalizedStep <= 2.5) niceStep = 2.5;
      else if (normalizedStep <= 5) niceStep = 5;
      else niceStep = 10;

      const tickStep = niceStep * tickMagnitude;
      const tickCount = Math.max(2, Math.ceil((dataMaxY * 1.05) / tickStep));
      const maxY = tickCount * tickStep;
      const tickDecimals = tickStep >= 1
        ? (Number.isInteger(tickStep) ? 0 : 1)
        : Math.min(3, Math.ceil(-Math.log10(tickStep)) + 1);

      if (minX === maxX) {
        minX -= 0.5;
        maxX += 0.5;
      }

      const xScale = value => marginLeft + ((value - minX) / (maxX - minX)) * plotW;
      const yScale = value => marginTop + plotH - (value / maxY) * plotH;

      ctx.font = "12px Arial";
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      for (let tickIndex = 0; tickIndex <= tickCount; tickIndex += 1) {
        const tickValue = tickIndex * tickStep;
        const y = yScale(tickValue);

        ctx.strokeStyle = tickIndex === 0 || tickIndex === tickCount
          ? "#cccccc"
          : "#e8e8e8";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(marginLeft, y);
        ctx.lineTo(marginLeft + plotW, y);
        ctx.stroke();

        ctx.fillStyle = "#444";
        ctx.fillText(tickValue.toFixed(tickDecimals), marginLeft - 8, y);
      }

      ctx.save();
      ctx.translate(13, marginTop + plotH / 2);
      ctx.rotate(-Math.PI / 2);
      ctx.textAlign = "center";
      ctx.fillText(unit, 0, 0);
      ctx.restore();
      ctx.textBaseline = "alphabetic";

      ueIds.forEach(ueId => {
        const points = seriesByUe[ueId] || [];
        const color = colorForUe(ueId);
        ctx.beginPath();
        points.forEach((point, index) => {
          const x = xScale(point.x);
          const y = yScale(point.y);
          if (index === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.stroke();

        const last = points[points.length - 1];
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(xScale(last.x), yScale(last.y), 3, 0, 2 * Math.PI);
        ctx.fill();
      });

      ctx.fillStyle = "#333";
      ctx.font = "12px Arial";
      ctx.textAlign = "start";
      ctx.fillText("Older samples", marginLeft, h - 8);
      ctx.textAlign = "right";
      ctx.fillText("Latest", marginLeft + plotW, h - 8);
      ctx.textAlign = "start";
    }

    function updateUeLegend(legendId, seriesByUe, unit, waitingMessage) {
      const legend = document.getElementById(legendId);
      const ueIds = Object.keys(seriesByUe || {}).sort((a, b) =>
        a.localeCompare(b, undefined, {numeric: true})
      );

      if (!ueIds.length) {
        legend.textContent = waitingMessage;
        return;
      }

      legend.replaceChildren(...ueIds.map(ueId => {
        const points = seriesByUe[ueId] || [];
        const latest = points.length ? points[points.length - 1].y.toFixed(2) : "-";
        const item = document.createElement("span");
        item.className = "legend-item";

        const swatch = document.createElement("span");
        swatch.className = "legend-swatch";
        swatch.style.backgroundColor = colorForUe(ueId);

        const label = document.createElement("span");
        label.textContent = `UE ${ueId}: ${latest} ${unit}`;
        item.append(swatch, label);
        return item;
      }));
    }

    async function refresh() {
      try {
        const res = await fetch("/api/metrics");
        const data = await res.json();

        document.getElementById("status").textContent =
          "Last update: " + (data.last_update || "n/a") +
          " | Samples: " + (data.sample_count || 0);

        METRICS.forEach(metric => {
          const series = data.series[metric] || [];
          const latest = series.length ? series[series.length - 1].y : null;

          document.getElementById("value-" + metric).textContent =
            "Latest: " + (latest === null ? "-" : latest.toFixed(2));

          drawPlot("canvas-" + metric, series, metric);
        });

        const direction = document.getElementById("ue-prb-direction").value;
        const uePrbSeries = data.ue_prb_series?.[direction] || {};
        updateUeLegend(
          "legend-ue-prb", uePrbSeries, "PRBs", "Waiting for UE data..."
        );
        drawCurrentBars(
          "canvas-ue-prb-current", uePrbSeries, "PRBs", "No per-UE PRB data yet"
        );
        drawStackedUeHistory("canvas-ue-prb-history", uePrbSeries);

        const ueDelaySeries = data.ue_delay_series || {};
        updateUeLegend(
          "legend-ue-delay", ueDelaySeries, "ms", "Waiting for UE delay data..."
        );
        drawCurrentBars(
          "canvas-ue-delay-current", ueDelaySeries, "ms", "No per-UE delay data yet"
        );
        drawUeLineHistory(
          "canvas-ue-delay-history", ueDelaySeries, "ms", "No per-UE delay history yet"
        );
      } catch (err) {
        document.getElementById("status").textContent =
          "Dashboard error: " + err;
      }
    }

    document.getElementById("ue-prb-direction").addEventListener("change", refresh);
    refresh();
    setInterval(refresh, 1000);
  </script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    xapp_instance = None

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._send_html(HTML_PAGE)
            return

        if self.path == "/api/metrics":
            if DashboardHandler.xapp_instance is None:
                self._send_json({"error": "xApp not initialized"}, status=500)
                return
            self._send_json(DashboardHandler.xapp_instance.get_dashboard_data())
            return

        self._send_json({"error": "not found"}, status=404)


class MyXapp(xAppBase):
    def __init__(
        self,
        config,
        http_server_port,
        rmr_port,
        dashboard_host,
        dashboard_port,
        max_points=120,
        max_valid_delay_ms=200.0,
    ):
        super(MyXapp, self).__init__(config, http_server_port, rmr_port)

        self.lock = threading.Lock()
        self.max_points = max_points
        self.max_valid_delay_ms = max_valid_delay_ms
        self.sample_idx = 0
        self.last_update = None
        self.ue_sample_idx = 0

        self.metric_keys = [
            "DRB.UEThpDl",
            "DRB.UEThpUl",
            "RRU.PrbUsedDl",
            "RRU.PrbUsedUl"
        ]

        self.history = {
            metric: deque(maxlen=max_points) for metric in self.metric_keys
        }
        self.ue_prb_history = {
            "dl": {},
            "ul": {}
        }
        self.ue_delay_history = {}

        self.dashboard_host = dashboard_host
        self.dashboard_port = dashboard_port
        self.dashboard_server = None
        self.dashboard_thread = None

    def start_dashboard(self):
        DashboardHandler.xapp_instance = self
        self.dashboard_server = ThreadingHTTPServer((self.dashboard_host, self.dashboard_port), DashboardHandler)
        self.dashboard_thread = threading.Thread(target=self.dashboard_server.serve_forever, daemon=True)
        self.dashboard_thread.start()
        print(f"Dashboard available at http://{self.dashboard_host}:{self.dashboard_port}")
        if self.dashboard_host == "0.0.0.0":
            print("Access it from another machine using: http://<LAB-PC-IP>:{}".format(self.dashboard_port))

    def stop_dashboard(self):
        if self.dashboard_server is not None:
            self.dashboard_server.shutdown()
            self.dashboard_server.server_close()

    def signal_handler(self, signum, frame):
        self.stop_dashboard()
        super().signal_handler(signum, frame)

    def _extract_scalar(self, value):
        if isinstance(value, list):
            if not value:
                return None
            value = value[0]

        if value is None:
            return None

        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def get_dashboard_data(self):
        with self.lock:
            payload = {
                "last_update": self.last_update,
                "sample_count": self.sample_idx,
                "series": {},
                "ue_prb_series": {"dl": {}, "ul": {}},
                "ue_delay_series": {}
            }

            for metric in self.metric_keys:
                payload["series"][metric] = list(self.history[metric])

            for direction in ("dl", "ul"):
                for ue_id, history in self.ue_prb_history[direction].items():
                    payload["ue_prb_series"][direction][str(ue_id)] = list(history)

            for ue_id, history in self.ue_delay_history.items():
                payload["ue_delay_series"][str(ue_id)] = list(history)

            return payload

    def ue_metrics_subscription_callback(self, e2_agent_id, subscription_id, indication_hdr, indication_msg):
        """Store PRB and DL buffer-delay measurements for every matched UE."""
        meas_data = self.e2sm_kpm.extract_meas_data(indication_msg)
        ue_meas_data = meas_data.get("ueMeasData", {})
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        with self.lock:
            for ue_id, ue_data in ue_meas_data.items():
                metrics = ue_data.get("measData", {})

                for direction, metric in (
                    ("dl", "RRU.PrbUsedDl"),
                    ("ul", "RRU.PrbUsedUl"),
                ):
                    scalar = self._extract_scalar(metrics.get(metric))
                    if scalar is None:
                        continue

                    history = self.ue_prb_history[direction].setdefault(
                        str(ue_id), deque(maxlen=self.max_points)
                    )
                    history.append({"x": self.ue_sample_idx, "y": scalar})

                raw_delay = self._extract_scalar(metrics.get("DRB.RlcSduDelayDl"))
                if raw_delay is not None:
                    # DRB.RlcSduDelayDl is reported in units of 0.1 ms.
                    delay_ms = raw_delay * 0.1
                    if 0 <= delay_ms <= self.max_valid_delay_ms:
                        history = self.ue_delay_history.setdefault(
                            str(ue_id), deque(maxlen=self.max_points)
                        )
                        history.append({"x": self.ue_sample_idx, "y": delay_ms})

            if ue_meas_data:
                self.ue_sample_idx += 1
                self.last_update = now

    def my_subscription_callback(self, e2_agent_id, subscription_id, indication_hdr, indication_msg):
        print("\nRIC Indication Received from {} for Subscription ID: {}".format(e2_agent_id, subscription_id))
        indication_hdr = self.e2sm_kpm.extract_hdr_info(indication_hdr)
        meas_data = self.e2sm_kpm.extract_meas_data(indication_msg)

        print("E2SM_KPM RIC Indication Content:")
        print("-ColletStartTime: ", indication_hdr['colletStartTime'])
        print("-Measurements Data:")

        granulPeriod = meas_data.get("granulPeriod", None)
        if granulPeriod is not None:
            print("-granulPeriod: {}".format(granulPeriod))

        for metric_name, value in meas_data["measData"].items():
            print("--Metric: {}, Value: {}".format(metric_name, value))

        now = time.strftime("%Y-%m-%d %H:%M:%S")

        with self.lock:
            for metric in self.metric_keys:
                raw_value = meas_data["measData"].get(metric, [None])
                scalar = self._extract_scalar(raw_value)

                if scalar is None:
                    if len(self.history[metric]) > 0:
                        scalar = self.history[metric][-1]["y"]
                    else:
                        scalar = 0.0

                self.history[metric].append({
                    "x": self.sample_idx,
                    "y": scalar
                })

            self.sample_idx += 1
            self.last_update = now

    @xAppBase.start_function
    def start(self, e2_node_id, metric_names):
        print("Subscribe to E2 node ID: {}, RAN func: e2sm_kpm for metrics {}".format(e2_node_id, metric_names))
        report_period = 1000
        granul_period = 100

        self.start_dashboard()

        self.e2sm_kpm.subscribe_report_service_style_1(
            e2_node_id,
            report_period,
            metric_names,
            granul_period,
            self.my_subscription_callback
        )

        # Report style 4 includes the UE ID with every measurement, allowing
        # PRB and DL buffer-delay histories to be kept per connected UE.
        matching_ue_conds = [{
            "testCondInfo": {
                "testType": ("ul-rSRP", "true"),
                "testExpr": "lessthan",
                "testValue": ("valueInt", 1000),
            }
        }]
        self.e2sm_kpm.subscribe_report_service_style_4(
            e2_node_id,
            report_period,
            matching_ue_conds,
            ["RRU.PrbUsedDl", "RRU.PrbUsedUl", "DRB.RlcSduDelayDl"],
            granul_period,
            self.ue_metrics_subscription_callback
        )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='OSC xApp with live web dashboard')
    parser.add_argument("--config", type=str, default='', help="xApp config file path")
    parser.add_argument("--http_server_port", type=int, default=8091, help="HTTP server listen port used by xAppBase")
    parser.add_argument("--rmr_port", type=int, default=4561, help="RMR port")
    parser.add_argument("--dashboard_host", type=str, default="0.0.0.0", help="Dashboard bind host")
    parser.add_argument("--dashboard_port", type=int, default=8088, help="Dashboard port")
    parser.add_argument("--e2_node_id", type=str, default='gnbd_001_001_00019b_0', help="E2 Node ID")
    parser.add_argument("--ran_func_id", type=int, default=2, help="RAN function ID")
    parser.add_argument(
        "--metrics",
        type=str,
        default="DRB.UEThpDl,DRB.UEThpUl,RRU.PrbUsedDl,RRU.PrbUsedUl",
        help="Metrics name as comma-separated string"
    )
    parser.add_argument("--max_points", type=int, default=120, help="Number of samples kept in memory")
    parser.add_argument(
        "--max_valid_delay_ms",
        type=float,
        default=600.0,
        help="Discard per-UE DL buffer-delay samples above this value",
    )

    args = parser.parse_args()

    metrics = [m.strip() for m in args.metrics.split(",")]

    myXapp = MyXapp(
        args.config,
        args.http_server_port,
        args.rmr_port,
        args.dashboard_host,
        args.dashboard_port,
        max_points=args.max_points,
        max_valid_delay_ms=args.max_valid_delay_ms,
    )

    myXapp.e2sm_kpm.set_ran_func_id(args.ran_func_id)

    signal.signal(signal.SIGQUIT, myXapp.signal_handler)
    signal.signal(signal.SIGTERM, myXapp.signal_handler)
    signal.signal(signal.SIGINT, myXapp.signal_handler)

    myXapp.start(args.e2_node_id, metrics)
