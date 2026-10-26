"""Submit one Herdr prompt and verify that its worker turn actually started."""

import re
import time

from .security import ANSI_ESCAPE


ACTIVE_TURN_STATUS = re.compile(
    r"^(?:[•✳✴✢⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏◐◓◑◒|/\\-]\s*)?"
    r"working(?:\.{1,3}|…)?(?:\s+\(esc to interrupt\))?$",
    re.IGNORECASE,
)


# Codex keeps its timed status above the input and model footer. Narrow panes
# truncate the interrupt hint, including the closing parenthesis.
TIMED_ACTIVE_TURN_STATUS = re.compile(
    r"^[•✳✴✢⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏◐◓◑◒|/\\-]\s*"
    r"working\s+\((?:\d+[hms]\s*)+\s*[•·]\s*esc to "
    r"(?:interrupt\)|int(?:e|er|err|erru|errup|errupt)?…)$",
    re.IGNORECASE,
)


def has_active_turn_status(output):
    """Recognize a standalone active-turn marker, including Codex's timed TUI status."""
    if isinstance(output, bytes):
        output = output.decode("utf-8", "replace")
    clean = ANSI_ESCAPE.sub("", str(output))
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    return bool((lines and ACTIVE_TURN_STATUS.fullmatch(lines[-1]))
                or any(TIMED_ACTIVE_TURN_STATUS.fullmatch(line)
                       and (index == len(lines) - 1 or lines[index + 1].startswith("›"))
                       for index, line in enumerate(lines)))


class PromptNotStarted(RuntimeError):
    """The submission did not produce a verified active turn."""


def submit_and_verify(submit, observe, *, deadline, interval=0.2, sleep=time.sleep):
    """Submit exactly once; return only after a verified Working observation.

    ``observe`` returns a mapping with ``state``, ``prompt_verified``, and
    optional ``launch_issue`` fields. A blocked/trust observation is terminal:
    this helper never sends input to a dialog.
    """
    # Refuse to write into an already-blocked agent (including trust dialogs).
    last = observe()
    if last.get("state") == "blocked":
        raise PromptNotStarted(last.get("launch_issue") or "worker is blocked; inspect the dialog")
    if last.get("identity_verified") is not True or last.get("state") != "idle":
        raise PromptNotStarted("owned worker is not verified and idle; inspect before submitting")
    submit()
    while deadline.remaining() > 0:
        last = observe()
        if last.get("state") == "blocked":
            raise PromptNotStarted(last.get("launch_issue") or "worker is blocked; inspect the dialog")
        if last.get("state") == "working" and last.get("identity_verified") is True and last.get("prompt_verified") is True:
            return last
        sleep(min(interval, deadline.remaining()))
    if last and last.get("launch_issue"):
        raise PromptNotStarted(last["launch_issue"])
    raise PromptNotStarted("prompt was submitted but a verified Working state was not observed")


def pane_text_submit(command, herdr, pane, prompt, deadline):
    """Submit via pane text followed by Enter, preserving the prompt as argv."""
    code, _, _ = command([herdr, "pane", "send-text", pane, prompt], deadline)
    if code:
        raise PromptNotStarted("herdr pane send-text failed; inspect the owned pane")
    code, _, _ = command([herdr, "pane", "send-keys", pane, "enter"], deadline)
    if code:
        raise PromptNotStarted("herdr pane send-keys Enter failed; inspect the owned pane")
