#!/usr/bin/env python3

"""
Minimal priority-aware PRB xApp for srsRAN OSC RIC.

What it does:
1. KPM Style 1: reads DU/cell PRB load.
2. KPM Style 4: discovers connected UE IDs and reads DL throughput/delay.
3. Computes a multi-metric QoS pressure score from PRB use, UE competition,
   and DL delay.
4. Uses that score continuously as the base PRB differentiation intensity,
   gated by the priority-weighted delay pressure of active higher-priority
   UEs.
   - heuristic mode maps the gated intensity to priority-aware PRB caps
   - bandit mode learns an aggressiveness multiplier before the same gate
5. Sends E2SM-RC Slice-Level PRB Quota control per UE, but only if --enable_control is set.
6. Uses holdoff and minimum cap-change safeguards to avoid excessive RC messages.

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
from collections import defaultdict, deque
from statistics import median

from lib.xAppBase import xAppBase


STYLE1_PRB_METRICS = [
    "RRU.PrbAvailDl",
    "RRU.PrbUsedDl",
    "RRU.PrbTotDl",
]

STYLE4_UE_METRICS = [
    "DRB.UEThpDl",
    "DRB.RlcSduDelayDl",
]

BANDIT_AGGRESSIVENESS = { # QoS-pressure multipliers for contextual bandit
    0: 0.0,   # relaxed/no extra reaction
    1: 0.5,   # mild
    2: 0.75,  # moderate
    3: 1.0,   # normal
    4: 1.25,  # aggressive
}

VALID_PRIORITIES = {"best_effort", "medium", "high"}

HIGHER_PRIORITIES = {
    "high": set(),
    "medium": {"high"},
    "best_effort": {"medium", "high"},
}

PRIORITY_THROUGHPUT_WEIGHTS = {
    "high": 3.0,
    "medium": 2.0,
    "best_effort": 1.0,
}

# Normalize the same 3:2:1 priority levels against high priority when a UE's
# delay pressure protects lower-priority traffic.  Consequently, equal delay
# pressure from high has a stronger effect on best effort than equal pressure
# from medium, while zero delay pressure still causes no restriction.
HIGHER_PRIORITY_PROTECTION_FACTORS = {
    "high": 1.0,
    "medium": 2.0 / 3.0,
    #"medium": 0.85,
    "best_effort": 1.0 / 3.0,
}

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


def clip01(x):
    return max(0.0, min(1.0, x))


class PriorityPrbXapp(xAppBase):
    def __init__(self, config, http_server_port, rmr_port, args):
        super().__init__(config, http_server_port, rmr_port)
        self.args = args

        self.high_ues = parse_ue_list(args.high_ues)
        self.medium_ues = parse_ue_list(args.medium_ues)
        self.best_effort_ues = parse_ue_list(args.best_effort_ues)

        self.known_ues = set()
        self.ue_dl_thp = {}
        self.ue_dl_delay_ms = {}
        self.ue_delay_history = defaultdict(lambda: deque(maxlen=self.args.delay_history_len))
        self.dl_load = None # raw DL load
        self.smoothed_dl_load = None # smoothed DL load using EMA 
        self.congestion_score = 0.0 # normalized multi-metric QoS pressure score
        self.prb_pressure = 0.0
        self.competition_pressure = 0.0
        self.delay_pressure = None
        self.ue_delay_pressure = {}
        self.valid_delay_count = 0

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
                    "ue_dl_delay_ms",
                    "ue_delay_median_ms",
                    "ue_delay_pressure",
                    "dl_load_raw",
                    "dl_load_smoothed",
                    "congestion_score",
                    "prb_pressure",
                    "competition_pressure",
                    "delay_pressure",
                    "valid_delay_count",
                    "higher_priority_pressure",
                    "effective_intensity",
                    "congestion_active",
                    "target_cap",
                    "previous_cap",
                    "control_enabled",
                    "control_sent",
                    "policy_mode",
                    "w_prb",
                    "w_competition",
                    "w_delay",
                    "active_dl_threshold",
                    "delay_good_ms",
                    "delay_bad_ms",
                    "max_valid_delay_ms",
                    "delay_history_len",
                    "load_smoothing_alpha",
                    "best_effort_min_cap_at_max_pressure",
                    "medium_min_cap_at_max_pressure",
                    "high_min_cap_at_max_pressure",
                    "min_prb",
                    "dedicated_prb",
                    "control_holdoff_seconds",
                    "min_cap_change",
                ],
            )

            self.csv_writer.writeheader()

    def get_context(self):
        score = self.congestion_score or 0.0
        active_count = self.active_ue_count()

        if score < 0.25:
            pressure_bucket = "low"
        elif score < 0.50:
            pressure_bucket = "medium"
        elif score < 0.75:
            pressure_bucket = "high"
        else:
            pressure_bucket = "critical"

        if active_count <= 1:
            active_bucket = "one"
        elif active_count == 2:
            active_bucket = "two"
        else:
            active_bucket = "three_plus"

        return (pressure_bucket, active_bucket)

    def compute_reward(self):
        reward = 0.0

        for ue_id in self.active_ues():
            priority = self.priority_of_ue(ue_id)
            thp = self.ue_dl_thp.get(ue_id, 0.0) or 0.0
            reward += PRIORITY_THROUGHPUT_WEIGHTS[priority] * thp

        reward -= 5000.0 * (self.congestion_score or 0.0)
        reward -= 3000.0 * (self.delay_pressure or 0.0)

        return reward
    
    def select_bandit_action(self, context):
        epsilon = self.args.bandit_epsilon

        # exploration
        if random.random() < epsilon:
            return random.choice(list(BANDIT_AGGRESSIVENESS.keys()))

        # exploitation
        best_action = None
        best_value = None

        for action_id in BANDIT_AGGRESSIVENESS.keys():
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

        print(
            f"BANDIT SELECT: context={context}, action={action_id}, "
            f"aggressiveness={BANDIT_AGGRESSIVENESS[action_id]}"
        )

        return action_id

    def log_decision(
        self,
        trigger,
        e2_agent_id,
        ue_id,
        target_cap,
        previous_cap,
        control_sent,
        higher_priority_pressure=None,
        effective_intensity=None,
    ):
        if self.csv_writer is None:
            return

        priority = self.priority_of_ue(ue_id)
        thp = self.ue_dl_thp.get(ue_id, None)
        delay_ms = self.ue_dl_delay_ms.get(ue_id, None)
        delay_history = self.ue_delay_history.get(ue_id)
        delay_median_ms = median(delay_history) if delay_history else None

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
                "ue_dl_delay_ms": delay_ms,
                "ue_delay_median_ms": delay_median_ms,
                "ue_delay_pressure": self.ue_delay_pressure.get(ue_id),
                "dl_load_raw": self.dl_load,
                "dl_load_smoothed": self.smoothed_dl_load,
                "congestion_score": self.congestion_score,
                "prb_pressure": self.prb_pressure,
                "competition_pressure": self.competition_pressure,
                "delay_pressure": self.delay_pressure,
                "valid_delay_count": self.valid_delay_count,
                "higher_priority_pressure": higher_priority_pressure,
                "effective_intensity": effective_intensity,
                "congestion_active": self.congestion_active,
                "target_cap": target_cap,
                "previous_cap": previous_cap,
                "control_enabled": self.args.enable_control,
                "control_sent": control_sent,
                "policy_mode": self.args.policy_mode,
                "w_prb": self.args.w_prb,
                "w_competition": self.args.w_competition,
                "w_delay": self.args.w_delay,
                "active_dl_threshold": self.args.active_dl_threshold,
                "delay_good_ms": self.args.delay_good_ms,
                "delay_bad_ms": self.args.delay_bad_ms,
                "max_valid_delay_ms": self.args.max_valid_delay_ms,
                "delay_history_len": self.args.delay_history_len,
                "load_smoothing_alpha": self.args.load_smoothing_alpha,
                "best_effort_min_cap_at_max_pressure": self.args.best_effort_min_cap_at_max_pressure,
                "medium_min_cap_at_max_pressure": self.args.medium_min_cap_at_max_pressure,
                "high_min_cap_at_max_pressure": self.args.high_min_cap_at_max_pressure,
                "min_prb": self.args.min_prb,
                "dedicated_prb": self.args.dedicated_prb,
                "control_holdoff_seconds": self.args.control_holdoff_seconds,
                "min_cap_change": self.args.min_cap_change,
            }
        )

    def update_congestion_state(self):
        # Debug/status label only. Control decisions use congestion_score as a
        # continuous intensity and are not gated by this boolean.
        self.update_congestion_score()
        self.congestion_active = self.congestion_score > 0.0

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
            if self.active_ue_count() <= 1:
                return 100
            
            if self.last_bandit_action is None:
                return 100

            aggressiveness = BANDIT_AGGRESSIVENESS[self.last_bandit_action]
            base_intensity = clip01(
                (self.congestion_score or 0.0) * aggressiveness
            )
            _, effective_intensity = self.gated_intensity_for_priority(
                priority,
                self.active_ue_count(),
                base_intensity,
            )
            return self.target_cap_from_intensity(priority, effective_intensity)

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

    def compute_prb_pressure(self):
        if self.smoothed_dl_load is None:
            return 0.0

        return clip01(self.smoothed_dl_load / 100.0)

    def compute_competition_pressure(self):
        active_count = self.active_ue_count()

        if active_count <= 1:
            return 0.0

        # Use only active UEs so disconnected or idle known UEs cannot change
        # the pressure. Under equal sharing, one UE's nominal share is 1/N;
        # the remainder represents competition from the other active UEs.
        return clip01(1.0 - (1.0 / active_count))

    def delay_pressure_for_ue(self, ue_id):
        """Return normalized median-delay pressure for one active UE."""
        history = self.ue_delay_history.get(ue_id)
        if not history:
            return None

        delay_ms = median(history)
        denominator = self.args.delay_bad_ms - self.args.delay_good_ms
        if denominator <= 0:
            return 1.0 if delay_ms > self.args.delay_good_ms else 0.0

        return clip01(
            (delay_ms - self.args.delay_good_ms) / denominator
        )

    def compute_delay_pressure(self):
        pressures = []
        self.ue_delay_pressure = {}

        for ue_id in self.active_ues():
            pressure = self.delay_pressure_for_ue(ue_id)
            if pressure is None:
                continue

            self.ue_delay_pressure[ue_id] = pressure
            pressures.append(pressure)

        self.valid_delay_count = len(pressures)

        if not pressures:
            return None

        return sum(pressures) / len(pressures)

    def higher_priority_pressure(self, priority):
        """
        Return the strongest priority-weighted delay pressure above
        `priority`.

        If an active higher-priority UE has no valid delay history yet, use a
        conservative raw pressure of 1.0 until measurements become available.
        The raw pressure is multiplied by the protecting UE's priority factor,
        so high pressure protects more strongly than medium pressure. With no
        active higher-priority UE, no protection is needed.
        """
        higher_priorities = HIGHER_PRIORITIES.get(priority, set())
        higher_ues = [
            ue_id
            for ue_id in self.active_ues()
            if self.priority_of_ue(ue_id) in higher_priorities
        ]

        if not higher_ues:
            return 0.0

        pressures = []
        for ue_id in higher_ues:
            higher_priority = self.priority_of_ue(ue_id)
            protection_factor = HIGHER_PRIORITY_PROTECTION_FACTORS[
                higher_priority
            ]
            raw_pressure = self.ue_delay_pressure.get(ue_id)
            if raw_pressure is None:
                raw_pressure = 1.0
            pressures.append(raw_pressure * protection_factor)

        return max(pressures)

    def gated_intensity_for_priority(
        self,
        priority,
        active_count,
        base_intensity=None,
    ):
        """Combine cell-wide pressure with higher-priority protection need."""
        if self.dl_load is None or active_count <= 1:
            return 0.0, 0.0

        if base_intensity is None:
            base_intensity = self.congestion_score or 0.0

        higher_pressure = self.higher_priority_pressure(priority)
        effective_intensity = clip01(
            clip01(base_intensity) * higher_pressure
        )
        return higher_pressure, effective_intensity

    def update_congestion_score(self):
        self.prb_pressure = self.compute_prb_pressure()
        self.competition_pressure = self.compute_competition_pressure()
        self.delay_pressure = self.compute_delay_pressure()

        weighted_sum = (
            self.args.w_prb * self.prb_pressure
            + self.args.w_competition * self.competition_pressure
        )

        total_weight = (
            self.args.w_prb
            + self.args.w_competition
        )

        if self.delay_pressure is not None:
            weighted_sum += self.args.w_delay * self.delay_pressure
            total_weight += self.args.w_delay

        if total_weight <= 0:
            self.congestion_score = 0.0
            return

        score = weighted_sum / total_weight

        self.congestion_score = clip01(score)

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

    def target_cap_from_intensity(self, priority, intensity):
        intensity = clip01(intensity)

        if priority == "high":
            min_cap = self.args.high_min_cap_at_max_pressure
        elif priority == "medium":
            min_cap = self.args.medium_min_cap_at_max_pressure
        elif priority == "best_effort":
            min_cap = self.args.best_effort_min_cap_at_max_pressure
        else:
            min_cap = 100

        cap = 100 - intensity * (100 - min_cap)
        return int(round(max(min_cap, min(100, cap))))

    def target_cap_for_priority(self, priority, active_count):
        _, effective_intensity = self.gated_intensity_for_priority(
            priority,
            active_count,
        )
        return self.target_cap_from_intensity(
            priority,
            effective_intensity,
        )

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

        base_intensity = self.congestion_score or 0.0

        if self.args.policy_mode == "bandit":
            action_id = self.get_bandit_action_for_cycle()
            aggressiveness = BANDIT_AGGRESSIVENESS[action_id]
            base_intensity = clip01(base_intensity * aggressiveness)

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
                    0.0,
                    0.0,
                )
                continue
            
            priority = self.priority_of_ue(ue_id)
            higher_pressure, effective_intensity = (
                self.gated_intensity_for_priority(
                    priority,
                    active_count,
                    base_intensity,
                )
            )
            target_cap = self.target_cap_from_intensity(
                priority,
                effective_intensity,
            )

            control_sent = self.maybe_send_control(e2_agent_id, ue_id, target_cap)

            self.log_decision(
                trigger,
                e2_agent_id,
                ue_id,
                target_cap,
                previous_cap,
                control_sent,
                higher_pressure,
                effective_intensity,
            )

    def print_status(self):
        now = time.time()
        if now - self.last_print_time < self.args.print_period_seconds:
            return
        self.last_print_time = now

        print("\n--- Priority PRB xApp status ---")
        print(f"DL PRB load raw: {self.dl_load}")
        print(f"DL PRB load smoothed: {self.smoothed_dl_load}")
        print(f"QoS pressure score: {self.congestion_score}")
        print(f"PRB pressure: {self.prb_pressure}")
        print(f"Competition pressure: {self.competition_pressure}")
        print(f"Delay pressure: {self.delay_pressure}")
        print(f"Valid delay count: {self.valid_delay_count}")
        print("Deprecated score thresholds: retained for CLI compatibility, not used for control")
        print(f"Congestion active label: {self.congestion_active}")
        print(f"Active UEs: {self.active_ue_count()}")
        print(f"Policy mode: {self.args.policy_mode}")
        if self.args.policy_mode == "bandit":
            action_label = self.last_bandit_action if self.last_bandit_action is not None else 0
            print(
                f"Bandit action: {action_label}, "
                f"aggressiveness={BANDIT_AGGRESSIVENESS[action_label]}"
            )
        print(f"Control enabled: {self.args.enable_control}")

        if not self.known_ues:
            print("No UEs discovered yet from Style 4.")
        else:
            for ue_id in sorted(self.known_ues):
                priority = self.priority_of_ue(ue_id)
                ue_slice = self.ue_slice_map.get(ue_id, "unknown")
                target_cap = self.displayed_target_cap_for_ue(ue_id)
                thp = self.ue_dl_thp.get(ue_id, None)
                delay_ms = self.ue_dl_delay_ms.get(ue_id, None)
                ue_pressure = self.ue_delay_pressure.get(ue_id)
                higher_pressure = (
                    self.higher_priority_pressure(priority)
                    if self.is_active_ue(ue_id)
                    else 0.0
                )
                print(
                    f"UE {ue_id}: slice={ue_slice}, priority={priority}, "
                    f"DL_thp={thp}, DL_delay_ms={delay_ms}, "
                    f"delay_pressure={ue_pressure}, "
                    f"higher_priority_pressure={higher_pressure}, "
                    f"target_max_prb={target_cap}%, "
                    f"current_sent_cap={self.current_cap.get(ue_id)}"
                )

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
                raw_delay = first_number(metrics.get("DRB.RlcSduDelayDl"))

                if raw_delay is None:
                    self.ue_dl_delay_ms[ue_id] = None
                else:
                    delay_ms = raw_delay * 0.1
                    self.ue_dl_delay_ms[ue_id] = delay_ms

                    if (
                        self.is_active_ue(ue_id)
                        and delay_ms >= 0
                        and delay_ms <= self.args.max_valid_delay_ms  # ignore spurious outliers that are too high
                    ):
                        self.ue_delay_history[ue_id].append(delay_ms)

            # clear delay history for UEs that are no longer active
            for ue_id in self.known_ues:
                if not self.is_active_ue(ue_id):
                    self.ue_delay_history.pop(ue_id, None)
                    self.ue_dl_delay_ms[ue_id] = None

            self.update_congestion_state()
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

    parser.add_argument("--active_dl_threshold", type=float, default=500.0) # 500 kbps
    parser.add_argument("--delay_good_ms", type=float, default=5.0)
    parser.add_argument("--delay_bad_ms", type=float, default=100.0)
    parser.add_argument("--max_valid_delay_ms", type=float, default=6000.0)
    parser.add_argument("--delay_history_len", type=int, default=5)

    parser.add_argument("--best_effort_ues", type=str, default="")
    parser.add_argument("--medium_ues", type=str, default="")
    parser.add_argument("--high_ues", type=str, default="")
    parser.add_argument(
        "--default_priority",
        type=str,
        default="best_effort",
        choices=["best_effort", "medium", "high"],
    )

    parser.add_argument("--medium_min_cap_at_max_pressure", type=int, default=50)
    parser.add_argument("--best_effort_min_cap_at_max_pressure", type=int, default=20)
    parser.add_argument("--high_min_cap_at_max_pressure", type=int, default=100)

    parser.add_argument("--min_prb", type=int, default=10)
    parser.add_argument("--dedicated_prb", type=int, default=0)
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

    parser.add_argument(
        "--w_prb",
        type=float,
        default=0.40, 
        help="Weight for PRB pressure in congestion score.",
    )

    parser.add_argument(
        "--w_competition",
        type=float,
        default=0.35, 
        help="Weight for competition pressure in congestion score.",
    )

    parser.add_argument(
        "--w_delay",
        type=float,
        default=0.25, 
        help="Weight for delay pressure in congestion score.",
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
