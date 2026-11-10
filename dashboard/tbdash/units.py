"""Runs components and tasks as transient systemd units (systemd-run), so they
outlive dashboard restarts and can also be managed with systemctl/journalctl:

    systemctl status srsdash-du      systemctl stop srsdash-ue1

Output goes to a file per unit in log_dir, not to the journal: OAI logs far
too much for journald's rate limits."""

import os
import shutil

from .util import log, run

# srsdash-, not tbdash-: the OAI dashboard on this host uses tbdash-<id> for
# components with the same ids (cu, du, ue1...).
PREFIX = "srsdash-"
# Development only: TBDASH_SYSTEMD_USER=1 uses the per-user systemd instance
# (no root needed, no User=/RT limits), to exercise start/stop as a normal user.
USER_MODE = os.environ.get("TBDASH_SYSTEMD_USER") == "1"
_SCOPE = ["--user"] if USER_MODE else []
_PROPS = ["Id", "LoadState", "ActiveState", "SubState", "MainPID", "Result",
          "ExecMainStatus", "ExecMainStartTimestampMonotonic", "ExecMainExitTimestampMonotonic"]


def unit_name(kind, uid):
    return f"{PREFIX}{'task-' if kind == 'task' else ''}{uid}.service"


def show(units):
    """Returns {unit: {prop: value}} for the given unit names."""
    if not units:
        return {}
    rc, out = run(["systemctl"] + _SCOPE + ["show", "--no-pager", "-p", ",".join(_PROPS)] + list(units),
                  timeout=10)
    result = {}
    for block in out.strip().split("\n\n"):
        props = {}
        for line in block.splitlines():
            key, _, val = line.partition("=")
            props[key] = val
        if props.get("Id"):
            result[props["Id"]] = props
    return result


def monotonic_to_age(usec_str, mono_now):
    try:
        usec = int(usec_str)
    except (TypeError, ValueError):
        return None
    if usec <= 0:
        return None
    return max(0.0, mono_now - usec / 1e6)


def trim_log(path, max_bytes):
    """Keeps a running unit's log bounded: copies it to <file>.1 and truncates
    it. Units append with O_APPEND, so they carry on at the new end."""
    try:
        if os.path.getsize(path) <= max_bytes:
            return False
        shutil.copyfile(path, path + ".1")
        with open(path, "r+b") as f:
            f.truncate(0)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"# testbed-dashboard: log above {max_bytes // (1024 * 1024)} MB, "
                    f"previous part in {os.path.basename(path)}.1\n")
        return True
    except OSError as e:
        log.warning("could not trim %s: %s", path, e)
        return False


def rotate_log(path):
    """Keeps the previous run's log as <file>.1, so each run starts clean."""
    try:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            os.replace(path, path + ".1")
    except OSError as e:
        log.warning("could not rotate %s: %s", path, e)


def is_active(unit):
    rc, out = run(["systemctl"] + _SCOPE + ["is-active", unit], timeout=10)
    return out.strip() in ("active", "activating", "deactivating", "reloading")


def start(unit, spec, argv, env, log_path, description):
    """Starts argv as a transient unit. Returns (ok, message)."""
    if is_active(unit):   # never rotate the log of a unit that is still writing it
        return False, f"unit {unit} is already active"
    run(["systemctl"] + _SCOPE + ["reset-failed", unit], timeout=10)
    rotate_log(log_path)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"# testbed-dashboard: {' '.join(argv)}\n")
    if spec["user"] not in ("root", "0"):
        try:
            shutil.chown(log_path, user=spec["user"])
        except (LookupError, OSError):
            pass

    cmd = ["systemd-run"] + _SCOPE + [
           f"--unit={unit}", f"--description={description}",
           "--property=Type=exec",
           f"--property=WorkingDirectory={spec['cwd']}",
           f"--property=StandardOutput=append:{log_path}",
           f"--property=StandardError=append:{log_path}",
           "--property=KillMode=control-group",
           f"--property=KillSignal={spec['kill_signal']}",
           f"--property=TimeoutStopSec={spec['stop_timeout']}"]
    if not USER_MODE:
        # the RAN wants real-time scheduling and locked memory, like under sudo
        cmd += ["--property=LimitRTPRIO=infinity", "--property=LimitMEMLOCK=infinity",
                "--property=LimitNOFILE=65536"]
        if spec["user"] not in ("root", "0"):
            cmd.append(f"--property=User={spec['user']}")
    for key, val in env.items():
        cmd.append(f"--setenv={key}={val}")
    cmd.append("--")
    # systemd-run looks a relative program up from its own directory, not
    # from WorkingDirectory: make "./nr-softmodem" absolute.
    prog = argv[0]
    if "/" in prog and not os.path.isabs(prog):
        prog = os.path.normpath(os.path.join(spec["cwd"], prog))
    cmd.append(prog)
    cmd.extend(argv[1:])
    rc, out = run(cmd, timeout=30)
    if rc != 0:
        return False, out.strip() or f"systemd-run exit {rc}"
    return True, out.strip()


def kill(unit, sig="SIGKILL"):
    rc, out = run(["systemctl"] + _SCOPE + ["kill", f"--signal={sig}", unit], timeout=15)
    return rc == 0, out.strip()


def stop(unit, timeout):
    """Stops the unit. A stop asked for is not a failure: the unit's failed
    state (exit status after SIGINT/SIGKILL) is cleared, so it reads as stopped."""
    rc, out = run(["systemctl"] + _SCOPE + ["stop", unit], timeout=timeout + 15)
    run(["systemctl"] + _SCOPE + ["reset-failed", unit], timeout=10)
    if rc != 0:
        return False, out.strip() or f"systemctl stop exit {rc}"
    return True, ""
