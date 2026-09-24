Final baseline-versus-control plot notes
==========================================
Input root: /home/pca/weight_set_optimization/testing_results_3ues
Runs: 5 matched baseline/control pairs
Primary scenarios: 01, 02, 03, and 05-10 (9 scenarios)
Excluded from primary figures: 00* calibration and 04 extreme stress
Goodput/satisfaction/loss source: receiver-side iPerf analyzer fields
Delay source: raw xApp ue_dl_delay_ms, style4_ue active rows only
Delay window: correct per-run marker, warmup=5 s, cooldown=2 s
Delay validity filter: 0 <= delay <= 6000 ms
Error bars: sample standard deviation across the 5 runs
Small points: individual runs; thin lines: matched run pairs
ECDF source: receiver-side iPerf 1-second interval received bytes
ECDF window: same warmup=5 s and cooldown=2 s (53 intervals/run)
ECDF interpretation: pooled curves are descriptive; intervals within a run are not independent repetitions

Warnings:
- None

Low valid-delay-sample groups (<20 in any run):
- control / 03_moderate_overload_equal / best_effort: per-run counts [7, 7, 8, 7, 7]
