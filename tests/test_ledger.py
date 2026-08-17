"""The properties the ledger exists to hold.

Run: python tests/test_ledger.py
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from warrant.ledger import GENESIS, Ledger, seal


def decision(action, decided_class=2, verdict="refused"):
    return {
        "recorded_at": "2026-08-17T09:00:00Z",
        "action": action,
        "decided_class": decided_class,
        "basis": "rule",
        "rule_ids": ("CT-PAM-1",),
        "authority": "PAM/PIM practice",
        "citation": "privileged use is approved per use",
        "verdict": verdict,
        "system": "n8n:msp-ticket-triage",
        "model": "claude-sonnet-5",
        "prompt_version": "1.0.0",
        "policy_citation": None,
        "cost_usd": 0.0021,
        "decided_by": None,
    }


def edit(path, statement):
    """Change the database underneath the ledger, the way someone covering a
    track would, rather than through an API that would reseal the chain."""
    raw = sqlite3.connect(path)
    raw.execute(statement)
    raw.commit()
    raw.close()


def chain_links(ledger):
    first = ledger.append(decision("reset the domain admin password"))
    second = ledger.append(decision("draft a reply to the ticket", 1, "executed"))
    third = ledger.append(decision("send the reply"))

    assert first["prev_hash"] == GENESIS, "the first row hangs off genesis"
    assert second["prev_hash"] == first["hash"], "each row carries the one before it"
    assert third["prev_hash"] == second["hash"]
    assert ledger.head() == third["hash"]

    ok, problems = ledger.verify()
    assert ok and not problems, problems

    # Sealing is deterministic, or the chain cannot be checked anywhere else.
    assert seal({**first, "rule_ids": "CT-PAM-1"}, GENESIS) == first["hash"]
    return first


def silent_edit_is_caught(ledger, path):
    """Attack one: change a field, leave the hash alone."""
    edit(path, "UPDATE decisions SET decided_class = 1, verdict = 'executed' WHERE seq = 1")

    ok, problems = ledger.verify()
    assert not ok, "an altered row must not verify"
    assert any("seq 1" in p and "altered" in p for p in problems), problems


def resealed_edit_is_caught(ledger, path):
    """Attack two, the careful one: change a field AND reseal that row's hash so
    it is internally consistent. The row now passes its own check, and the break
    moves to the row after it, whose prev_hash no longer matches."""
    rows = ledger.rows()
    forged = {**rows[0], "decided_class": 1, "verdict": "executed"}
    forged_hash = seal(forged, forged["prev_hash"])
    edit(
        path,
        "UPDATE decisions SET decided_class = 1, verdict = 'executed', "
        f"hash = '{forged_hash}' WHERE seq = 1",
    )

    ok, problems = ledger.verify()
    assert not ok, "resealing one row must not repair the chain"
    assert any("seq 2" in p and "prev_hash" in p for p in problems), problems
    # And the forged row itself now looks fine in isolation, which is exactly why
    # the chain is what carries the guarantee rather than any single hash.
    assert not any("seq 1" in p for p in problems), problems


def main():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = Path(tmp) / "ledger.db"
        ledger = Ledger(path)
        try:
            chain_links(ledger)
            silent_edit_is_caught(ledger, path)
            resealed_edit_is_caught(ledger, path)
        finally:
            ledger.close()

    print(
        "ledger self-test passed: links, seals deterministically, "
        "catches a silent edit and a resealed one"
    )


if __name__ == "__main__":
    main()
