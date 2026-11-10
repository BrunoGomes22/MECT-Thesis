#!/usr/bin/env python3
"""Metrics exporter for the srsRAN setup: reads the DU's JSON metrics from its
WebSocket (remote_control, {"cmd": "metrics_subscribe"}) and prints the same
JSON lines as the FlexRIC exporter xApp of the OAI setup, so the dashboard's
MetricsStore and charts work unchanged:

  {"t": "mac", "ts": <us>, "ues": [{rnti, slice, sst, dl_mcs, ul_mcs, wb_cqi,
        pusch_snr, pucch_snr, dl_bler, ul_bler, bsr, phr, dl_tbs, ul_tbs}]}
  {"t": "rlc", "ts": <us>, "rbs": [{rnti, tx_buf, rx_buf, tx_dd, tx_sdu, rx_sdu,
        tx_retx, sojourn_us, hol_us}]}
  {"t": "nodes", "nodes": [{"type_name": "gNB-DU", "id": ...}]}   from the OSC RIC e2mgr
  {"t": "hb", "mac_subs": 0|1}                                     every second

Differences with the OAI exporter:
  - dl_tbs/ul_tbs are cumulative bytes integrated from the DU's per-period bit
    rates (the store derives Mbit/s from their differences);
  - no per-UE PRB counters (the DU JSON has none): the PRB charts stay empty;
  - sojourn_us is the queueing delay estimated as DL buffer / DL rate (the
    DU's summed RLC SDU latency counter overflows under load);
  - tx_dd counts dropped + discarded RLC SDUs times their mean size;
  - slice comes from the static UE -> slice map (DU UE index = F1AP ID).

Environment: SRSRAN_WS (ws://127.0.0.1:8001), E2MGR_URL
(http://10.0.2.11:3800), SLICE_MAP (path of ue_slice_map.json),
SLICE_INDEX (SD -> slice index, default "ffffff=0,000001=1,000002=2").
"""
import json
import os
import signal
import sys
import time
import urllib.request
from datetime import datetime

import websocket

WS_URL = os.environ.get("SRSRAN_WS", "ws://127.0.0.1:8001")
E2MGR_URL = os.environ.get("E2MGR_URL", "http://10.0.2.11:3800")
SLICE_MAP = os.environ.get("SLICE_MAP", "")
SLICE_INDEX = dict(kv.split("=") for kv in
                   os.environ.get("SLICE_INDEX", "ffffff=0,000001=1,000002=2").split(","))
NODES_EVERY_S = 5.0

running = True


def stop(*_):
    global running
    running = False


def emit(obj):
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def log(msg):
    sys.stderr.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    sys.stderr.flush()


def load_slice_map():
    """DU UE index -> (sst, slice index)."""
    try:
        with open(SLICE_MAP) as f:
            ues = json.load(f)["ues"]
    except (OSError, ValueError, KeyError):
        return {}
    return {int(u["gnb_cu_ue_f1ap_id"]): (int(u["sst"]), int(SLICE_INDEX.get(str(u["sd"]).lower(), -1)))
            for u in ues}


def e2_nodes():
    try:
        with urllib.request.urlopen(f"{E2MGR_URL}/v1/nodeb/states", timeout=2) as r:
            states = json.loads(r.read().decode())
    except (OSError, ValueError):
        return None
    nodes = []
    for n in states or []:
        if n.get("connectionStatus") != "CONNECTED":
            continue
        name = n.get("inventoryName", "")
        kind = "gNB-DU" if name.startswith("gnbd") else ("gNB-CU" if name.startswith("gnb_cu") else "gNB")
        nodes.append({"type_name": kind, "id": name})
    return nodes


def du_us(ts):
    """DU timestamp '2026-11-10T16:18:00.025' -> microseconds."""
    try:
        return int(datetime.fromisoformat(ts).timestamp() * 1e6)
    except (TypeError, ValueError):
        return int(time.time() * 1e6)


class State:
    def __init__(self):
        self.cum = {}         # ue index -> {"dl": bytes, "ul": bytes, "dd": bytes, "last_us": us}
        self.rlc = {}         # ue index -> {"dd_sdus": n, "sdus": n, "bytes": n, "rx_sdus": n}
        self.rnti = {}        # ue index -> rnti
        self.last_cells = 0.0


def handle_cells(msg, st, slices):
    ts = du_us(msg.get("timestamp"))
    ues, rbs = [], []
    for cell in msg.get("cells", []):
        for ue in cell.get("ue_list", []):
            idx = ue.get("ue")
            rnti = ue.get("rnti")
            if idx is None or rnti is None:
                continue
            st.rnti[idx] = rnti
            c = st.cum.setdefault(idx, {"dl": 0.0, "ul": 0.0, "dd": 0.0, "last_us": ts})
            dt = max(0.0, (ts - c["last_us"]) / 1e6) or 1.0
            if ts != c["last_us"]:
                c["dl"] += (ue.get("dl_brate") or 0.0) / 8 * dt
                c["ul"] += (ue.get("ul_brate") or 0.0) / 8 * dt
            c["last_us"] = ts
            sst, slc = slices.get(idx, (None, None))
            dl_ok, dl_nok = ue.get("dl_nof_ok") or 0, ue.get("dl_nof_nok") or 0
            ul_ok, ul_nok = ue.get("ul_nof_ok") or 0, ue.get("ul_nof_nok") or 0
            ues.append({
                "rnti": rnti, "ue_index": idx,
                "slice": slc if slc is not None and slc >= 0 else None, "sst": sst,
                "dl_mcs": ue.get("dl_mcs"), "ul_mcs": ue.get("ul_mcs"),
                "wb_cqi": ue.get("cqi"),
                "pusch_snr": ue.get("pusch_snr_db"), "pucch_snr": ue.get("pucch_snr_db"),
                "dl_bler": dl_nok / (dl_ok + dl_nok) if dl_ok + dl_nok else 0.0,
                "ul_bler": ul_nok / (ul_ok + ul_nok) if ul_ok + ul_nok else 0.0,
                "bsr": ue.get("bsr"), "phr": ue.get("last_phr"),
                "dl_tbs": int(c["dl"]), "ul_tbs": int(c["ul"]),
            })
            buf = ue.get("dl_bs") or 0
            rate = ue.get("dl_brate") or 0.0
            r = st.rlc.get(idx, {})
            mean_sdu = (r.get("bytes", 0) / r["sdus"]) if r.get("sdus") else 1400.0
            rbs.append({
                "rnti": rnti, "tx_buf": buf, "rx_buf": 0,
                "tx_dd": int(r.get("dd_sdus", 0) * mean_sdu),
                "tx_sdu": r.get("sdus", 0), "rx_sdu": r.get("rx_sdus", 0), "tx_retx": 0,
                "sojourn_us": buf * 8 / rate * 1e6 if rate > 0 else (0.0 if buf == 0 else None),
                "hol_us": 0,
            })
    emit({"t": "mac", "ts": ts, "ues": ues})
    emit({"t": "rlc", "ts": ts, "rbs": rbs})
    st.last_cells = time.time()


def handle_rlc(msg, st):
    r = msg.get("rlc_metrics") or {}
    idx = r.get("ue_id")
    if idx is None:
        return
    tx, rx = r.get("tx") or {}, r.get("rx") or {}
    a = st.rlc.setdefault(idx, {"dd_sdus": 0, "sdus": 0, "bytes": 0, "rx_sdus": 0})
    a["dd_sdus"] += (tx.get("num_dropped_sdus") or 0) + (tx.get("num_discarded_sdus") or 0)
    a["sdus"] += tx.get("num_sdus") or 0
    a["bytes"] += tx.get("num_sdu_bytes") or 0
    a["rx_sdus"] += rx.get("num_sdus") or 0


def main():
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    st = State()
    slices = load_slice_map()
    slices_ts = time.time()
    ws = None
    last_hb = last_nodes = 0.0
    log(f"srsran exporter: {WS_URL}, e2mgr {E2MGR_URL}, slice map {SLICE_MAP or '-'}")
    while running:
        now = time.time()
        if now - last_hb >= 1.0:
            emit({"t": "hb", "mac_subs": 1 if ws is not None and now - st.last_cells < 5 else 0})
            last_hb = now
        if now - last_nodes >= NODES_EVERY_S:
            nodes = e2_nodes()
            if nodes is not None:
                emit({"t": "nodes", "nodes": nodes})
            last_nodes = now
        if now - slices_ts > 10:
            slices, slices_ts = load_slice_map(), now
        if ws is None:
            try:
                ws = websocket.create_connection(WS_URL, timeout=1)
                ws.send(json.dumps({"cmd": "metrics_subscribe"}))
                log("connected to the DU metrics WebSocket")
            except (OSError, websocket.WebSocketException):
                ws = None
                time.sleep(1)
                continue
        try:
            msg = json.loads(ws.recv())
        except websocket.WebSocketTimeoutException:
            continue
        except (OSError, ValueError, websocket.WebSocketException) as e:
            log(f"WebSocket closed ({e}); reconnecting")
            try:
                ws.close()
            except Exception:
                pass
            ws = None
            st = State()
            time.sleep(1)
            continue
        if "cells" in msg:
            handle_cells(msg, st, slices)
        elif "rlc_metrics" in msg:
            handle_rlc(msg, st)
    if ws is not None:
        ws.close()


if __name__ == "__main__":
    main()
