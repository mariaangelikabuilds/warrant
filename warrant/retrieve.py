"""Find the internal policy that governs a decision, or return nothing.

The classifier already says which rule fired and which external standard it
answers to. That is a label. What an auditor asks next is "show me the policy you
were following", and this retrieves it so the sealed record carries the actual
governing paragraph rather than a rule id.

Two disciplines, both borrowed from runbook-rag:

  Cite or refuse. Below the support threshold this returns None. It never
  composes an answer out of the closest paragraph, and it never emits a clause
  number that is not in the corpus. A confident wrong citation in an audit record
  is worse than no citation.

  No standard text is reproduced. Every policy is written in-house and names the
  external standard by identifier only. Quoting NIST or ITIL into this repo would
  be both a licensing problem and a maintenance lie, since the copy would drift.

Lexical scoring with IDF weighting, standard library only. Ten policies is not a
corpus that needs embeddings: an approximate nearest neighbour index over ten
paragraphs would be cosplay, and the honest upgrade path is stated at the bottom
rather than pre-built.
"""

import json
import math
import re
from pathlib import Path

# Absolute weight of matched evidence, not a ratio. The first version scored
# matched IDF over the whole query's IDF, and words absent from the corpus
# contributed nothing to either side, so a query whose only known word happened to
# match scored a perfect 1.0: "reticulate the client splines" cited the incident
# policy at full confidence on the strength of the word "client". A ratio over a
# denominator you control is not a confidence measure. This is the summed weight
# of what actually matched, so one common word cannot reach it and several
# specific ones can. Calibrated in tests/test_retrieve.py against actions that
# should and should not find a policy; moving it without re-running those is how a
# threshold rots.
SUPPORT_THRESHOLD = 2.2

# Question words earn their place here the hard way. "what" occurs exactly once in
# the corpus, which made IDF treat it as a rare and therefore valuable term, and
# "what is the weather in Makati tomorrow" retrieved the script-execution policy on
# the strength of it. Rarity is not the same as meaning.
STOPWORDS = frozenset(
    "a an and the of to for on in at by with from is are be was were will would "
    "should can could may not no or if it its this that these those any all "
    "what who when where why how which whose do does did done has have had "
    "into onto out up down over under than then there here about while during "
    "i me my we our you your they them their he she it".split()
)

# At least this many distinct query terms must match. One word in common is a
# coincidence; two is a topic. Without this, any single rare-looking token can
# carry a citation on its own, which is how a weather question ended up citing a
# change-management policy.
MIN_MATCHED_TERMS = 2


def _stem(word):
    """Crude suffix stripping, deliberately not a real stemmer.

    "refund" failed to match "refunds" and the refund action scored zero, which is
    the whole cost of having no stemmer. Then "issued" stemmed to "issu" while
    "issue" stayed whole and they still missed each other, so the trailing vowel
    goes too and both land on "issu". Symmetry matters more than linguistic
    correctness here: the two forms only need to agree with each other.
    """
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    return word[:-1] if len(word) > 4 and word.endswith("e") else word


def _tokens(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [_stem(w) for w in words if w not in STOPWORDS]


class PolicyIndex:
    """Ten paragraphs, scored by weighted overlap. Built once, queried often."""

    def __init__(self, path=None):
        source = Path(path) if path else Path(__file__).resolve().parent / "policies.json"
        self.policies = json.loads(source.read_text(encoding="utf-8"))["policies"]
        self.ids = {p["id"] for p in self.policies}

        self._docs = {
            p["id"]: _tokens(f"{p['title']} {p['text']} {p['implements']}")
            for p in self.policies
        }
        self._idf = self._build_idf()

    def _build_idf(self):
        total = len(self._docs)
        seen = {}
        for tokens in self._docs.values():
            for word in set(tokens):
                seen[word] = seen.get(word, 0) + 1
        # Smoothed, so a word in every policy contributes nothing rather than
        # dividing by zero, and a word in one contributes most.
        return {word: math.log((total + 1) / (count + 0.5)) for word, count in seen.items()}

    def _score(self, query_tokens, doc_tokens):
        """Summed IDF weight of the query terms this document actually contains.

        Absolute, not normalised. A word the corpus has never seen scores nothing
        and, importantly, cannot shrink a denominator into flattering the result.
        """
        if not query_tokens or not doc_tokens:
            return 0.0
        doc = set(doc_tokens)
        matched = [w for w in set(query_tokens) if w in doc]
        if len(matched) < MIN_MATCHED_TERMS:
            return 0.0
        return sum(self._idf.get(word, 0.0) for word in matched)

    def find(self, action):
        """The governing policy for one action, or None when nothing supports it."""
        query = _tokens(action)
        ranked = sorted(
            ((self._score(query, tokens), policy_id) for policy_id, tokens in self._docs.items()),
            reverse=True,
        )
        if not ranked:
            return None

        best_score, best_id = ranked[0]
        if best_score < SUPPORT_THRESHOLD:
            return None

        policy = next(p for p in self.policies if p["id"] == best_id)
        # Belt and braces: the id handed back must exist in the corpus it came
        # from. Cheap, and it turns a whole class of citation bug into a crash.
        if policy["id"] not in self.ids:
            raise RuntimeError(f"retrieved id {policy['id']} is not in the corpus")

        return {
            "policy_id": policy["id"],
            "title": policy["title"],
            "implements": policy["implements"],
            "text": policy["text"],
            "support": round(best_score, 3),
        }

    def for_rules(self, rule_ids):
        """The policy that governs the rule which actually decided, if any.

        Lexical search alone gets this wrong in the case that matters most. "Reset
        the password on the domain admin account" is Class 2 because CT-PAM-1 fired,
        but the words are dominated by "password reset", so overlap retrieved the
        routine-work policy: an audit record citing the paragraph that says the
        action is fine, next to a refusal. The rule that decided is the fact, and
        the words are only a hint, so the rule is consulted first.
        """
        wanted = set(rule_ids or ())
        if not wanted:
            return None
        for policy in self.policies:
            if wanted & set(policy.get("governs", ())):
                return {
                    "policy_id": policy["id"],
                    "title": policy["title"],
                    "implements": policy["implements"],
                    "text": policy["text"],
                    "support": None,
                    "matched_by": "rule",
                }
        return None

    def citation(self, action, rule_ids=None):
        """One line for the ledger, or None. This is what gets sealed.

        Rule first, words second. An unclassified action has no deciding rule, so it
        falls through to lexical search, and if that finds nothing the citation is
        None and the action is still refused.
        """
        found = self.for_rules(rule_ids) or self.find(action)
        if not found:
            return None
        return f"{found['policy_id']} ({found['implements']}): {found['title']}"


# Upgrade path, stated rather than pre-built: at a few hundred policies this wants
# real embeddings and a vector index, and the threshold becomes a per-query
# decision rather than a constant. At ten it does not.
