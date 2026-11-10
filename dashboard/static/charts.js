/* Minimal time-series line chart on <canvas>: hairline grid, 2px lines,
   end dots with a surface ring, crosshair + one tooltip for every series,
   toggle-to-isolate legend and a table view. No external dependencies. */
"use strict";

(function (global) {
  const TIME_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];

  function niceStep(span, target) {
    const raw = span / Math.max(1, target);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    return (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  }

  function niceScale(min, max, target) {
    if (!isFinite(min) || !isFinite(max)) { min = 0; max = 1; }
    if (max - min < 1e-9) {
      const pad = Math.abs(max) * 0.1 || 1;
      min -= min === 0 ? 0 : pad;
      max += pad;
    }
    const step = niceStep(max - min, target);
    const lo = Math.floor(min / step + 1e-9) * step;
    const hi = Math.ceil(max / step - 1e-9) * step;
    const ticks = [];
    for (let v = lo; v <= hi + step * 1e-6; v += step) ticks.push(Math.round(v / step) * step);
    return { lo, hi: hi > lo ? hi : lo + step, step, ticks };
  }

  function pad2(n) { return String(n).padStart(2, "0"); }

  function fmtClock(t, sec) {
    const d = new Date(t * 1000);
    return pad2(d.getHours()) + ":" + pad2(d.getMinutes()) + (sec ? ":" + pad2(d.getSeconds()) : "");
  }

  function fmtTick(v, step) {
    const abs = Math.abs(v);
    if (abs >= 10000) return (v / 1000).toLocaleString("en-GB", { maximumFractionDigits: 1 }) + "K";
    const digits = step >= 1 ? 0 : Math.min(3, Math.ceil(-Math.log10(step)));
    return v.toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  function lowerBound(arr, t) {
    let lo = 0, hi = arr.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (arr[mid] < t) lo = mid + 1; else hi = mid; }
    return lo;
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  class LineChart {
    /* opts: title, unit, height, zero (baseline at 0), yMin/yMax (fixed
       bounds), fmt(v) for values, maxGap (s) to break lines */
    constructor(container, opts) {
      this.opts = Object.assign({ height: 170, zero: true, fmt: (v) => String(v), maxGap: 4 }, opts);
      this.hidden = new Set();
      this.series = [];
      this.times = [];
      this.t0 = 0;
      this.t1 = 1;
      this.hoverIdx = null;
      this.dim = false;

      const root = el("figure", "chart");
      root.style.margin = "0";
      const head = el("div", "chart-head");
      head.append(el("h3", null, this.opts.title));
      this.unitEl = el("span", "chart-unit", this.opts.unit || "");
      head.append(this.unitEl);
      this.tableBtn = el("button", "btn small ghost", "Table");
      this.tableBtn.type = "button";
      this.tableBtn.setAttribute("aria-pressed", "false");
      this.tableBtn.addEventListener("click", () => this.toggleTable());
      head.append(this.tableBtn);
      this.legendEl = el("div", "chart-legend");
      this.legendEl.setAttribute("role", "group");
      this.legendEl.setAttribute("aria-label", "Legend (click to show/hide a series)");
      const body = el("div", "chart-body");
      this.canvas = document.createElement("canvas");
      this.canvas.tabIndex = 0;
      this.canvas.setAttribute("role", "img");
      this.canvas.setAttribute("aria-label", `${this.opts.title} (${this.opts.unit || ""}). Arrow keys read the values; the Table button shows the numbers.`);
      this.canvas.style.height = this.opts.height + "px";
      this.tip = el("div", "chart-tip");
      this.tip.hidden = true;
      this.emptyEl = el("div", "chart-empty", "No data");
      body.append(this.canvas, this.tip, this.emptyEl);
      this.tableEl = el("div", "chart-table");
      this.tableEl.hidden = true;
      root.append(head, this.legendEl, body, this.tableEl);
      container.append(root);
      this.root = root;

      this.canvas.addEventListener("pointermove", (e) => this._onMove(e));
      this.canvas.addEventListener("pointerleave", () => { this.hoverIdx = null; this.render(); });
      this.canvas.addEventListener("keydown", (e) => this._onKey(e));
      this.canvas.addEventListener("blur", () => { this.hoverIdx = null; this.render(); });
      if (global.ResizeObserver) {
        this._ro = new ResizeObserver(() => this.render());
        this._ro.observe(body);
      }
    }

    setDim(dim) {
      if (dim !== this.dim) { this.dim = dim; this.root.classList.toggle("dim", dim); }
    }

    setUnit(text) { this.unitEl.textContent = text; }

    /* series: [{key, label, color (css var), points: values aligned to times}] */
    setData(times, series, t0, t1, opts) {
      this.times = times;
      this.series = series;
      this.t0 = t0;
      this.t1 = t1;
      this.extra = opts || {};
      this._legend();
      this.render();
      if (!this.tableEl.hidden) this._table();
    }

    toggleTable() {
      const show = this.tableEl.hidden;
      this.tableEl.hidden = !show;
      this.tableBtn.setAttribute("aria-pressed", String(show));
      if (show) this._table();
    }

    _legend() {
      const keys = this.series.map((s) => s.key + "|" + s.label + "|" + s.color).join(";");
      if (keys === this._legendKeys) {
        for (const b of this.legendEl.children) b.setAttribute("aria-pressed", String(!this.hidden.has(b.dataset.key)));
        return;
      }
      this._legendKeys = keys;
      this.legendEl.replaceChildren();
      if (this.series.length < 2) return;   // one series: the subtitle names it
      for (const s of this.series) {
        const b = el("button", "legend-item");
        b.type = "button";
        b.dataset.key = s.key;
        b.setAttribute("aria-pressed", String(!this.hidden.has(s.key)));
        const k = el("span", "line-key");
        k.style.background = `var(${s.color})`;
        b.append(k, el("span", null, s.label));
        b.addEventListener("click", () => {
          if (this.hidden.has(s.key)) this.hidden.delete(s.key); else this.hidden.add(s.key);
          this._legend();
          this.render();
          if (!this.tableEl.hidden) this._table();
        });
        this.legendEl.append(b);
      }
    }

    _visible() { return this.series.filter((s) => !this.hidden.has(s.key)); }

    _range() {
      const i0 = lowerBound(this.times, this.t0);
      return [i0, this.times.length];
    }

    _yScale(vis, i0, i1) {
      let min = Infinity, max = -Infinity;
      for (const s of vis) {
        for (let i = i0; i < i1; i++) {
          const v = s.points[i];
          if (v == null) continue;
          if (v < min) min = v;
          if (v > max) max = v;
        }
      }
      const o = this.opts;
      const ex = this.extra || {};
      if (!isFinite(min)) { min = 0; max = 1; }
      if (o.zero) min = Math.min(0, min);
      if (o.yMin != null) min = Math.min(o.yMin, min);
      if (o.yMax != null) max = Math.max(o.yMax, max);
      if (ex.yMax != null) max = Math.max(ex.yMax, max);
      return niceScale(min, max, 4);
    }

    render() {
      const canvas = this.canvas;
      const w = canvas.clientWidth;
      const h = this.opts.height;
      if (!w) return;
      const dpr = global.devicePixelRatio || 1;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
      }
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const cs = getComputedStyle(this.root);
      const col = (name) => cs.getPropertyValue(name).trim();
      const surface = col("--surface"), grid = col("--grid"), axis = col("--axis");
      const muted = col("--muted"), ink2 = col("--ink-2");
      const font = "11px " + (col("--sans") || "system-ui");

      const vis = this._visible();
      const [i0, i1] = this._range();
      const hasData = vis.some((s) => { for (let i = i0; i < i1; i++) if (s.points[i] != null) return true; return false; });
      this.emptyEl.hidden = hasData;

      const ys = this._yScale(vis, i0, i1);
      ctx.font = font;
      const fmtY = (v) => fmtTick(v, ys.step);
      let left = 0;
      for (const t of ys.ticks) left = Math.max(left, ctx.measureText(fmtY(t)).width);
      left = Math.ceil(left) + 10;

      // direct end labels (value at the end of each line) when they fit
      const ends = [];
      for (const s of vis) {
        for (let i = i1 - 1; i >= i0; i--) {
          if (s.points[i] != null) { ends.push({ s, i, v: s.points[i] }); break; }
        }
      }
      const fmtV = this.opts.fmt;
      let right = 12;
      let labelEnds = ends.length > 0 && ends.length <= 4;
      if (labelEnds) {
        let lw = 0;
        for (const e of ends) lw = Math.max(lw, ctx.measureText(fmtV(e.v)).width);
        right = Math.ceil(lw) + 18;
      }
      const top = 8, bottom = 22;
      const pw = Math.max(10, w - left - right), ph = h - top - bottom;
      const X = (t) => left + ((t - this.t0) / (this.t1 - this.t0 || 1)) * pw;
      const Y = (v) => top + ph - ((v - ys.lo) / (ys.hi - ys.lo || 1)) * ph;
      this._geom = { left, right, top, bottom, pw, ph, X, Y, i0, i1 };

      // grid + y labels
      ctx.lineWidth = 1;
      ctx.textBaseline = "middle";
      ctx.textAlign = "right";
      for (const t of ys.ticks) {
        const y = Math.round(Y(t)) + 0.5;
        ctx.strokeStyle = t === 0 ? axis : grid;
        ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(left + pw, y); ctx.stroke();
        ctx.fillStyle = muted;
        ctx.fillText(fmtY(t), left - 8, y);
      }
      // x axis + time labels
      const span = this.t1 - this.t0;
      let step = TIME_STEPS[TIME_STEPS.length - 1];
      const maxLabels = Math.max(2, Math.floor(pw / 70));
      for (const s of TIME_STEPS) { if (span / s <= maxLabels) { step = s; break; } }
      ctx.strokeStyle = axis;
      ctx.beginPath(); ctx.moveTo(left, top + ph + 0.5); ctx.lineTo(left + pw, top + ph + 0.5); ctx.stroke();
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillStyle = muted;
      const first = Math.ceil(this.t0 / step) * step;
      for (let t = first; t <= this.t1; t += step) {
        const x = X(t);
        if (x < left + 12 || x > left + pw - 12) continue;
        ctx.fillText(fmtClock(t, step < 60), x, top + ph + 6);
      }

      // lines
      ctx.save();
      ctx.beginPath();
      ctx.rect(left, top - 4, pw, ph + 8);
      ctx.clip();
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      const gap = this.opts.maxGap;
      for (const s of vis) {
        ctx.strokeStyle = col(s.color);
        ctx.beginPath();
        let pen = false, lastT = null;
        for (let i = i0; i < i1; i++) {
          const v = s.points[i];
          const t = this.times[i];
          if (v == null || (lastT != null && t - lastT > gap)) { pen = false; }
          if (v == null) { lastT = null; continue; }
          const x = X(t), y = Y(v);
          if (!pen) { ctx.moveTo(x, y); pen = true; } else ctx.lineTo(x, y);
          lastT = t;
        }
        ctx.stroke();
      }
      ctx.restore();

      // end dots (surface ring) and end labels
      for (const e of ends) this._dot(ctx, X(this.times[e.i]), Y(e.v), col(e.s.color), surface);
      if (labelEnds) {
        const pos = ends.map((e) => ({ e, y: Y(e.v) })).sort((a, b) => a.y - b.y);
        for (let k = 1; k < pos.length; k++) if (pos[k].y - pos[k - 1].y < 13) labelEnds = false;
        if (labelEnds) {
          ctx.textAlign = "left";
          ctx.textBaseline = "middle";
          ctx.fillStyle = ink2;
          for (const p of pos) ctx.fillText(fmtV(p.e.v), Math.min(X(this.times[p.e.i]) + 9, w - right + 9), p.y);
        }
      }

      // crosshair
      if (this.hoverIdx != null && this.hoverIdx >= i0 && this.hoverIdx < i1) {
        const t = this.times[this.hoverIdx];
        const x = Math.round(X(t)) + 0.5;
        ctx.strokeStyle = axis;
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + ph); ctx.stroke();
        for (const s of vis) {
          const v = s.points[this.hoverIdx];
          if (v != null) this._dot(ctx, X(t), Y(v), col(s.color), surface);
        }
        this._tooltip(vis, t, x);
      } else {
        this.tip.hidden = true;
      }
    }

    _dot(ctx, x, y, color, surface) {
      ctx.beginPath(); ctx.arc(x, y, 6, 0, Math.PI * 2); ctx.fillStyle = surface; ctx.fill();
      ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
    }

    _tooltip(vis, t, x) {
      const tip = this.tip;
      tip.replaceChildren(el("div", "tip-time", fmtClock(t, true)));
      const rows = vis.map((s) => ({ s, v: s.points[this.hoverIdx] }))
        .sort((a, b) => (b.v ?? -Infinity) - (a.v ?? -Infinity));
      for (const r of rows) {
        const row = el("div", "tip-row");
        const k = el("span", "line-key");
        k.style.background = `var(${r.s.color})`;
        row.append(k, el("span", "tip-val", r.v == null ? "—" : this.opts.fmt(r.v)), el("span", "tip-lab", r.s.label));
        tip.append(row);
      }
      tip.hidden = false;
      const bw = this.canvas.clientWidth;
      const tw = tip.offsetWidth;
      let left = x + 12;
      if (left + tw > bw) left = x - tw - 12;
      tip.style.left = Math.max(0, left) + "px";
      tip.style.top = "4px";
    }

    _nearest(px) {
      const g = this._geom;
      if (!g || g.i1 <= g.i0) return null;
      const t = this.t0 + ((px - g.left) / g.pw) * (this.t1 - this.t0);
      let i = lowerBound(this.times, t);
      if (i >= g.i1) i = g.i1 - 1;
      if (i > g.i0 && Math.abs(this.times[i - 1] - t) < Math.abs(this.times[i] - t)) i--;
      return Math.max(g.i0, i);
    }

    _onMove(e) {
      const rect = this.canvas.getBoundingClientRect();
      const idx = this._nearest(e.clientX - rect.left);
      if (idx !== this.hoverIdx) { this.hoverIdx = idx; this.render(); }
    }

    _onKey(e) {
      const g = this._geom;
      if (!g || g.i1 <= g.i0) return;
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        if (this.hoverIdx == null) this.hoverIdx = g.i1 - 1;
        else this.hoverIdx = Math.min(g.i1 - 1, Math.max(g.i0, this.hoverIdx + (e.key === "ArrowLeft" ? -1 : 1)));
        this.render();
      } else if (e.key === "Escape") {
        this.hoverIdx = null;
        this.render();
      }
    }

    _table() {
      const vis = this._visible();
      const [i0, i1] = this._range();
      const table = el("table", "data");
      const head = el("tr");
      head.append(el("th", "left", "Time"));
      for (const s of vis) head.append(el("th", null, s.label));
      const thead = el("thead"); thead.append(head);
      const tbody = el("tbody");
      for (let i = i1 - 1; i >= Math.max(i0, i1 - 60); i--) {
        const tr = el("tr");
        tr.append(el("td", "left", fmtClock(this.times[i], true)));
        for (const s of vis) {
          const v = s.points[i];
          tr.append(el("td", v == null ? "na" : null, v == null ? "—" : this.opts.fmt(v)));
        }
        tbody.append(tr);
      }
      table.append(thead, tbody);
      this.tableEl.replaceChildren(table);
    }
  }

  global.LineChart = LineChart;
  global.fmtClock = fmtClock;
})(window);
