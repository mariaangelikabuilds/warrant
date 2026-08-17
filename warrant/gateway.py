"""The decision path: classify, record, then execute or refuse.

An agent does not reach a real system from here. It asks for one action, and
this decides whether the action is inside the authority it has been given. The
ordering matters and is deliberate:

  classify -> record -> act

Recording happens before execution, not after. A record written after the fact
is missing exactly the case anyone would want it for, which is the run that
executed and then crashed.

Class 1 runs. Class 2 does not run, ever, from here. There is no flag, no
override argument, and no privileged caller. The only route from a Class 2
proposal to a real side effect is `approve`, which requires a named human and
writes a second sealed row naming them.
"""

from datetime import datetime, timezone

from .classify import classify, load_rules
from .ledger import Ledger

PROMPT_VERSION = "1.0.0"


class BudgetExceeded(RuntimeError):
    """Raised when a session has spent its ceiling. Deliberately not catchable
    into a retry: the point of a ceiling is that it stops things."""


class Budget:
    """A hard per-session ceiling on spend.

    Fails closed. A budget with no ceiling configured is not unlimited, it is a
    misconfiguration, so it refuses rather than assuming someone meant infinity.
    """

    def __init__(self, ceiling_usd):
        if ceiling_usd is None or ceiling_usd <= 0:
            raise ValueError("a budget needs a positive ceiling; there is no unlimited mode")
        self.ceiling_usd = float(ceiling_usd)
        self.spent_usd = 0.0

    def remaining(self):
        return max(0.0, self.ceiling_usd - self.spent_usd)

    def charge(self, amount_usd):
        amount = float(amount_usd or 0.0)
        if self.spent_usd + amount > self.ceiling_usd:
            raise BudgetExceeded(
                f"session ceiling ${self.ceiling_usd:.4f} reached "
                f"(spent ${self.spent_usd:.4f}, this action ${amount:.4f})"
            )
        self.spent_usd += amount
        return self.spent_usd


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Gateway:
    """Holds the rules, the ledger, the budget, and the systems it may reach."""

    def __init__(self, ledger_path, budget_usd, systems=None, rules_path=None):
        self.rules, self.rules_path = load_rules(rules_path)
        self.ledger = Ledger(ledger_path)
        self.budget = Budget(budget_usd)
        # A system this gateway has not been given is not reachable. Unknown
        # target is a refusal, not a passthrough.
        self.systems = dict(systems or {})

    def close(self):
        self.ledger.close()

    def _record(self, verdict, decision, system, cost_usd, decided_by=None,
                policy_citation=None, approves=None):
        return self.ledger.append({
            "recorded_at": _now(),
            "action": decision["action"],
            "decided_class": decision["class"],
            "basis": decision["basis"],
            "rule_ids": decision["rules"],
            "authority": decision["authority"],
            "citation": decision["citation"],
            "verdict": verdict,
            "system": system,
            "model": decision.get("model"),
            "prompt_version": PROMPT_VERSION,
            "policy_citation": policy_citation,
            "cost_usd": cost_usd,
            "decided_by": decided_by,
            "approves": approves,
        })

    def propose(self, action, system, cost_usd=0.0, model=None):
        """The only entry point an agent gets.

        Returns a dict carrying the verdict. On a Class 2 action it carries the
        sealed row's hash as the handle a human approval must quote, so an
        approval cannot be replayed against a different proposal.
        """
        decision = {**classify(action, self.rules), "model": model}

        if system not in self.systems:
            row = self._record("refused", decision, system, cost_usd)
            return {
                "verdict": "refused",
                "reason": f"unknown system {system!r}; this gateway cannot reach it",
                "decision": decision,
                "record": row["hash"],
            }

        self.budget.charge(cost_usd)

        if decision["class"] == 2:
            row = self._record("refused", decision, system, cost_usd)
            return {
                "verdict": "needs_human",
                "reason": decision["citation"],
                "approver": decision["approver"],
                "authority": decision["authority"],
                "decision": decision,
                "record": row["hash"],
                "agent_may": decision["automation_may"],
            }

        result = self.systems[system](action)
        row = self._record("executed", decision, system, cost_usd)
        return {
            "verdict": "executed",
            "decision": decision,
            "record": row["hash"],
            "result": result,
        }

    def approve(self, record_hash, decided_by, cost_usd=0.0):
        """A named human turns one refused proposal into one execution.

        The handle is the sealed hash of the refusal, so approving is bound to
        the exact proposal that was refused. An unnamed approver is not an
        approver, and a hash that is not a pending refusal is not approvable.
        """
        if not decided_by or not str(decided_by).strip():
            raise ValueError("an approval must name a human")

        pending = next((r for r in self.ledger.rows() if r["hash"] == record_hash), None)
        if pending is None:
            raise LookupError(f"no decision recorded under {record_hash[:12]}")
        if pending["verdict"] != "refused" or pending["decided_class"] != 2:
            raise LookupError("that record is not a refused Class 2 proposal")
        if any(r.get("approves") == record_hash for r in self.ledger.rows()):
            raise LookupError("that proposal was already approved")

        self.budget.charge(cost_usd)
        result = self.systems[pending["system"]](pending["action"])
        decision = {
            "action": pending["action"],
            "class": pending["decided_class"],
            "basis": pending["basis"],
            "rules": tuple(filter(None, pending["rule_ids"].split(","))),
            "authority": pending["authority"],
            "citation": pending["citation"],
            "model": pending["model"],
        }
        row = self._record(
            "executed_after_approval", decision, pending["system"], cost_usd,
            decided_by, approves=record_hash,
        )
        return {"verdict": "executed_after_approval", "record": row["hash"], "result": result}

    def verify(self):
        return self.ledger.verify()
