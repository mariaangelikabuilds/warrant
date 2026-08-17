"""The properties the gateway exists to hold.

Every one of these is a thing the gateway must refuse to do, which is the only
kind of guarantee worth having here. Run: python tests/test_gateway.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from warrant.gateway import BudgetExceeded, Gateway


class Spy:
    """Stands in for a real system and records what actually reached it."""

    def __init__(self):
        self.calls = []

    def __call__(self, action):
        self.calls.append(action)
        return {"ok": True, "action": action}


def build(tmp, budget_usd=1.0):
    spy = Spy()
    gateway = Gateway(
        ledger_path=Path(tmp) / "ledger.db",
        budget_usd=budget_usd,
        systems={"n8n:msp-ticket-triage": spy},
    )
    return gateway, spy


def class_one_runs(gateway, spy):
    out = gateway.propose("draft a reply to the ticket", "n8n:msp-ticket-triage", 0.002)
    assert out["verdict"] == "executed", out
    assert spy.calls == ["draft a reply to the ticket"], spy.calls


def class_two_never_runs(gateway, spy):
    before = len(spy.calls)
    out = gateway.propose("reset the domain admin password", "n8n:msp-ticket-triage", 0.002)
    assert out["verdict"] == "needs_human", out
    assert out["approver"], "a Class 2 refusal must name who can approve it"
    assert len(spy.calls) == before, "a Class 2 action must not reach the system"
    return out["record"]


def unclassified_fails_closed(gateway, spy):
    before = len(spy.calls)
    out = gateway.propose("reticulate the client splines", "n8n:msp-ticket-triage", 0.001)
    assert out["verdict"] == "needs_human", out
    assert out["decision"]["basis"] == "unclassified", out
    assert len(spy.calls) == before, "an unruled action must not reach the system"


def raise_only_holds(gateway, spy):
    """A routine verb beside a privileged one resolves upward, not downward."""
    before = len(spy.calls)
    out = gateway.propose(
        "draft a reply to the ticket and send it", "n8n:msp-ticket-triage", 0.001
    )
    assert out["verdict"] == "needs_human", out
    assert len(spy.calls) == before


def unknown_system_is_refused(gateway, spy):
    before = len(spy.calls)
    out = gateway.propose("draft a reply to the ticket", "n8n:some-other-thing", 0.001)
    assert out["verdict"] == "refused", out
    assert len(spy.calls) == before, "a system not granted must not be reachable"


def approval_is_bound_and_single_use(gateway, spy, pending_hash):
    try:
        gateway.approve(pending_hash, decided_by="")
        raise AssertionError("an unnamed approver must be refused")
    except ValueError:
        pass

    try:
        gateway.approve("f" * 64, decided_by="Angel")
        raise AssertionError("approving an unknown record must be refused")
    except LookupError:
        pass

    before = len(spy.calls)
    out = gateway.approve(pending_hash, decided_by="Angel (security lead)", cost_usd=0.001)
    assert out["verdict"] == "executed_after_approval", out
    assert len(spy.calls) == before + 1, "approval is the only route to the side effect"

    try:
        gateway.approve(pending_hash, decided_by="Angel (security lead)")
        raise AssertionError("a proposal must not be approvable twice")
    except LookupError:
        pass


def budget_fails_closed(tmp):
    gateway, spy = build(tmp + "/budget", budget_usd=0.005)
    try:
        gateway.propose("draft a reply to the ticket", "n8n:msp-ticket-triage", 0.004)
        try:
            gateway.propose("draft a reply to the ticket", "n8n:msp-ticket-triage", 0.004)
            raise AssertionError("spending past the ceiling must raise")
        except BudgetExceeded:
            pass
        assert len(spy.calls) == 1, "nothing runs after the ceiling is hit"
    finally:
        gateway.close()

    try:
        Gateway(ledger_path=Path(tmp) / "x.db", budget_usd=0, systems={})
        raise AssertionError("a zero ceiling is a misconfiguration, not unlimited")
    except ValueError:
        pass


def main():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        gateway, spy = build(tmp)
        try:
            class_one_runs(gateway, spy)
            pending = class_two_never_runs(gateway, spy)
            unclassified_fails_closed(gateway, spy)
            raise_only_holds(gateway, spy)
            unknown_system_is_refused(gateway, spy)
            approval_is_bound_and_single_use(gateway, spy, pending)

            ok, problems = gateway.verify()
            assert ok and not problems, problems

            approval = [r for r in gateway.ledger.rows() if r["verdict"] == "executed_after_approval"]
            assert len(approval) == 1 and approval[0]["approves"] == pending, approval
            assert approval[0]["decided_by"] == "Angel (security lead)"
        finally:
            gateway.close()

        budget_fails_closed(tmp)

    print(
        "gateway self-test passed: class 2 never runs, unruled fails closed, raise-only "
        "holds, unknown systems are unreachable, approval is named, bound and single use, "
        "budget fails closed, ledger verifies"
    )


if __name__ == "__main__":
    main()
