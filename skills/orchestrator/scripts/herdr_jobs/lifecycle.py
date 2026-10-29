"""Pure job lifecycle decisions and the effect data model.

The legacy :class:`~herdr_jobs.engine.Engine` still owns the production run
until every phase has moved to this seam.  This module deliberately contains
no filesystem, Herdr, or provider calls.  A transition describes the next
durable state and the ordered external effects required to reach it.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping, MutableMapping


PHASES = frozenset({"pending", "split", "moved", "retry_ready", "ready", "submitted", "failed"})
TERMINAL_STATES = frozenset({"idle", "done", "exited"})
OBSERVATION_STATES = TERMINAL_STATES | frozenset({"working", "blocked", "unknown"})
EFFECT_ACTIONS = frozenset({
    "admission_acquire", "admission_release", "split", "move", "retry_setup",
    "start", "prompt", "jail", "collect", "workspace_close",
})


def is_untrusted_cwd_issue(issue: Any) -> bool:
    return isinstance(issue, str) and ("cwd is untrusted" in issue.casefold()
                                     or "codex is showing a trust dialog" in issue.casefold())


class InvalidState(ValueError):
    """Raised when a reducer input is not a valid persisted job state."""


class EffectUnknown(Exception):
    """An effect may have happened, but its outcome cannot be verified.

    The exception is also a value object.  Adapters may return an instance or
    raise it; runners normalize both forms to the same pending transition.
    """

    def __init__(self, reason: str = "effect outcome is unknown", effect: "Effect | None" = None):
        super().__init__(reason)
        self.reason = str(reason)
        self.effect = effect


@dataclass(frozen=True)
class Effect:
    """One ordered external operation requested by a transition."""

    action: str
    job_id: str = ""
    attempt_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in EFFECT_ACTIONS:
            raise ValueError(f"unknown lifecycle effect: {self.action}")
        if not isinstance(self.job_id, str):
            raise TypeError("effect job_id must be text")
        if self.attempt_id is not None and not isinstance(self.attempt_id, str):
            raise TypeError("effect attempt_id must be text or null")
        if not isinstance(self.payload, Mapping):
            raise TypeError("effect payload must be an object")

    @property
    def kind(self) -> str:
        """Compatibility spelling for callers that call effects ``kind``."""
        return self.action

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "job_id": self.job_id,
            "attempt_id": self.attempt_id,
            "payload": deepcopy(dict(self.payload)),
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


@dataclass(frozen=True)
class EffectReceipt:
    """Verified result of executing one effect."""

    action: str
    job_id: str = ""
    attempt_id: str | None = None
    result: Mapping[str, Any] = field(default_factory=dict)

    @property
    def outcome(self) -> str:
        return "applied"

    @property
    def data(self) -> Mapping[str, Any]:
        return self.result

    def as_observation(self) -> dict[str, Any]:
        observation = deepcopy(dict(self.result))
        observation.setdefault("effect", self.action)
        observation.setdefault("effect_receipt", True)
        if self.attempt_id is not None:
            observation.setdefault("attempt_id", self.attempt_id)
        return observation


@dataclass(frozen=True)
class Transition:
    """Reducer output: a new state and ordered effects to execute."""

    next_state: Mapping[str, Any]
    effects: list[Effect] = field(default_factory=list)
    reason: str | None = None
    unknown: bool = False

    @property
    def state(self) -> Mapping[str, Any]:
        return self.next_state

    def effect_dicts(self) -> list[dict[str, Any]]:
        return [effect.as_dict() for effect in self.effects]


def job_id_of(state: Mapping[str, Any]) -> str:
    value = state.get("job_id")
    if value is None and isinstance(state.get("spec"), Mapping):
        value = state["spec"].get("job_id")
    return value if isinstance(value, str) else ""


def _attempt_id(state: Mapping[str, Any]) -> str | None:
    value = state.get("attempt_id")
    return value if isinstance(value, str) else None


def _route(state: Mapping[str, Any]) -> Mapping[str, Any]:
    spec = state.get("spec")
    if isinstance(spec, Mapping):
        route = spec.get("route")
    else:
        route = state.get("route")
    return route if isinstance(route, Mapping) else {}


def _pending_action(state: Mapping[str, Any]) -> str | None:
    pending = state.get("pending_effect")
    if isinstance(pending, Mapping):
        action = pending.get("action")
        return action if isinstance(action, str) else None
    return None


def _with_history(state: MutableMapping[str, Any], entry: Mapping[str, Any]) -> None:
    history = state.setdefault("history", [])
    if isinstance(history, list):
        history.append(dict(entry))


def _set_issue(state: MutableMapping[str, Any], issue: str | None) -> None:
    if "issue" in state or issue is not None:
        state["issue"] = issue


def _effect(state: Mapping[str, Any], action: str, payload: Mapping[str, Any] | None = None) -> Effect:
    return Effect(action, job_id_of(state), _attempt_id(state), payload or {})


class JobLifecycle:
    """Pure reducer for one persisted job.

    ``step`` also accepts a run state containing ``jobs``.  In that form it
    reduces the first job with work to do and returns a run-shaped state,
    which makes the seam convenient for both unit tests and the durable
    runner.
    """

    def step(self, state: Mapping[str, Any], observation: Mapping[str, Any] | None = None) -> Transition:
        if not isinstance(state, Mapping):
            raise InvalidState("job state must be an object")
        if isinstance(state.get("jobs"), list):
            return self._step_run(state, observation)
        return self._step_job(state, observation)

    def _step_run(self, state: Mapping[str, Any], observation: Mapping[str, Any] | None) -> Transition:
        next_state = deepcopy(dict(state))
        jobs = next_state.get("jobs")
        if not isinstance(jobs, list):
            raise InvalidState("run jobs must be an array")
        if not jobs:
            return Transition(next_state, reason="no jobs")
        target = observation.get("job_id") if isinstance(observation, Mapping) else None
        selected = None
        for index, job in enumerate(jobs):
            if (isinstance(job, Mapping) and not job.get("settled")
                    and (target is None or job_id_of(job) == target)):
                selected = index
                break
        if selected is None:
            reason = (f"no unsettled job matches observation job_id {target!r}"
                      if target is not None else "no unsettled jobs")
            return Transition(next_state, reason=reason)
        job_transition = self._step_job(jobs[selected], observation)
        jobs[selected] = dict(job_transition.next_state)
        return Transition(next_state, list(job_transition.effects), job_transition.reason, job_transition.unknown)

    def _step_job(self, original: Mapping[str, Any], observation: Mapping[str, Any] | None) -> Transition:
        state = deepcopy(dict(original))
        self._validate(state)
        observation = self._normalize_observation(observation)

        # A provider rate limit is a verified outcome even when the provider
        # response arrived while an effect was pending.  Clear that known
        # effect and schedule a retry; this path is distinct from an unknown
        # response, which must remain pending.
        if self._rate_limited(observation):
            if _pending_action(state) is not None:
                _with_history(state, {"action": "rate_limit_effect_reconciled",
                                      "pending_action": _pending_action(state)})
                state["pending_effect"] = None
            self._record_rate_limit(state, observation)
            return Transition(state, reason="rate limited")

        # A checkpoint with a pending effect means the caller may have lost the
        # external response.  It is reconciled only by an observation or
        # receipt; the reducer never emits that effect a second time.
        pending = _pending_action(state)
        if pending is not None:
            reconciled = self._reconcile_pending(state, observation)
            if reconciled is not None:
                return reconciled
            _set_issue(state, "effect delivery remains unproven; it will not be replayed")
            state["settled"] = False
            return Transition(state, reason="pending effect is unresolved", unknown=True)

        if observation:
            self._apply_observation(state, observation)

        if state.get("settled") is True:
            if state.get("admission_acquired") is True:
                state["settled"] = False
                action = "admission_release"
            else:
                return Transition(state, reason="job settled")
        else:
            action = self._next_action(state)
        if action is None:
            if state.get("admission_wait") is not None and state.get("admission_acquired") is not True:
                return Transition(state, reason="provider admission is waiting")
            self._settle(state, observation)
            if state.get("settled") is True and state.get("admission_acquired") is True:
                state["settled"] = False
                action = "admission_release"
            else:
                return Transition(state, reason="no effect required")

        state["pending_effect"] = {"action": action}
        if state.get("phase") != "failed":
            _set_issue(state, None)
        return Transition(state, [_effect(state, action)], reason=f"schedule {action}")

    @staticmethod
    def _validate(state: Mapping[str, Any]) -> None:
        phase = state.get("phase", "pending")
        if not isinstance(phase, str) or phase not in PHASES:
            raise InvalidState(f"invalid job phase: {phase!r}")
        pending = state.get("pending_effect")
        if pending is not None:
            if not isinstance(pending, Mapping) or pending.get("action") not in EFFECT_ACTIONS:
                raise InvalidState("pending_effect must name a known effect")
        if "settled" in state and type(state["settled"]) is not bool:
            raise InvalidState("settled must be a boolean")

    @staticmethod
    def _normalize_observation(observation: Mapping[str, Any] | None) -> dict[str, Any]:
        if observation is None:
            return {}
        if isinstance(observation, EffectReceipt):
            return observation.as_observation()
        if isinstance(observation, EffectUnknown):
            return {"effect_unknown": True, "reason": observation.reason}
        if not isinstance(observation, Mapping):
            raise InvalidState("observation must be an object")
        value = deepcopy(dict(observation))
        lifecycle = value.get("state")
        if lifecycle is not None and lifecycle not in OBSERVATION_STATES:
            raise InvalidState(f"invalid observed worker state: {lifecycle!r}")
        if "identity_verified" in value and type(value["identity_verified"]) is not bool:
            raise InvalidState("identity_verified must be a boolean")
        return value

    def _reconcile_pending(self, state: MutableMapping[str, Any], observation: Mapping[str, Any]) -> Transition | None:
        if not observation:
            return None
        if observation.get("effect_unknown") is True or observation.get("unknown") is True:
            _set_issue(state, observation.get("reason") or "effect outcome is unknown; inspect before retrying")
            return Transition(state, reason="effect outcome is unknown", unknown=True)

        pending = _pending_action(state)
        admission_wait = observation.get("admission_wait")
        if pending is not None and observation.get("effect_receipt") is True \
                and isinstance(admission_wait, Mapping):
            state["pending_effect"] = None
            if observation.get("lease_released") is True:
                state["admission_acquired"] = False
            state["admission_wait"] = deepcopy(dict(admission_wait))
            _set_issue(state, "waiting for provider admission")
            _with_history(state, {"action": "provider_admission_wait", **dict(admission_wait)})
            return Transition(state, reason="provider admission is waiting")
        if pending == "admission_acquire" and observation.get("effect_receipt") is True:
            admitted = observation.get("admitted") is True
            state["admission_acquired"] = observation.get("lease_held", admitted) is True
            state["admission_wait"] = (None if admitted else {
                "reason": observation.get("reason") or "capacity",
                "provider": observation.get("provider", state.get("provider")),
                "provider_active": observation.get("provider_active", 0),
                "cap": observation.get("cap", 1),
                "retry_at": observation.get("retry_at"),
            })
            state["pending_effect"] = None
            _set_issue(state, None if admitted else "waiting for provider admission")
            return Transition(state, reason="provider admitted" if admitted else "provider admission is waiting")
        if pending == "admission_release" and observation.get("effect_receipt") is True:
            state["admission_acquired"] = False
            state["pending_effect"] = None
            if state.get("phase") == "failed":
                state["settled"] = True
            current = state.get("observed")
            if isinstance(current, Mapping):
                self._settle(state, current)
            return Transition(state, reason="provider lease released")
        receipt = observation.get("effect_receipt") is True or observation.get("receipt_valid") is True
        result = observation.get("result")
        if isinstance(result, Mapping):
            receipt = True
            observation = {**observation, **result}
        lifecycle = observation.get("state")
        identity = observation.get("identity_verified") is True

        if pending in ("split", "move") and identity:
            pane_id = observation.get("pane_id")
            if isinstance(pane_id, str) and pane_id:
                if pending == "move" and isinstance(state.get("pane_id"), str):
                    previous = state.setdefault("previous_pane_ids", [])
                    if isinstance(previous, list):
                        previous.append(state["pane_id"])
                state["pane_id"] = pane_id
                if observation.get("workspace_id") is not None:
                    state["workspace_id"] = observation["workspace_id"]
                state["phase"] = "split" if pending == "split" else "moved"
                state["pending_effect"] = None
                _with_history(state, {"action": pending, "reconciled": True})
                _set_issue(state, None)
                return Transition(state, reason=f"reconciled {pending}")
            if not receipt:
                return None

        startup = observation.get("observation")
        startup = startup if isinstance(startup, Mapping) else {}
        issue = observation.get("launch_issue") or observation.get("issue") or startup.get("launch_issue")
        if (pending == "start" and (lifecycle == "blocked" or observation.get("launch_blocked") is True)
                and _route(state).get("runtime") == "codex" and is_untrusted_cwd_issue(issue)):
            state.update(phase="failed", settled=state.get("admission_acquired") is not True,
                         pending_effect=None, issue_code="codex_cwd_untrusted")
            _set_issue(state, issue)
            _with_history(state, {"action": "start_failed", "reason": "codex_cwd_untrusted", "issue": issue})
            return Transition(state, reason="Codex cwd trust failed at startup")

        if pending in ("start", "prompt", "jail") and (receipt or identity and lifecycle in OBSERVATION_STATES):
            prompt_verified = observation.get("prompt_verified") is True
            blocked = lifecycle == "blocked" or observation.get("launch_blocked") is True
            exited = lifecycle == "exited"
            if blocked and pending == "start":
                state["phase"] = "ready"
                _set_issue(state, observation.get("launch_issue") or observation.get("issue") or "worker startup is blocked")
            elif pending == "jail" and exited:
                state["phase"] = "submitted"
                state["activity_seen"] = True
                _set_issue(state, None)
            elif pending == "start" and (prompt_verified or receipt or lifecycle in ("idle", "done", "working")):
                state["phase"] = "submitted" if prompt_verified or receipt else "ready"
                if state["phase"] == "submitted":
                    state["activity_seen"] = True
                _set_issue(state, None if state["phase"] == "submitted" else "worker startup is ready for prompt")
            elif pending == "prompt" and (prompt_verified or receipt):
                state["phase"] = "submitted"
                state["activity_seen"] = True
                _set_issue(state, None)
            else:
                return None
            state["pending_effect"] = None
            _with_history(state, {"action": pending, "reconciled": True})
            self._settle(state, observation)
            return Transition(state, reason=f"reconciled {pending}")
        if pending == "retry_setup" and receipt:
            state["phase"] = "retry_ready"
            state["pending_effect"] = None
            _with_history(state, {"action": pending, "reconciled": True})
            return Transition(state, reason="reconciled retry setup")
        if pending == "workspace_close" and receipt:
            state["workspace_closed"] = True
            state["pending_effect"] = None
            _with_history(state, {"action": pending, "reconciled": True})
            return Transition(state, reason="reconciled workspace close")
        return None

    @staticmethod
    def _apply_observation(state: MutableMapping[str, Any], observation: Mapping[str, Any]) -> None:
        state["observed"] = dict(observation)
        if observation.get("identity_verified") is False:
            state["settled"] = False
            _set_issue(state, observation.get("launch_issue") or "worker identity is unverified")
        if observation.get("state") == "working" and observation.get("prompt_verified") is True:
            state["activity_seen"] = True
        if observation.get("receipt_valid") is True:
            state["activity_seen"] = True

    @staticmethod
    def _rate_limited(observation: Mapping[str, Any]) -> bool:
        signal = observation.get("rate_limit")
        return isinstance(signal, Mapping) or observation.get("outcome") == "rate_limited"

    @staticmethod
    def _record_rate_limit(state: MutableMapping[str, Any], observation: Mapping[str, Any]) -> None:
        state["phase"] = "retry_ready"
        state["settled"] = False
        state["rate_limit_seen"] = True
        signal = observation.get("rate_limit") or {}
        retry_at = signal.get("retry_at")
        if retry_at is None:
            retry_at = signal.get("retry_after_seconds")
        state["rate_limit_until"] = retry_at
        _set_issue(state, "provider rate limit recorded; retry is scheduled")
        _with_history(state, {"action": "provider_rate_limit", **dict(signal)})

    @staticmethod
    def _next_action(state: Mapping[str, Any]) -> str | None:
        phase = state.get("phase", "pending")
        if phase == "failed":
            return "admission_release" if state.get("admission_acquired") is True else None
        route = _route(state)
        mode = route.get("mode")
        if phase == "retry_ready" and state.get("rate_limit_seen") is True \
                and state.get("admission_acquired") is True:
            return "admission_release"
        if state.get("admission_lease_id") and state.get("admission_acquired") is not True:
            return "admission_acquire"
        if phase == "pending":
            return "split" if not state.get("pane_id") else ("jail" if mode == "jail" else "start")
        if phase == "split":
            return "move"
        if phase == "moved":
            return "retry_setup" if state.get("retry_profile") is not None else ("jail" if mode == "jail" else "start")
        if phase == "retry_ready":
            return "start"
        if phase == "ready":
            observation = state.get("observed")
            if not isinstance(observation, Mapping) or observation.get("identity_verified") is not True:
                return None
            if observation.get("state") != "idle":
                return None
            return "prompt"
        return None

    @staticmethod
    def _settle(state: MutableMapping[str, Any], observation: Mapping[str, Any]) -> None:
        lifecycle = observation.get("state")
        receipt = observation.get("receipt_valid") is True or observation.get("effect_receipt") is True
        if (state.get("phase") == "submitted" and state.get("pending_effect") is None
                and lifecycle in TERMINAL_STATES
                and (state.get("activity_seen") is True or receipt or lifecycle == "exited")):
            state["settled"] = True
            _set_issue(state, None)
        elif state.get("phase") == "submitted":
            state["settled"] = False


class EffectPort:
    """Structural protocol implemented by production and test adapters."""

    def execute(self, effect: Effect) -> EffectReceipt | EffectUnknown | Mapping[str, Any]:
        raise NotImplementedError


__all__ = ["Effect", "EffectPort", "EffectReceipt", "EffectUnknown", "InvalidState",
           "JobLifecycle", "Transition", "job_id_of"]
