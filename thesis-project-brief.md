# Thesis Project Brief

*Living document. Update it whenever a decision changes. Last updated: 8 October 2026.*

Items marked **[TODO]** still need filling in. Items marked **[CONFIRM]** are my reading of the code or setup and should be checked.

---

## 0. How to use this brief (instructions for Claude)

- This is an MSc thesis in English, written in LaTeX with the University of Aveiro template. The author writes the first draft of each section. Claude critiques, checks consistency, and suggests edits rather than rewriting whole sections.
- The thesis covers the **heuristic policy only**. The contextual-bandit mode in the code was exploratory and may appear at most as future work.
- When the thesis text and the xApp code disagree, point out the mismatch instead of silently going with either one.
- Never invent references. Suggested literature is a lead to verify, not a citation.
- Use the terminology and notation in Section 8.

---

## 1. Thesis at a glance

| Item | Value |
|---|---|
| Degree | MSc, University of Aveiro, DETI (Engineering) |
| Author | **[TODO]** |
| Advisors | **[TODO]** |
| Working title | **[TODO]**. Example: *Priority-Aware PRB Capping in O-RAN: A Near-RT RIC xApp for Protecting High-Priority Slices under Congestion* |
| Submission date | **[TODO]** |

**One-paragraph summary.** I developed an xApp for the Near-RT RIC that protects higher-priority traffic when a 5G cell is under load. The testbed uses srsRAN Project (split CU/DU), Open5GS, and the srsRAN OSC-based Near-RT RIC. The xApp reads cell PRB usage and per-UE throughput and RLC delay through E2SM-KPM. It combines these into a continuous congestion (QoS-pressure) score. Each UE is mapped to a priority class (high, medium, best-effort) through its network slice (S-NSSAI). When higher-priority UEs show delay pressure, the xApp sends per-UE maximum-PRB-ratio caps through E2SM-RC. Best-effort UEs are capped most, medium UEs less, and high-priority UEs are never capped, which frees PRBs for higher-priority traffic. The approach works well with 3 UEs at 10 MHz, degrades with 6 UEs at 10 MHz due to scheduler limitations in srsRAN Project, and recovers with 6 UEs at 20 MHz.

---

## 2. Problem, research questions, and contributions

### Problem
Under congestion, a cell's scheduler shares PRBs without regard to service priority unless slicing or QoS policies are enforced. Higher-priority (for example, SLA-bound) traffic then suffers higher delay and lower throughput. O-RAN's Near-RT RIC enables closed-loop control at a 10 ms to 1 s timescale, so a priority-aware policy can run outside the vendor scheduler, using only standard E2 service models.

### Candidate research questions (proposed; agree with advisors)
- **RQ1.** Can a Near-RT RIC xApp that uses only standard E2SM-KPM metrics and E2SM-RC PRB-quota control protect high-priority slice traffic in a congested cell, and at what cost to lower-priority traffic?
- **RQ2.** How do the weights of the congestion score (PRB load, UE competition, RLC delay) change the trade-off between protecting high-priority traffic and lower-priority throughput?
- **RQ3.** How does the approach scale with the number of UEs and the cell bandwidth, and which RAN-side mechanisms limit it?

### Contributions (draft)
1. A priority-aware PRB-capping xApp with a multi-metric congestion score and higher-priority delay gating, implemented on an open-source O-RAN stack.
2. A CU-side mechanism that exports the UE-to-slice mapping (SST/SD) to the RIC, so the xApp can derive priority from the slice the UE is registered in.
3. A DU-side correction of the RLC SDU delay KPM in srsRAN Project. **[CONFIRM: describe what was wrong and what the fix changes.]**
4. An experimental evaluation on a split CU/DU testbed across UE counts (3, 6), bandwidths (10, 20 MHz), offered loads, and weight configurations, including an analysis of where the approach stops working.

### Related work status
No systematic literature comparison yet. The idea was developed during the project. **[TODO]** A literature search is needed on slicing and priority xApps (especially ones evaluated on srsRAN or OAI testbeds), RIC-based RAN slicing, and AI/DRL-based slicing control, so the thesis can state what is new. The claim that "literature rarely evaluates across many scenarios" must be backed by specific papers.

---

## 3. Testbed and software

### Architecture
- **CU, DU, and 5G Core on separate machines**, following the O-RAN split.
- **Near-RT RIC** on its own machine: srsRAN's OSC-based RIC (https://github.com/srsran/oran-sc-ric). The xApp runs inside it. All metrics and control messages go over **E2 between the RIC and the DU**.
- **UEs:** virtual srsUEs running on the same machine as the DU (ZMQ-based RF emulation **[CONFIRM]**). This avoids the latency that remote virtual UEs would add.
- **Core:** Open5GS, installed with the official quickstart guide (https://open5gs.org/open5gs/docs/guide/01-quickstart/). Version **[TODO]**.

### srsRAN Project version and modifications
- Last srsRAN Project commit before the project moved to OCUDU: `4bf1543936d062686d64c10724d2f27a9854f065`.
- **DU patch:** correction of the RLC delay metric (`DRB.RlcSduDelayDl`). **[TODO]** Describe the bug, the fix, and the files changed. The xApp multiplies the raw value by 0.1 to get milliseconds, which assumes the patched DU reports it in units of 0.1 ms. **[CONFIRM]**
- **CU patch:** the CU periodically exports a JSON file mapping each UE (`gnb_cu_ue_f1ap_id`) to its slice (`sst`, `sd`). The xApp reads it from a file path or HTTP URL every second. **[TODO]** Describe the transport from the CU machine to the RIC machine.
- Both patches should go in an appendix as diffs or listings.

### Earlier setups (for Chapter 3)
Over-the-air setups with a Quectel modem and a USRP B200:
1. Monolithic (RAN and core on one PC)
2. RAN and core on separate PCs
3. CU, DU, and core on separate machines

The setup used for the main results is virtual srsUEs with the CU/DU split (above). **[TODO]** Note why the project moved away from OTA (stability, number of UEs, repeatability?).

### Why srsRAN Project
Mainly easier to deploy than OpenAirInterface. **[TODO]** Add other reasons (E2 agent built in, KPM/RC support, code readability, active community?) and the main alternatives considered.

### Cell configurations
| Config | Bandwidth | PRBs | UEs |
|---|---|---|---|
| A | 10 MHz | 52 | 3 srsUEs |
| B | 10 MHz | 52 | 6 srsUEs |
| C | 20 MHz | 106 if SCS is 15 kHz **[CONFIRM]** | 6 srsUEs |

Other parameters **[TODO]**: band, duplex mode (FDD/TDD), SCS, MIMO layers, MCS table, scheduler policy (time-domain RR or PF).

---

## 4. The xApp (heuristic policy)

Source: `priority_prb_xapp_test.py`, built on the RIC's `xAppBase` Python framework.

### Inputs (E2SM-KPM, RAN function ID 2)
- **Report Style 1 (cell level):** `RRU.PrbTotDl` (DL PRB utilisation, %), with a fallback computed from `RRU.PrbUsedDl` and `RRU.PrbAvailDl`.
- **Report Style 4 (per UE):** `DRB.UEThpDl` (DL throughput) and `DRB.RlcSduDelayDl` (DL RLC SDU delay). The UE matching condition (`ul-rSRP < 1000`) acts as a catch-all to discover every connected UE.
- Report period and granularity period: 1000 ms by default.
- The policy is re-evaluated on every Style 1 and Style 4 indication.

### Priority mapping
UE ID → slice (from the CU JSON) → priority, using a configurable S-NSSAI map. Default:
`1-000000 = best_effort`, `1-000001 = medium`, `1-000002 = high`, `1-ffffff = best_effort`.
Unmapped UEs fall back to a manual UE-ID list, then to `best_effort`. This assumes the KPM Style 4 UE IDs match the CU's `gnb_cu_ue_f1ap_id`. **[CONFIRM]**

### Active UEs
A UE is active if its DL throughput is at least 500 (kbps **[CONFIRM unit]**). Only active UEs count for competition and delay, and inactive UEs have their cap reset to 100%.

### Congestion (QoS-pressure) score
All terms are normalised to [0, 1].

- **PRB pressure:** $P = \bar{L}/100$, where $\bar{L}$ is an exponential moving average of the DL PRB load, $\bar{L}_t = \alpha L_t + (1-\alpha)\bar{L}_{t-1}$, with $\alpha = 0.3$.
- **Competition pressure:** $C = 1 - 1/N_a$ for $N_a \ge 2$ active UEs, otherwise 0.
- **Per-UE delay pressure:** $d_u = \mathrm{clip}\!\left(\frac{\tilde{\delta}_u - \delta_{good}}{\delta_{bad} - \delta_{good}}\right)$. Here $\tilde{\delta}_u$ is the median of the UE's last 5 valid delay samples, with $\delta_{good} = 5$ ms and $\delta_{bad} = 100$ ms. Samples above 6000 ms are discarded as outliers.
- **Cell delay pressure:** $D$ = mean of $d_u$ over active UEs that have delay history.
- **Score:** $S = \dfrac{w_P P + w_C C + w_D D}{w_P + w_C + w_D}$, with defaults $w_P = 0.34$, $w_C = 0.33$, $w_D = 0.33$. If no UE has delay data, the delay term is dropped and the remaining weights are renormalised.

### Higher-priority gating
For a UE of priority $p$:

- $H_p = \max_{v \in \text{active, higher priority}} \big(f_{p(v)} \cdot d_v\big)$, with protection factors $f_{high} = 1$ and $f_{medium} = 2/3$.
- A higher-priority UE with no delay history yet counts as $d_v = 1$ (a conservative choice).
- $H_{high} = 0$ always, so high-priority UEs are never capped. $H_p = 0$ when no higher-priority UE is active.
- **Effective intensity:** $I_p = \mathrm{clip}(S \cdot H_p)$. It is 0 when there are fewer than 2 active UEs.

The score sets *how much* the cell is under pressure. The gate sets *whether anyone above this UE actually needs protection*. A congested cell with healthy high-priority delay produces no caps.

### Cap mapping
$\text{cap}_p = 100 - I_p\,(100 - m_p)$ (in % of PRBs), with floor $m_p$ at maximum intensity:
- best-effort: $m = 20\%$
- medium: $m = 50\%$
- high: $m = 100\%$ (never capped)

Example: a best-effort cap of 29% corresponds to $I \approx 0.89$. In a 52-PRB cell that allows roughly 15 PRBs.

### Control (E2SM-RC, RAN function ID 3)
- **Slice-Level PRB Quota** control message per UE: `min_prb_ratio = 10%`, `max_prb_ratio = cap`, `dedicated_prb_ratio = 0%`, with ACK requested.
- **Anti-oscillation:** a new cap is sent only if it differs from the last one sent by at least 5 percentage points, and at least 5 s have passed since the last control message to that UE.
- How the DU enforces the cap: the cap limits the PRBs given in each PDCCH DL grant to that UE. **[CONFIRM by pointing to the srsRAN scheduler code path; this belongs in Chapter 4.]**

### Logging
Every decision is written to CSV: timestamp, trigger, per-UE throughput/delay/pressures, score components, gating, target and previous cap, whether control was sent, and all parameters. This is the main data source for the results.

### Parameter table (defaults)
| Parameter | Default | Meaning |
|---|---|---|
| `report_period_ms` / `granul_period_ms` | 1000 / 1000 | KPM reporting |
| `active_dl_threshold` | 500 | Minimum DL throughput to count as active |
| `delay_good_ms` / `delay_bad_ms` | 5 / 100 | Delay normalisation range |
| `max_valid_delay_ms` | 6000 | Outlier cut-off |
| `delay_history_len` | 5 | Median window (samples) |
| `load_smoothing_alpha` | 0.3 | EMA factor for PRB load |
| `w_prb` / `w_competition` / `w_delay` | 0.34 / 0.33 / 0.33 | Score weights |
| `best_effort` / `medium` / `high` min cap | 20 / 50 / 100 % | Cap at maximum intensity |
| `min_prb` / `dedicated_prb` | 10 / 0 % | RC quota floor and dedicated share |
| `control_holdoff_seconds` | 5 | Per-UE control holdoff |
| `min_cap_change` | 5 | Minimum cap change (percentage points) |

---

## 5. Experiments and results

### Traffic
Downlink UDP with iperf3 (`-u`) at several offered rates per UE, to see how the xApp reacts to different load levels. **[TODO]** List the rate sets per scenario, test duration, number of repetitions, and which UE belongs to which slice in each run.

### Scenarios
| Scenario | Config | Purpose | Outcome |
|---|---|---|---|
| Cell saturation | A, B | Maximum cell capacity, network characterisation | **[TODO]** numbers |
| xApp performance, 3 UEs @ 10 MHz | A | Main proof of concept | Works as intended |
| xApp performance, 6 UEs @ 10 MHz | B | Scalability | Degrades with several UEs per class (scheduler limitation) |
| xApp performance, 6 UEs @ 20 MHz | C | Scalability with more capacity | Fixes the issue; cell capacity nearly doubles |
| Weight combinations | **[TODO]** | Sensitivity of the score weights | Different combinations affect the network differently; plots exist |

### Baselines
**[TODO]** Decide and state them explicitly. Minimum: the same traffic **without the xApp** (control disabled), so each result can be read as an improvement. Consider also a static cap configuration.

### Metrics to report (proposal)
Per priority class: DL throughput, RLC delay (median and tail), and the share of time high-priority delay stays under a target. Also: total cell throughput (cost of protection), cap trajectories over time, number of control messages sent, and time from load onset to cap taking effect.

### Results files
**[TODO]** Upload the CSV logs, iperf3 outputs, and plots to the project, with a short index: file → scenario → configuration → weights.

---

## 6. Known limitations

- **Scalability at 10 MHz:** with several UEs per priority class, capping becomes less effective because of how the srsRAN Project scheduler allocates grants. **[TODO]** Pin down the mechanism (for example, the per-slot UE limit, the round-robin order, or the minimum grant size) and characterise where it breaks down. This is a finding, not just a weakness.
- Virtual srsUEs with ZMQ: no real radio channel, no fading or CQI variation. The results reflect scheduling behaviour, not radio conditions.
- 1 s KPM granularity and a 5 s holdoff limit how fast the loop reacts.
- Downlink only, UDP only.
- The priority mapping depends on two custom patches, not on standard E2 information elements.
- Bandit mode is exploratory and out of scope.

---

## 7. Chapter plan (initial, agreed with advisors; subject to change)

**Chapter 1 – Introduction.** Motivation and context, problem statement, research questions, contributions, document structure.

**Chapter 2 – Background and state of the art.**
- Current state and future of mobile networks; why O-RAN matters for 6G; what 6G is expected to enable
- O-RAN limitations that could affect 6G performance
- The MAC scheduler as the resource allocator; standard (3GPP-era) and AI-based scheduling algorithms; their current limitations
- UEs with different priorities, needs, and SLAs; heterogeneous networks and densification
- Positioning of the proposed solution, including evaluation across many scenarios (to be supported by literature)

**Chapter 3 – Testbed and network characterisation.**
- Overview of the main open-source frameworks (srsRAN Project, OAI, others); why srsRAN Project; srsRAN Project details
- The setups tried: OTA monolithic, OTA RAN/core split, OTA CU/DU/core split, and the final virtual-UE CU/DU split
- Network characterisation; cell saturation tests with 3 and 6 UEs
- xApp architecture diagram

**Chapter 4 – Priority-aware PRB capping: design and evaluation at 10 MHz.**
- Link between cell saturation and the limitations from Chapter 2
- How the xApp mitigates the problem: the congestion score, gating, caps, and how caps limit PDCCH grants
- Results with 3 and 6 UEs at 10 MHz
- The scalability limitation from 3 to 6 UEs

**Chapter 5 – Scaling to 20 MHz.** Results with 6 UEs at 20 MHz, improvement in network and xApp performance.

**Chapter 6 – Conclusions and future work.** Not yet in the plan; most templates expect one. Future work could include the contextual-bandit/RL policy, UL support, more UEs, and OTA validation.

**Appendices.** srsRAN DU and CU patches, full parameter list, extra plots.

Open structural questions **[TODO]**: where the weight-sensitivity study goes (Chapter 4 or a separate section), and whether the design of the xApp should be its own chapter, separate from the results.

---

## 8. Writing conventions

### LaTeX template (University of Aveiro)
- `memoir` class, 11pt, A4, one-sided, 1.5 line spacing; cover in a separate `cover.tex` using `uaThesisTemplate.sty` (`[engineering,deti]`).
- Built with `make` (latexmk + pdflatex with `--shell-escape`, required by `minted`), output in `build/`.
- Bibliography: biblatex with **biber**, `style=ieee`. Files: `bib/references.bib` and `bib/rfc.bib`. A `bib/3gpp.bib` with 3GPP specs ships with the template but is **not** in the `\bibliography{}` line; add it to cite 3GPP TSs.
- Acronyms: `acronym` package, `printonlyused`, defined in `glossary.tex` (which still has the placeholder entries and the Portuguese heading "Glossário").
- Code listings: `minted`. Units: `siunitx`. TikZ/PGF lines are commented out; enable them for diagrams.
- Template leftovers to remove: `lipsum`, dummy acronyms, sample chapters.

### Terminology (keep consistent)
- **O-RAN** (the alliance and architecture); **Near-RT RIC**; **xApp**; **E2**, **E2SM-KPM**, **E2SM-RC**
- **srsRAN Project** (not "srsRAN" alone when referring to the 5G gNB); **srsUE**; **Open5GS**
- **PRB**, **PDCCH**, **RLC**, **S-NSSAI** (SST + SD)
- Priority classes: **high**, **medium**, **best-effort** (BE)
- **Congestion score** $S$ (the code calls it the "QoS pressure score"; pick one name and use it everywhere)
- **Cap** = maximum PRB ratio (%) sent to a UE

### Notation
$N_a$ active UEs; $\bar{L}$ smoothed PRB load; $P, C, D$ pressure terms; $w_P, w_C, w_D$ weights; $S$ score; $d_u$ per-UE delay pressure; $H_p$ higher-priority pressure; $f$ protection factor; $I_p$ effective intensity; $m_p$ minimum cap.

---

## 9. Points to resolve (from reading the code)

1. **"Detects congestion" vs. continuous control.** RESOLVED: all results were produced with the continuous (threshold-free) version. There is no congestion threshold: the cap scales continuously with $S \cdot H_p$, and `congestion_active` is only a label ($S > 0$). The leftover "deprecated score thresholds" message in the code is inactive. In the thesis, describe the xApp as reacting in proportion to pressure, not as switching on when congestion is detected. Optionally remove the leftovers from the version of the code submitted with the thesis.
2. **Delay drives the capping.** Because of the gate, PRB load and UE count alone never cause caps; a higher-priority UE must show delay above 5 ms (or have no delay history yet). Describe it this way in the text, since it is a design choice worth defending.
3. **Cold-start behaviour.** A higher-priority UE without delay history counts as maximum pressure, so lower-priority UEs can be capped as soon as a high-priority UE becomes active. Mention it and check whether it is visible in the logs.
4. **Weight renormalisation.** When no UE has delay data, the score becomes $(0.34P + 0.33C)/0.67$ with the default weights. Worth one sentence in the text.
5. **Units.** Confirm the throughput threshold unit (kbps?) and the ×0.1 delay scaling against the patched DU.
6. **UE ID consistency.** Confirm the KPM Style 4 UE ID equals the CU's `gnb_cu_ue_f1ap_id` in all runs, including after UE reattachment.
7. **"Slice-level" control used per UE.** The RC message is a slice-level PRB quota, but the xApp sends one per UE. Explain how srsRAN interprets this (per-UE enforcement in the scheduler) so examiners do not read it as slice-level control.
