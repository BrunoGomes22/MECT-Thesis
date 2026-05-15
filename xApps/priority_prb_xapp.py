#!/usr/bin/env python3

"""
Minimal priority-aware PRB xApp for srsRAN OSC RIC.

What it does:
1. KPM Style 1: reads DU/cell PRB load.
2. KPM Style 4: discovers connected UE IDs and reads DL throughput.
3. If DL PRB load is below the congestion threshold, all discovered UEs get max_prb=100%.
4. If DL PRB load is above the congestion threshold:
   - high-priority UEs get max_prb=100%
   - medium-priority UEs get max_prb=70%
   - best-effort UEs get max_prb=40%
5. Sends E2SM-RC Slice-Level PRB Quota control per UE, but only if --enable_control is set.

Example:
  docker compose exec python_xapp_runner ./priority_prb_xapp.py \
    --e2_node_id gnbd_001_001_00019b_0 \
    --http_server_port 8092 \
    --rmr_port 4562 \
    --best_effort_ues 0 \
    --medium_ues 1 \
    --high_ues 2 \
    --enable_control
"""

import argparse
import signal
import time

from lib.xAppBase import xAppBase


STYLE1_PRB_METRICS = [
    "RRU.PrbAvailDl",
    "RRU.PrbUsedDl",
    "RRU.PrbTotDl",
]

STYLE4_UE_METRICS = [
    "DRB.UEThpDl",
]


def parse_ue_list(text):
    if text is None or text.strip() == "":
        return set()
    return set(int(x.strip()) for x in text.split(",") if x.strip() != "")


def first_number(value):
    if isinstance(value, list):
        if not value:
            return None
        value = value[0]
    try:
        return float(value)
    except Exception:
        return None


def get_metric(meas_data, name):
    metrics = meas_data.get("measData", {})
    return first_number(metrics.get(name))


class PriorityPrbXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port, args):
        super().__init__(config, http_server_port, rmr_port)
        self.args = args

        self.high_ues = parse_ue_list(args.high_ues)
        self.medium_ues = parse_ue_list(args.medium_ues)
        self.best_effort_ues = parse_ue_list(args.best_effort_ues)

        self.known_ues = set()
        self.ue_dl_thp = {}
        self.dl_load = None

        self.current_cap = {}
        self.last_control_time = {}
        self.last_print_time = 0.0

    def is_active_ue(self, ue_id): # consider only UEs with non-zero DL throughput as active
        thp = self.ue_dl_thp.get(ue_id, 0.0)
        return thp is not None and thp >= self.args.active_dl_threshold

    def compute_dl_load(self, used_dl, avail_dl, total_dl):
        # prefer RRU.PrbTotDl because it is already a percentage
        if total_dl is not None:
            return max(0.0, min(100.0, total_dl))

        # fallback if PrbTotDl is not available
        if used_dl is not None and avail_dl is not None and used_dl + avail_dl > 0:
            return max(0.0, min(100.0, 100.0 * used_dl / (used_dl + avail_dl)))

        return None

    def priority_of_ue(self, ue_id):
        if ue_id in self.high_ues:
            return "high"
        if ue_id in self.medium_ues:
            return "medium"
        if ue_id in self.best_effort_ues:
            return "best_effort"
        return self.args.default_priority

    def target_cap_for_priority(self, priority):
        if self.dl_load is None:
            return 100

        # relax constraints when there is no congestion
        if self.dl_load < self.args.congested_threshold:
            return 100

        overload = self.dl_load - self.args.congested_threshold

        # apply differentiated treatment when congestion is detected
        if priority == "high":
            #return self.args.high_cap
            return 100
        if priority == "medium":
            #return self.args.medium_cap
            return int(max(60, 85 - overload * 0.5)) # each percentage point of overload above the threshold reduces medium cap by 0.5 percentage points

        #return self.args.best_effort_cap
        return int(max(30, 70 - overload)) # each percentage point of overload above the threshold reduces best-effort cap by 1 percentage point

    def maybe_send_control(self, e2_agent_id, ue_id, target_cap):
        if not self.args.enable_control:
            return

        now = time.time()

        if self.current_cap.get(ue_id) == target_cap:
            return

        if now - self.last_control_time.get(ue_id, 0.0) < self.args.control_holdoff_seconds:
            return

        print(
            f"RC CONTROL: UE {ue_id} -> min={self.args.min_prb}%, "
            f"max={target_cap}%, dedicated={self.args.dedicated_prb}%"
        )

        self.e2sm_rc.control_slice_level_prb_quota(
            e2_agent_id,
            ue_id,
            min_prb_ratio=self.args.min_prb,
            max_prb_ratio=target_cap,
            dedicated_prb_ratio=self.args.dedicated_prb,
            ack_request=1,
        )

        self.current_cap[ue_id] = target_cap
        self.last_control_time[ue_id] = now

    def apply_policy(self, e2_agent_id):
        if self.dl_load is None:
            return

        for ue_id in sorted(self.known_ues):
            if not self.is_active_ue(ue_id):
                if self.current_cap.get(ue_id) != 100:
                    self.maybe_send_control(e2_agent_id, ue_id, 100)
                continue
            
            priority = self.priority_of_ue(ue_id)
            target_cap = self.target_cap_for_priority(priority)

            self.maybe_send_control(e2_agent_id, ue_id, target_cap)

    def print_status(self):
        now = time.time()
        if now - self.last_print_time < self.args.print_period_seconds:
            return
        self.last_print_time = now

        print("\n--- Priority PRB xApp status ---")
        print(f"DL PRB load: {self.dl_load}")
        print(f"Congestion threshold: {self.args.congested_threshold}%")
        print(f"Control enabled: {self.args.enable_control}")

        if not self.known_ues:
            print("No UEs discovered yet from Style 4.")
        else:
            for ue_id in sorted(self.known_ues):
                priority = self.priority_of_ue(ue_id)
                target_cap = self.target_cap_for_priority(priority)
                thp = self.ue_dl_thp.get(ue_id, None)
                print(
                    f"UE {ue_id}: priority={priority}, "
                    f"DL_thp={thp}, target_max_prb={target_cap}%, "
                    f"current_sent_cap={self.current_cap.get(ue_id)}"
                )

        if self.dl_load is not None and self.dl_load >= self.args.congested_threshold:
            print("Decision: CONGESTED -> priority caps active")
        else:
            print("Decision: NOT CONGESTED -> all caps relaxed to 100%")

    def style1_prb_callback(self, e2_agent_id, subscription_id, indication_hdr, indication_msg):
        try:
            meas_data = self.e2sm_kpm.extract_meas_data(indication_msg)

            used_dl = get_metric(meas_data, "RRU.PrbUsedDl")
            avail_dl = get_metric(meas_data, "RRU.PrbAvailDl")
            total_dl = get_metric(meas_data, "RRU.PrbTotDl")

            self.dl_load = self.compute_dl_load(used_dl, avail_dl, total_dl)

            self.print_status()
            self.apply_policy(e2_agent_id)

        except Exception as exc:
            print(f"ERROR in Style 1 PRB callback: {exc}")

    def style4_ue_callback(self, e2_agent_id, subscription_id, indication_hdr, indication_msg):
        try:
            meas_data = self.e2sm_kpm.extract_meas_data(indication_msg)

            ue_meas_data = meas_data.get("ueMeasData", {})
            for ue_id_text, ue_data in ue_meas_data.items():
                ue_id = int(ue_id_text)
                self.known_ues.add(ue_id)

                metrics = ue_data.get("measData", {})
                self.ue_dl_thp[ue_id] = first_number(metrics.get("DRB.UEThpDl"))

            self.print_status()
            self.apply_policy(e2_agent_id)

        except Exception as exc:
            print(f"ERROR in Style 4 UE callback: {exc}")

    @xAppBase.start_function
    def start(self, e2_node_id):
        print("Starting priority-aware PRB xApp")
        print(f"E2 node: {e2_node_id}")
        print(f"High priority UEs: {sorted(self.high_ues)}")
        print(f"Medium priority UEs: {sorted(self.medium_ues)}")
        print(f"Best-effort UEs: {sorted(self.best_effort_ues)}")
        print(f"Default priority for unmapped UEs: {self.args.default_priority}")

        # Subscription 1: cell-level PRB load.
        self.e2sm_kpm.subscribe_report_service_style_1(
            e2_node_id,
            self.args.report_period_ms,
            STYLE1_PRB_METRICS,
            self.args.granul_period_ms,
            self.style1_prb_callback,
        )

        # Subscription 2: UE discovery + per-UE throughput.
        matching_ue_conds = [
            {
                "testCondInfo": {
                    "testType": ("ul-rSRP", "true"),
                    "testExpr": "lessthan",
                    "testValue": ("valueInt", 1000),
                }
            }
        ]

        self.e2sm_kpm.subscribe_report_service_style_4(
            e2_node_id,
            self.args.report_period_ms,
            matching_ue_conds,
            STYLE4_UE_METRICS,
            self.args.granul_period_ms,
            self.style4_ue_callback,
        )


def parse_args():
    parser = argparse.ArgumentParser(description="Priority-aware DU PRB xApp")

    parser.add_argument("--config", type=str, default="")
    parser.add_argument("--http_server_port", type=int, default=8092)
    parser.add_argument("--rmr_port", type=int, default=4562)
    parser.add_argument("--e2_node_id", type=str, required=True)

    parser.add_argument("--report_period_ms", type=int, default=1000)
    parser.add_argument("--granul_period_ms", type=int, default=1000)
    parser.add_argument("--print_period_seconds", type=float, default=1.0)

    parser.add_argument("--congested_threshold", type=float, default=70.0)
    parser.add_argument("--active_dl_threshold", type=float, default=500.0) # 500 kbps

    parser.add_argument("--best_effort_ues", type=str, default="")
    parser.add_argument("--medium_ues", type=str, default="")
    parser.add_argument("--high_ues", type=str, default="")
    parser.add_argument(
        "--default_priority",
        type=str,
        default="best_effort",
        choices=["best_effort", "medium", "high"],
    )

    #parser.add_argument("--best_effort_cap", type=int, default=40)
    #parser.add_argument("--medium_cap", type=int, default=70)
    #parser.add_argument("--high_cap", type=int, default=100)

    parser.add_argument("--min_prb", type=int, default=10)
    parser.add_argument("--dedicated_prb", type=int, default=100)
    parser.add_argument("--control_holdoff_seconds", type=float, default=5.0)
    parser.add_argument("--enable_control", action="store_true")
    parser.add_argument("--rc_ran_func_id", type=int, default=3)

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    xapp = PriorityPrbXapp(args.config, args.http_server_port, args.rmr_port, args)
    xapp.e2sm_kpm.set_ran_func_id(2)
    xapp.e2sm_rc.set_ran_func_id(args.rc_ran_func_id)

    signal.signal(signal.SIGQUIT, xapp.signal_handler)
    signal.signal(signal.SIGTERM, xapp.signal_handler)
    signal.signal(signal.SIGINT, xapp.signal_handler)

    xapp.start(args.e2_node_id)
