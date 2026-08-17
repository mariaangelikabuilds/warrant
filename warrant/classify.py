"""Classify a proposed action against the Class 1 / Class 2 boundary.

Ported from class-two (github.com/mariaangelikabuilds/class-two), where this
started as a Claude skill. The matching is unchanged; only rule loading differs,
because here it is a library inside a gateway rather than a CLI in a container.

The two properties are the whole point and are not negotiable:

  Raise only. A Class 2 match beats a Class 1 match on the same action. Nothing
  in this file can talk an action down to routine.

  Fail closed. An action matching no rule is Class 2, reported as unclassified.
  An action nobody has ruled on is not thereby safe.
"""

import argparse
import json
import sys
from pathlib import Path

UNCLASSIFIED = "unclassified"


def load_rules(explicit=None):
    """Load rules.json. It ships beside this module so the package is self-contained."""
    path = Path(explicit) if explicit else Path(__file__).resolve().parent / "rules.json"
    if not path.is_file():
        raise SystemExit(f"rules.json not found at {path}")
    return json.loads(path.read_text(encoding="utf-8")), path


MAX_GAP = 2

# Dropped from both the rule vocabulary and the action before matching. A rule
# written as "draft a reply" must still catch "draft the first reply", and the
# only thing standing between them is grammar nobody meant as vocabulary.
STOPWORDS = frozenset(
    "a an and the of to for on in at by with from our your their its this that "
    "these those it is are be was were will would should can could".split()
)


def tokenize(text):
    """Content words only, lowercased. Punctuation and grammar are not vocabulary."""
    words = "".join(c if c.isalnum() else " " for c in text.lower()).split()
    return tuple(w for w in words if w not in STOPWORDS)


def phrase_present(tokens, phrase, max_gap=MAX_GAP):
    """True when the phrase's words appear in order, close together.

    Substring matching fails the way a service desk actually writes: 'reset the
    password' does not contain 'reset password'. Requiring order plus a small
    gap keeps 'reset the password' matching while stopping a phrase from
    matching words scattered across an unrelated sentence.
    """
    wanted = tokenize(phrase)
    if not wanted:
        return False
    position = -1
    for word in wanted:
        window = range(position + 1, min(position + 2 + max_gap, len(tokens)) if position >= 0 else len(tokens))
        found = next((i for i in window if tokens[i] == word), None)
        if found is None:
            return False
        position = found
    return True


def match_rules(action, rules):
    """Every rule whose vocabulary appears in the action text, in file order."""
    tokens = tokenize(action)
    return tuple(r for r in rules["rules"] if any(phrase_present(tokens, k) for k in r["any"]))


def classify(action, rules):
    """Classify one action. Returns a new dict; nothing is mutated."""
    matched = match_rules(action, rules)
    blocking = tuple(r for r in matched if r["class"] == 2)

    if not matched:
        return {
            "action": action,
            "class": rules["defaults"]["unmatched_class"],
            "basis": UNCLASSIFIED,
            "rules": (),
            "authority": "no rule matched",
            "citation": rules["defaults"]["reason"],
            "approver": "a human must classify this before it runs",
            "automation_may": "propose the action and ask for a ruling",
        }

    decided = blocking if blocking else matched
    lead = decided[0]
    return {
        "action": action,
        "class": lead["class"],
        "basis": "rule",
        "rules": tuple(r["id"] for r in decided),
        "authority": lead["authority"],
        "citation": lead["citation"],
        "approver": lead["approver"],
        "automation_may": lead["automation_may"],
    }


def render(result):
    """One human-readable block per action."""
    verdict = "CLASS 1  may run unattended" if result["class"] == 1 else "CLASS 2  needs a named human"
    lines = [
        result["action"],
        f"  {verdict}",
        f"  basis      {result['basis']}" + (f" ({', '.join(result['rules'])})" if result["rules"] else ""),
        f"  authority  {result['authority']}",
        f"  why        {result['citation']}",
    ]
    if result["approver"]:
        lines.append(f"  approver   {result['approver']}")
    lines.append(f"  automation may  {result['automation_may']}")
    return "\n".join(lines)


def read_actions(path):
    """A JSON array of strings, or of objects carrying an 'action' key."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit(f"{path} must hold a JSON array, found {type(raw).__name__}.")
    return tuple(item if isinstance(item, str) else item["action"] for item in raw)


def self_test():
    """The properties this file exists to hold. Run it before shipping a rule change."""
    rules, path = load_rules()

    routine = classify("reset the password for jsmith", rules)
    assert routine["class"] == 1, routine
    assert "CT-RTN-1" in routine["rules"], routine

    # Raise only: a routine verb next to a privileged noun is not routine.
    mixed = classify("reset the password on the domain admin account", rules)
    assert mixed["class"] == 2, mixed
    assert "CT-PAM-1" in mixed["rules"], mixed

    # The MFA carve-out. Resetting a password is routine, re-enrolling the
    # factor that proves identity is not.
    mfa = classify("reset mfa for a user who lost their phone", rules)
    assert mfa["class"] == 2, mfa

    # Fail closed on vocabulary the rules have never seen.
    unknown = classify("reticulate the client splines", rules)
    assert unknown["class"] == 2, unknown
    assert unknown["basis"] == UNCLASSIFIED, unknown

    # Drafting is Class 1, sending is Class 2, and the pair appearing together
    # must resolve to the higher one.
    draft = classify("draft a reply to the ticket", rules)
    assert draft["class"] == 1, draft
    send = classify("draft a reply to the ticket and send it", rules)
    assert send["class"] == 2, send
    assert "CT-OUT-1" in send["rules"], send

    # A rule's vocabulary carries grammar the real sentence does not. Both of
    # these came back unclassified on the first batch run, which is the failure
    # that matters most here: a missing rule reads identically to a safe action.
    phrasing = classify("draft the first reply in our service desk voice", rules)
    assert phrasing["class"] == 1 and "CT-RTN-3" in phrasing["rules"], phrasing
    spaced = classify("assemble the quarterly metrics for the QBR", rules)
    assert spaced["class"] == 1 and "CT-MON-1" in spaced["rules"], spaced

    # Generating a remediation script is fine. Running it is the boundary.
    run = classify("run the remediation script on the endpoint", rules)
    assert run["class"] == 2, run
    assert run["approver"], run

    # Every rule must carry the fields the skill renders, or the output lies.
    for rule in rules["rules"]:
        for field in ("id", "title", "class", "any", "authority", "citation", "automation_may"):
            assert rule.get(field) is not None, f"{rule.get('id')} is missing {field}"
        assert rule["class"] in (1, 2), rule["id"]
        if rule["class"] == 2:
            assert rule["approver"], f"{rule['id']} is Class 2 with no named approver"

    ids = [r["id"] for r in rules["rules"]]
    assert len(ids) == len(set(ids)), "duplicate rule id"

    print(f"self-test passed: {len(ids)} rules loaded from {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", nargs="?", help="the proposed action, in plain words")
    parser.add_argument("--file", help="JSON array of actions")
    parser.add_argument("--rules", help="path to rules.json")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--self-test", action="store_true", help="check the properties and exit")
    args = parser.parse_args(argv)

    if args.self_test:
        self_test()
        return 0

    if not args.action and not args.file:
        parser.error("give an action, or --file, or --self-test")

    rules, _ = load_rules(args.rules)
    actions = read_actions(args.file) if args.file else (args.action,)
    results = [classify(a, rules) for a in actions]

    if args.json:
        print(json.dumps([{**r, "rules": list(r["rules"])} for r in results], indent=2))
    else:
        print("\n\n".join(render(r) for r in results))

    # Exit 1 when anything needs a human, so a pipeline can stop on it.
    return 1 if any(r["class"] == 2 for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
