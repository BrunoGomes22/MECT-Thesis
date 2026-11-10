"""Small helpers shared by the dashboard modules."""

import logging
import re
import subprocess

log = logging.getLogger("tbdash")

_ANSI = re.compile(r"\x1b\[([0-9;]*)m")
_ANSI_OTHER = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ERR_WORDS = re.compile(r"\b(error|fatal|assert(ion)?|segmentation fault|failed|exception|"
                        r"\[E\]|\[err(or)?\]|\[critical\])", re.IGNORECASE)
_WARN_WORDS = re.compile(r"\b(warn(ing)?|\[W\]|\[warn(ing)?\])", re.IGNORECASE)

MAX_LINE = 2000


def run(argv, timeout=20, cwd=None, input_text=None):
    """Runs argv, returns (returncode, stdout+stderr). Never raises."""
    try:
        p = subprocess.run(argv, cwd=cwd, input=input_text, capture_output=True,
                           text=True, timeout=timeout, errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(argv)}"
    except OSError as e:
        return 127, f"{argv[0]}: {e}"


def classify_line(raw):
    """Strips ANSI codes from a log line and guesses its level: 'E', 'W' or ''.

    OAI colours LOG_E red (31) and LOG_W yellow (33/93); other tools only
    have words like [error]/[warning] to go by."""
    level = ""
    for m in _ANSI.finditer(raw):
        codes = m.group(1).split(";")
        if "31" in codes or "91" in codes:
            level = "E"
            break
        if "33" in codes or "93" in codes:
            level = "W"
    text = _ANSI_OTHER.sub("", _ANSI.sub("", raw)).rstrip("\r\n")
    if not level:
        if _ERR_WORDS.search(text):
            level = "E"
        elif _WARN_WORDS.search(text):
            level = "W"
    if len(text) > MAX_LINE:
        text = text[:MAX_LINE] + " …"
    return level, text
