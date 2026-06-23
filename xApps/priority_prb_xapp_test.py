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
import csv
import signal
import time
import random
import json
import os
import urllib.request

from lib.xAppBase import xAppBase


STYLE1_PRB_METRICS = [
    "RRU.PrbAvailDl",
    "RRU.PrbUsedDl",
    "RRU.PrbTotDl",
]

STYLE4_UE_METRICS = [
    "DRB.UEThpDl",
]

ACTION_PROFILES = { # action profiles for contextual bandit
    0: {"high": 100, "medium": 100, "best_effort": 100},  # relax
    1: {"high": 100, "medium": 90,  "best_effort": 80},   # mild
    2: {"high": 100, "medium": 80,  "best_effort": 60},   # moderate
    3: {"high": 100, "medium": 70,  "best_effort": 40},   # strong
    4: {"high": 100, "medium": 60,  "best_effort": 30},   # severe
}

VALID_PRIORITIES = {"best_effort", "medium", "high"}

def normalize_sd(sd):
    if sd is None:
        return "ffffff"

    sd = str(sd).lower().replace("0x", "")
    return sd.zfill(6)[-6:]


def slice_key(sst, sd):
    return f"{int(sst)}-{normalize_sd(sd)}"


def parse_slice_priority_map(text):
    """
    Example:
      1-000000=best_effort,1-000001=medium,1-000002=high
    """
    result = {}

    if text is None or text.strip() == "":
        return result

    for item in text.split(","):
        item = item.strip()
        if not item:
            continue

        if "=" not in item:
            raise ValueError(f"Invalid slice priority item: {item}")

        key, priority = item.split("=", 1)
        key = key.strip().lower()
        priority = priority.strip()

        if priority not in VALID_PRIORITIES:
            raise ValueError(f"Invalid priority '{priority}' for slice '{key}'")

        result[key] = priority

    return result

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
        self.dl_load = None # raw DL load
        self.smoothed_dl_load = None # smoothed DL load using EMA 

        self.current_cap = {}
        self.last_control_time = {}
        self.last_print_time = 0.0

        self.congestion_active = False

        self.csv_file = None
        self.csv_writer = None

        self.bandit_q = {}
        self.bandit_n = {}
        self.last_bandit_context = None
        self.last_bandit_action = None
        self.last_bandit_reward_time = 0.0

        self.ue_slice_map = {}
        self.last_slice_map_load = 0.0
        self.slice_priority_map = parse_slice_priority_map(args.slice_priority_map)

        if self.args.csv_log_file:
            self.csv_file = open(self.args.csv_log_file, "w", newline="", buffering=1)

            self.csv_writer = csv.DictWriter(
                self.csv_file,
                fieldnames=[
                    "timestamp",
                    "trigger",
                    "e2_node_id",
                    "ue_id",
                    "priority",
                    "is_active",
                    "active_ue_count",
                    "ue_dl_thp",
                    "dl_load_raw",
                    "dl_load_smoothed",
                    "congestion_active",
                    "target_cap",
                    "previous_cap",
                    "control_enabled",
                    "control_sent",
                ],
            )

            self.csv_writer.writeheader()

    def get_context(self):
        load = self.smoothed_dl_load or 0.0
        active_count = self.active_ue_count()

        if load < 60:
            load_bucket = "low"
        elif load < 75:
            load_bucket = "medium"
        elif load < 90:
            load_bucket = "high"
        else:
            load_bucket = "critical"

        if active_count <= 1:
            active_bucket = "one"
        elif active_count == 2:
            active_bucket = "two"
        else:
            active_bucket = "three_plus"

        return (load_bucket, active_bucket)

    def compute_reward(self):
        reward = 0.0

        weights = {
            "high": 3.0,
            "medium": 2.0,
            "best_effort": 1.0,
        }

        for ue_id in self.active_ues():
            priority = self.priority_of_ue(ue_id)
            thp = self.ue_dl_thp.get(ue_id, 0.0) or 0.0
            reward += weights[priority] * thp

        # penalize very high PRB load
        load = self.smoothed_dl_load or 0.0
        if load > 90:
            reward -= 10000
        elif load > 80:
            reward -= 5000

        return reward
    
    def select_bandit_action(self, context):
        epsilon = self.args.bandit_epsilon

        # exploration
        if random.random() < epsilon:
            return random.choice(list(ACTION_PROFILES.keys()))

        # exploitation
        best_action = None
        best_value = None

        for action_id in ACTION_PROFILES.keys():
            value = self.bandit_q.get((context, action_id), 0.0)

            if best_value is None or value > best_value:
                best_value = value
                best_action = action_id

        return best_action

    def update_bandit(self):
        if self.last_bandit_context is None or self.last_bandit_action is None:
            return False

        now = time.time()
        if now - self.last_bandit_reward_time < self.args.bandit_reward_delay:
            return False

        reward = self.compute_reward()

        key = (self.last_bandit_context, self.last_bandit_action)

        old_n = self.bandit_n.get(key, 0)
        old_q = self.bandit_q.get(key, 0.0)

        new_n = old_n + 1
        new_q = old_q + (reward - old_q) / new_n

        self.bandit_n[key] = new_n
        self.bandit_q[key] = new_q

        print(
            f"BANDIT UPDATE: context={key[0]}, action={key[1]}, "
            f"reward={reward}, q={new_q}, n={new_n}"
        )   

        # clear the pending action after it has been evaluated.
        self.last_bandit_context = None
        self.last_bandit_action = None
        self.last_bandit_reward_time = 0.0

        return True

    def get_bandit_action_for_cycle(self):
        # evaluate the previous action if enough time has passed
        self.update_bandit()

        # if there is no congestion use the relaxed profile
        if not self.congestion_active:
            return 0

        # if there is only one active UE, no need for priority differentiation
        if self.active_ue_count() <= 1:
            return 0

        # if an action is already pending reward evaluation keep using it
        if self.last_bandit_action is not None:
            return self.last_bandit_action

        # otherwise choose a new action for the current context
        context = self.get_context()
        action_id = self.select_bandit_action(context)

        self.last_bandit_context = context
        self.last_bandit_action = action_id
        self.last_bandit_reward_time = time.time()

        print(f"BANDIT SELECT: context={context}, action={action_id}, profile={ACTION_PROFILES[action_id]}")

        return action_id
    '''
    def target_cap_for_priority_bandit(self, priority):
        if not self.congestion_active:
            return 100

        if self.active_ue_count() <= 1:
            return 100

        context = self.get_context()

        self.update_bandit()

        action_id = self.select_bandit_action(context)
        profile = ACTION_PROFILES[action_id]

        self.last_bandit_context = context
        self.last_bandit_action = action_id
        self.last_bandit_reward_time = time.time()

        return profile[priority]
    '''

    def log_decision(self, trigger, e2_agent_id, ue_id, target_cap, previous_cap, control_sent):
        if self.csv_writer is None:
            return

        priority = self.priority_of_ue(ue_id)
        thp = self.ue_dl_thp.get(ue_id, None)

        self.csv_writer.writerow(
            {
                "timestamp": time.time(),
                "trigger": trigger,
                "e2_node_id": e2_agent_id,
                "ue_id": ue_id,
                "priority": priority,
                "is_active": self.is_active_ue(ue_id),
                "active_ue_count": self.active_ue_count(),
                "ue_dl_thp": thp,
                "dl_load_raw": self.dl_load,
                "dl_load_smoothed": self.smoothed_dl_load,
                "congestion_active": self.congestion_active,
                "target_cap": target_cap,
                "previous_cap": previous_cap,
                "control_enabled": self.args.enable_control,
                "control_sent": control_sent,
            }
        )

    def update_congestion_state(self): # hysteresis to avoid flapping around the congestion threshold
        if self.smoothed_dl_load is None:
            return

        if not self.congestion_active and self.smoothed_dl_load >= self.args.congestion_enter_threshold:
            self.congestion_active = True

        elif self.congestion_active and self.smoothed_dl_load <= self.args.congestion_exit_threshold:
            self.congestion_active = False

    def is_active_ue(self, ue_id): # consider only UEs with non-zero DL throughput as active
        thp = self.ue_dl_thp.get(ue_id, 0.0)
        return thp is not None and thp >= self.args.active_dl_threshold

    def active_ues(self): # get the list of currently active UEs based on DL throughput
        return [ue_id for ue_id in self.known_ues if self.is_active_ue(ue_id)]

    def active_ue_count(self): # get the count of currently active UEs based on DL throughput
        return len(self.active_ues())

    def displayed_target_cap_for_ue(self, ue_id): # get the target cap that would be applied to this UE based on current load and priority
        if not self.is_active_ue(ue_id):
            return 100

        priority = self.priority_of_ue(ue_id)

        if self.args.policy_mode == "bandit":
            if not self.congestion_active:
                return 100

            if self.active_ue_count() <= 1:
                return 100
            
            if self.last_bandit_action is None:
                return 100

            return ACTION_PROFILES[self.last_bandit_action][priority]

        return self.target_cap_for_priority(priority, self.active_ue_count())

    def compute_dl_load(self, used_dl, avail_dl, total_dl):
        # prefer RRU.PrbTotDl because it is already a percentage
        if total_dl is not None:
            return max(0.0, min(100.0, total_dl))

        # fallback if PrbTotDl is not available
        if used_dl is not None and avail_dl is not None and used_dl + avail_dl > 0:
            return max(0.0, min(100.0, 100.0 * used_dl / (used_dl + avail_dl)))

        return None

    def update_smoothed_dl_load(self): # update the smoothed DL load using exponential moving average (EMA)
        if self.dl_load is None:
            return

        alpha = self.args.load_smoothing_alpha

        if self.smoothed_dl_load is None:
            self.smoothed_dl_load = self.dl_load
        else:
            self.smoothed_dl_load = alpha * self.dl_load + (1 - alpha) * self.smoothed_dl_load

    def read_slice_map_source(self):
        source = self.args.ue_slice_map_source

        if source is None or source.strip() == "":
            return None

        source = source.strip()

        if source.startswith("http://") or source.startswith("https://"):
            with urllib.request.urlopen(source, timeout=0.5) as response:
                return response.read().decode("utf-8")

        if not os.path.exists(source):
            return None

        with open(source, "r") as f:
            return f.read()


    def reload_ue_slice_map_if_needed(self, force=False):
        if not self.args.ue_slice_map_source:
            return

        now = time.time()

        if not force and now - self.last_slice_map_load < self.args.slice_map_reload_seconds:
            return

        self.last_slice_map_load = now

        try:
            text = self.read_slice_map_source()
            if not text:
                return

            data = json.loads(text)

            new_map = {}

            for item in data.get("ues", []):
                ue_id = int(item["gnb_cu_ue_f1ap_id"])
                key = slice_key(item["sst"], item.get("sd"))
                new_map[ue_id] = key

            self.ue_slice_map = new_map

            if self.args.debug_slice_map:
                print(f"Loaded UE slice map: {self.ue_slice_map}")

        except Exception as exc:
            if self.args.debug_slice_map:
                print(f"ERROR loading UE slice map: {exc}")


    def priority_of_ue(self, ue_id):
        # first try dynamic slice-based priority if slice map source is configured
        self.reload_ue_slice_map_if_needed()

        ue_slice = self.ue_slice_map.get(ue_id)

        if ue_slice is not None:
            return self.slice_priority_map.get(ue_slice, self.args.default_priority)

        # fallback: old manual UE-ID mapping
        if ue_id in self.high_ues:
            return "high"
        if ue_id in self.medium_ues:
            return "medium"
        if ue_id in self.best_effort_ues:
            return "best_effort"
        return self.args.default_priority

    def target_cap_for_priority(self, priority, active_count):
        if self.dl_load is None:
            return 100

        # relax constraints when there is no congestion
        if not self.congestion_active:
            return 100

        if active_count <= 1:
            return 100

        load = self.smoothed_dl_load if self.smoothed_dl_load is not None else self.dl_load
        overload = max(0.0, load - self.args.congestion_enter_threshold)

        # apply differentiated treatment when congestion is detected
        if priority == "high":
            return 100

        # with only 2 active UEs, use less agressive caps
        if active_count == 2:
            if priority == "medium":
                return int(max(70, 90 - overload * 0.25)) # each percentage point of overload above the threshold reduces medium cap by 0.25 percentage points
    
            return int(max(50, 80 - overload * 0.5)) # each percentage point of overload above the threshold reduces best-effort cap by 0.5 percentage points

        # with 3 or more active UEs, use stronger differentiation
        if priority == "medium":
            return int(max(60, 85 - overload * 0.5)) # each percentage point of overload above the threshold reduces medium cap by 0.5 percentage points

        return int(max(30, 70 - overload)) # each percentage point of overload above the threshold reduces best-effort cap by 1 percentage point

    def maybe_send_control(self, e2_agent_id, ue_id, target_cap):
        if not self.args.enable_control:
            return False

        now = time.time()

        previous_cap = self.current_cap.get(ue_id)

        if previous_cap is None: # no previous control sent for this UE, proceed to send control with target_cap
            pass 

        elif previous_cap == target_cap: # target cap is the same as the last one that was sent no need to send again
            return False

        elif abs(previous_cap - target_cap) < self.args.min_cap_change: # target cap is close to the last one that was sent, skip to avoid unnecessary control messages
            return False

        if now - self.last_control_time.get(ue_id, 0.0) < self.args.control_holdoff_seconds:
            return False

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

        return True

    def apply_policy(self, e2_agent_id, trigger):
        if self.dl_load is None:
            return

        active_count = self.active_ue_count()

        bandit_profile = None

        if self.args.policy_mode == "bandit":
            action_id = self.get_bandit_action_for_cycle()
            bandit_profile = ACTION_PROFILES[action_id]

        for ue_id in sorted(self.known_ues):
            previous_cap = self.current_cap.get(ue_id)

            if not self.is_active_ue(ue_id):
                target_cap = 100

                if self.current_cap.get(ue_id) != 100:
                    control_sent = self.maybe_send_control(e2_agent_id, ue_id, target_cap)
                else:
                    control_sent = False

                self.log_decision(
                    trigger,
                    e2_agent_id,
                    ue_id,
                    target_cap,
                    previous_cap,
                    control_sent,
                )
                continue
            
            priority = self.priority_of_ue(ue_id)

            if self.args.policy_mode == "bandit":
                #target_cap = self.target_cap_for_priority_bandit(priority)
                target_cap = bandit_profile[priority]
            else:
                target_cap = self.target_cap_for_priority(priority, active_count)

            control_sent = self.maybe_send_control(e2_agent_id, ue_id, target_cap)

            self.log_decision(
                trigger,
                e2_agent_id,
                ue_id,
                target_cap,
                previous_cap,
                control_sent,
            )

    def print_status(self):
        now = time.time()
        if now - self.last_print_time < self.args.print_period_seconds:
            return
        self.last_print_time = now

        print("\n--- Priority PRB xApp status ---")
        print(f"DL PRB load raw: {self.dl_load}")
        print(f"DL PRB load smoothed: {self.smoothed_dl_load}")
        print(f"Congestion enter threshold: {self.args.congestion_enter_threshold}%")
        print(f"Congestion exit threshold: {self.args.congestion_exit_threshold}%")
        print(f"Congestion active: {self.congestion_active}")
        print(f"Active UEs: {self.active_ue_count()}")
        print(f"Control enabled: {self.args.enable_control}")

        if not self.known_ues:
            print("No UEs discovered yet from Style 4.")
        else:
            for ue_id in sorted(self.known_ues):
                priority = self.priority_of_ue(ue_id)
                ue_slice = self.ue_slice_map.get(ue_id, "unknown")
                #target_cap = self.target_cap_for_priority(priority)
                target_cap = self.displayed_target_cap_for_ue(ue_id)
                thp = self.ue_dl_thp.get(ue_id, None)
                print(
                    f"UE {ue_id}: slice={ue_slice}, priority={priority}, "
                    f"DL_thp={thp}, target_max_prb={target_cap}%, "
                    f"current_sent_cap={self.current_cap.get(ue_id)}"
                )

        if self.dl_load is not None and self.congestion_active == True: #self.dl_load >= self.args.congested_threshold:
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

            self.update_smoothed_dl_load() # update the smoothed load value using EMA
            self.update_congestion_state() # update congestion state based on the new load value

            self.apply_policy(e2_agent_id, trigger="style1_prb")
            self.print_status()

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

            self.apply_policy(e2_agent_id, trigger="style4_ue")
            self.print_status()
            
        except Exception as exc:
            print(f"ERROR in Style 4 UE callback: {exc}")

    @xAppBase.start_function
    def start(self, e2_node_id):
        print("Starting priority-aware PRB xApp")
        print(f"E2 node: {e2_node_id}")
        #print(f"High priority UEs: {sorted(self.high_ues)}")
        #print(f"Medium priority UEs: {sorted(self.medium_ues)}")
        #print(f"Best-effort UEs: {sorted(self.best_effort_ues)}")
        #print(f"Default priority for unmapped UEs: {self.args.default_priority}")
        print(f"UE slice map source: {self.args.ue_slice_map_source}")
        print(f"Slice priority map: {self.slice_priority_map}")

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

    #parser.add_argument("--congested_threshold", type=float, default=70.0)
    parser.add_argument("--congestion_enter_threshold", type=float, default=75.0)
    parser.add_argument("--congestion_exit_threshold", type=float, default=60.0)
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
    parser.add_argument("--min_cap_change", type=int, default=5)
    parser.add_argument("--enable_control", action="store_true")
    parser.add_argument("--rc_ran_func_id", type=int, default=3)
    parser.add_argument("--load_smoothing_alpha", type=float, default=0.3)

    parser.add_argument("--csv_log_file", type=str, default="")

    parser.add_argument(
        "--policy_mode",
        type=str,
        default="heuristic",
        choices=["heuristic", "bandit"],
    )

    parser.add_argument("--bandit_epsilon", type=float, default=0.1)
    parser.add_argument("--bandit_reward_delay", type=float, default=3.0)

    parser.add_argument(
        "--ue_slice_map_source",
        type=str,
        default="",
        help="Path or URL to srsRAN-exported UE slice map JSON.",
    )

    parser.add_argument(
        "--slice_map_reload_seconds",
        type=float,
        default=1.0,
        help="How often to reload the UE slice map.",
    )

    parser.add_argument(
        "--slice_priority_map",
        type=str,
        default="1-000000=best_effort,1-000001=medium,1-000002=high,1-ffffff=best_effort",
        help="Mapping from S-NSSAI to priority, ex: 1-000001=medium,1-000002=high",
    )

    parser.add_argument(
        "--debug_slice_map",
        action="store_true",
        help="Print slice map loading/debug messages.",
    )

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
