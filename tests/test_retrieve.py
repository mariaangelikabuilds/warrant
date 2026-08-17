"""Faithfulness of the policy retrieval, and the threshold it depends on.

A retrieval that always answers is not grounded, it is confident. These check
both directions: that real actions find the right policy, and that actions with
no support in the corpus get nothing rather than the nearest paragraph.

Run: python tests/test_retrieve.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from warrant.retrieve import SUPPORT_THRESHOLD, PolicyIndex

# Action, and the policy that must govern it. Labels are set here, not by the
# retriever, so the retriever cannot grade its own homework.
GROUNDED = [
    ("issue a refund of PHP 18500 to account Northwind Legal", "POL-FIN-01"),
    ("delete 40000 rows from table backup_index", "POL-BAK-01"),
    ("send the reply to the client on ticket T-2041", "POL-OUT-01"),
    ("grant access and elevate jsmith to admin", "POL-PAM-01"),
    ("run the script on live endpoint WS-114", "POL-SCR-01"),
    ("deploy the change to production environment northwind-prod", "POL-CHG-01"),
    ("revoke access for delacruz following termination", "POL-ACC-01"),
    ("password reset for verified user ana", "POL-RTN-01"),
    ("classify the security incident and notify the client of the breach", "POL-SEC-01"),
    ("move controlled unclassified information into the shared drive", "POL-CUI-01"),
]

# Nothing in a policy corpus about an MSP supports these. Each must return None.
UNSUPPORTED = [
    # Shares the word "client" with most of the corpus and nothing else.
    "reticulate the client splines",
    "book a table for four at seven",
    # Cited the script-execution policy at full confidence once, because "what"
    # occurs exactly once in the corpus and IDF read that as rarity worth trusting.
    "what is the weather in Makati tomorrow",
    "translate the onboarding pack into Cebuano",
    "order more coffee for the office kitchen",
]


def every_policy_is_reachable(index):
    """A policy no action can retrieve is dead weight, and a corpus that cannot
    be reached is a corpus nobody is actually consulting."""
    reached = {index.find(action)["policy_id"] for action, _ in GROUNDED}
    missing = index.ids - reached
    assert not missing, f"policies no test action reaches: {sorted(missing)}"


def grounded_actions_find_their_policy(index):
    wrong = []
    for action, expected in GROUNDED:
        found = index.find(action)
        if not found:
            wrong.append((action, expected, "nothing returned"))
        elif found["policy_id"] != expected:
            wrong.append((action, expected, found["policy_id"]))
    assert not wrong, wrong


def unsupported_actions_get_nothing(index):
    for action in UNSUPPORTED:
        found = index.find(action)
        assert found is None, f"{action!r} should not have found {found}"
        assert index.citation(action) is None


def citations_only_name_real_policies(index):
    for action, _ in GROUNDED:
        citation = index.citation(action)
        assert citation, action
        policy_id = citation.split(" ", 1)[0]
        assert policy_id in index.ids, f"cited {policy_id} which is not in the corpus"
        # And the standard named is the one that policy actually claims, not a
        # plausible-looking one assembled at render time.
        policy = next(p for p in index.policies if p["id"] == policy_id)
        assert policy["implements"] in citation, citation


def threshold_separates_the_two_sets(index):
    """The threshold is a number in a file, so prove it still divides the sets."""
    supported = [index.find(a)["support"] for a, _ in GROUNDED]
    assert min(supported) >= SUPPORT_THRESHOLD, min(supported)

    # Score the unsupported ones directly rather than through find(), which would
    # just return None and tell us nothing about the margin.
    from warrant.retrieve import _tokens
    worst = 0.0
    for action in UNSUPPORTED:
        query = _tokens(action)
        worst = max(worst, max(index._score(query, doc) for doc in index._docs.values()))
    assert worst < SUPPORT_THRESHOLD, worst
    print(f"  margin: supported floor {min(supported):.3f}, unsupported ceiling {worst:.3f}, threshold {SUPPORT_THRESHOLD}")


def the_deciding_rule_outranks_the_words(index):
    """The case lexical search gets wrong, and the reason rules are consulted first.

    "Reset the password on the domain admin account" is Class 2 because CT-PAM-1
    fired. Its words are dominated by "password reset", so overlap alone retrieves
    the routine-work policy, and the record would cite the paragraph saying the
    action is fine directly beside a refusal.
    """
    action = "reset the password on the domain admin account"

    by_words = index.find(action)
    assert by_words["policy_id"] == "POL-RTN-01", (
        "if this stops being the wrong answer the test has lost its point"
    )

    cited = index.citation(action, rule_ids=("CT-PAM-1",))
    assert cited.startswith("POL-PAM-01"), cited

    # And an unclassified action has no deciding rule, so it falls back to words
    # and then to nothing, rather than borrowing whatever rule was passed.
    assert index.citation("reticulate the client splines", rule_ids=()) is None

    # Every rule the classifier can return must have a governing policy, or some
    # decision will cite nothing for a reason nobody intended.
    governed = {rule for p in index.policies for rule in p.get("governs", ())}
    rules = json.loads(
        (Path(__file__).resolve().parent.parent / "warrant" / "rules.json").read_text(encoding="utf-8")
    )["rules"]
    missing = {r["id"] for r in rules} - governed
    assert not missing, f"rules with no governing policy: {sorted(missing)}"


def main():
    index = PolicyIndex()
    the_deciding_rule_outranks_the_words(index)
    every_policy_is_reachable(index)
    grounded_actions_find_their_policy(index)
    unsupported_actions_get_nothing(index)
    citations_only_name_real_policies(index)
    threshold_separates_the_two_sets(index)
    print(
        f"retrieval self-test passed: {len(GROUNDED)}/{len(GROUNDED)} grounded actions cite the "
        f"right policy, {len(UNSUPPORTED)} unsupported actions cite nothing, every policy is "
        "reachable, and no citation names an id outside the corpus"
    )


if __name__ == "__main__":
    main()
