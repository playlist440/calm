"""The daylight gate on its own: every transition of the state machine."""

from __future__ import annotations

from custom_components.calm.core.gate import DaylightGate, GateInput, GateState, Verdict


def step(gate: DaylightGate, **kwargs) -> Verdict:
    values = {"dark": True, "lit": True, "stale": False, "person": False, "arriving": False,
              "freshest_bright": False, "dt_seconds": 30.0, **kwargs}
    return gate.step(GateInput(**values)).verdict


def test_dark_keeps_it_open():
    gate = DaylightGate(600, 600)
    assert step(gate) is Verdict.LIGHT and gate.state is GateState.OPEN


def test_bright_with_nothing_lit_closes_at_once():
    gate = DaylightGate(600, 600)
    assert step(gate, dark=False, lit=False) is Verdict.SUFFICIENT
    assert gate.state is GateState.CLOSED


def test_bright_while_lit_waits_the_whole_off_dwell():
    gate = DaylightGate(600, 600)
    for _ in range(19):
        assert step(gate, dark=False) is Verdict.LIGHT
    assert step(gate, dark=False) is Verdict.TURN_OFF
    assert gate.state is GateState.CLOSED


def test_a_dark_moment_restarts_the_off_dwell():
    gate = DaylightGate(600, 600)
    for _ in range(15):
        step(gate, dark=False)
    step(gate)
    for _ in range(19):
        assert step(gate, dark=False) is Verdict.LIGHT


def closed() -> DaylightGate:
    gate = DaylightGate(600, 600)
    step(gate, dark=False, lit=False)
    return gate


def test_closed_waits_the_on_dwell_before_returning():
    gate = closed()
    for _ in range(19):
        assert step(gate, lit=False) is Verdict.WAITING
    decision = gate.step(GateInput(True, False, False, False, False, False, 30.0))
    assert decision.verdict is Verdict.RETURN and decision.reason == "daylight_gone"
    assert not decision.by_person and gate.state is GateState.OPEN


def test_a_person_returns_it_at_once():
    gate = closed()
    decision = gate.step(GateInput(True, False, False, True, True, False, 30.0))
    assert (decision.verdict, decision.reason, decision.by_person) == (Verdict.RETURN, "arrival", True)


def test_asking_returns_it_at_once_and_only_once():
    gate = closed()
    gate.ask()
    assert gate.step(GateInput(True, False, False, False, False, False, 30.0)).reason == "asked"
    assert not gate.asked


def test_a_silent_sensor_cannot_hold_it_closed():
    gate = closed()
    assert gate.step(GateInput(True, False, True, False, False, False, 30.0)).reason == "sensor_silent"


def test_the_freshest_reading_bright_restarts_the_wait():
    gate = closed()
    for _ in range(15):
        step(gate, lit=False)
    assert step(gate, lit=False, freshest_bright=True) is Verdict.WAITING
    assert gate.dark_for == 0.0


def test_bright_again_while_closed_forgets_the_wait_and_the_question():
    gate = closed()
    gate.ask()
    step(gate, lit=False)  # returns at once because asked
    gate = closed()
    gate.ask()
    assert step(gate, dark=False, lit=False) is Verdict.SUFFICIENT
    assert not gate.asked
