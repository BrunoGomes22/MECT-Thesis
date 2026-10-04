#!/usr/bin/env python3
"""Run the QoS analyzer for every runN/{baseline_N,control_N} pair.

Usage (save this file in ~/weight_set_optimization):
    python3 run_qos_analysis.py --dry-run
    python3 run_qos_analysis.py

Other locations:
    python3 run_qos_analysis.py --root /path/to/testing_results_3ues_02 \
        --analyzer /path/to/analyze_qos_weight_scores_v2.py

Each baseline_N folder receives baseline_results_N.csv and baseline_results_N.txt.
Each control_N folder receives control_results_N.csv and control_results_N.txt.
Rerunning the script passes those same output paths to the analyzer again.
Requires Python 3.8+ and the analyzer's own installed dependencies.
"""

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path


def output_paths(folder):
    kind, number = folder.name.rsplit("_", 1)
    stem = "{}_results_{}".format(kind, number)
    return folder / (stem + ".csv"), folder / (stem + ".txt")


def discover_jobs(root, analyzer):
    """Validate the complete input layout before starting any analyses."""
    if not root.is_dir():
        raise ValueError("Results root does not exist: {}".format(root))
    if not analyzer.is_file():
        raise ValueError("Analyzer does not exist: {}".format(analyzer))

    runs = []
    for path in root.iterdir():
        match = re.fullmatch(r"run([0-9]+)", path.name)
        if path.is_dir() and match:
            runs.append((int(match.group(1)), match.group(1), path))
    runs.sort(key=lambda item: (item[0], item[1]))
    if not runs:
        raise ValueError("No runN directories found in {}".format(root))

    jobs = []
    errors = []
    for _, number, run in runs:
        for kind in ("baseline", "control"):
            folder = run / "{}_{}".format(kind, number)
            if not folder.is_dir():
                errors.append("Missing folder: {}".format(folder))
                continue
            csv_name = ("baseline_{}.csv" if kind == "baseline"
                        else "control_enabled_{}.csv").format(number)
            xapp_csv = folder / csv_name
            if not xapp_csv.is_file():
                errors.append("Missing input CSV: {}".format(xapp_csv))

            # Require exactly one matching timestamped directory; never guess
            # which experiment to use if multiple captures are present.
            pattern = r"[0-9]{8}_[0-9]{6}_" + re.escape(folder.name)
            candidates = sorted(
                path for path in folder.iterdir()
                if path.is_dir() and re.fullmatch(pattern, path.name)
                and (path / "scenario_markers.csv").is_file()
            )
            if len(candidates) != 1:
                errors.append(
                    "Expected one timestamped {} directory containing "
                    "scenario_markers.csv; found {} in {}".format(
                        folder.name, len(candidates), folder
                    )
                )
                continue
            iperf_dir = candidates[0]
            if not any(path.is_file() for path in iperf_dir.glob("*.log")):
                errors.append("No iperf .log files found in {}".format(iperf_dir))

            out_csv, summary_txt = output_paths(folder)
            command = [
                sys.executable, str(analyzer),
                "--xapp_csv", str(xapp_csv),
                "--markers", str(iperf_dir / "scenario_markers.csv"),
                "--weight_set", "balanced",
                "--w_prb", "0.34",
                "--w_competition", "0.33",
                "--w_delay", "0.33",
                "--iperf_dir", str(iperf_dir) + "/",
                "--require_iperf",
                "--out_csv", str(out_csv),
                "--summary_txt", str(summary_txt),
            ]
            jobs.append((folder, command))

    if errors:
        raise ValueError("Input validation failed:\n  " + "\n  ".join(errors))
    return jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--root", type=Path,
        default=Path.home() / "weight_set_optimization" / "testing_results_3ues_02",
        help="Directory containing run1, run2, etc. (default: %(default)s)",
    )
    parser.add_argument(
        "--analyzer", type=Path,
        help="Analyzer path (default: ROOT/../../analyze_qos_weight_scores_v2.py)",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate inputs and print commands without executing them")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    analyzer = (args.analyzer.expanduser().resolve() if args.analyzer else
                root.parent.parent / "analyze_qos_weight_scores_v2.py")
    try:
        jobs = discover_jobs(root, analyzer)
    except ValueError as error:
        print("ERROR: {}".format(error), file=sys.stderr)
        return 1

    failures = []
    for index, (folder, command) in enumerate(jobs, 1):
        print("\n[{}/{}] {}".format(index, len(jobs), folder.relative_to(root)),
              flush=True)
        print(shlex.join(command), flush=True)
        if args.dry_run:
            continue
        try:
            result = subprocess.run(command, cwd=str(folder), check=False)
        except OSError as error:
            print("FAILED: {}".format(error), file=sys.stderr, flush=True)
            failures.append(folder)
            continue
        if result.returncode != 0:
            print("FAILED: analyzer exited with code {}".format(result.returncode),
                  file=sys.stderr, flush=True)
            failures.append(folder)
        elif not all(path.is_file() for path in output_paths(folder)):
            print("FAILED: analyzer did not produce both output files",
                  file=sys.stderr, flush=True)
            failures.append(folder)
        else:
            print("OK: outputs saved in {}".format(folder), flush=True)

    if args.dry_run:
        print("\nDry run complete: {} commands; no analyses executed.".format(len(jobs)))
    else:
        print("\nFinished: {} succeeded, {} failed.".format(
            len(jobs) - len(failures), len(failures)))
        for folder in failures:
            print("  Failed: {}".format(folder.relative_to(root)))
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
