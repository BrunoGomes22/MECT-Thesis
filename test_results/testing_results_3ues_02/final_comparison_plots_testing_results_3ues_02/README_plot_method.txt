Final baseline-versus-control plot notes
==========================================
Input root: /home/pca/weight_set_optimization/testing_results_3ues_02
Runs: 5 matched baseline/control pairs
Primary scenarios: 01-12 (12 scenarios)
All 12 scenarios are included in the detailed comparison figures
Compact figures show scenarios 01, 03, and 07-12
Goodput/satisfaction/loss source: receiver-side iPerf analyzer fields
Delay source: raw xApp ue_dl_delay_ms, style4_ue active rows only
Delay window: correct per-run marker, warmup=5 s, cooldown=2 s
Delay validity filter: 0 <= delay <= 6000 ms
Error bars: sample standard deviation across the 5 runs
Small points: individual runs; thin lines: matched run pairs
Plot font: Times New Roman
Dynamics figure: full scenario-03 traffic interval; KPM bitrate, congestion score, and target caps averaged across 5 controlled runs

Warnings:
- None

Low valid-delay-sample groups (<20 in any run):
- control / 03_moderate_overload_equal / best_effort: per-run counts [8, 8, 8, 7, 8]
- control / 10_overload_best_effort / best_effort: per-run counts [24, 19, 32, 21, 19]
- control / 11_overload_medium / best_effort: per-run counts [14, 17, 17, 17, 14]
- control / 12_overload_high / best_effort: per-run counts [13, 13, 13, 13, 18]
