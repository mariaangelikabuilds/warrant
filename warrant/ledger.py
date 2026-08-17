"""Append-only, hash-chained record of every authority decision.

Standard application logs record that an API call happened. They do not record
which prompt version was running, what the classifier decided, which rule
decided it, what authority that rule cites, or who approved it. When someone
asks six months later why an automated system did a thing to a client's
environment, "there is a log line" is not an answer.

Each row carries the hash of the row before it, so the chain is tamper-evident:
altering any field of any past decision breaks every hash after it, and `verify`
reports the first row where the chain parts. That is a property a third party can
check without trusting the system that produced the records.

Standard library only. SQLite because a single file that survives a restart is
the entire requirement, and Postgres would be an unbacked claim about scale.
"""

import hashlib
import json
import sqlite3
from pathlib import Path

GENESIS = "0" * 64

# Every field that the hash covers, in a fixed order. Ordering is part of the
# format: a hash over an unordered dict is a hash over whatever the runtime felt
# like today, and would fail to reproduce on another machine.
SEALED_FIELDS = (
    "seq",
    "recorded_at",
    "action",
    "decided_class",
    "basis",
    "rule_ids",
    "authority",
    "citation",
    "verdict",
    "system",
    "model",
    "prompt_version",
    "policy_citation",
    "cost_usd",
    "decided_by",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    seq             INTEGER PRIMARY KEY,
    recorded_at     TEXT    NOT NULL,
    action          TEXT    NOT NULL,
    decided_class   INTEGER NOT NULL,
    basis           TEXT    NOT NULL,
    rule_ids        TEXT    NOT NULL,
    authority       TEXT    NOT NULL,
    citation        TEXT    NOT NULL,
    verdict         TEXT    NOT NULL,
    system          TEXT,
    model           TEXT,
    prompt_version  TEXT,
    policy_citation TEXT,
    cost_usd        REAL,
    decided_by      TEXT,
    prev_hash       TEXT    NOT NULL,
    hash            TEXT    NOT NULL
);
"""


def seal(entry, prev_hash):
    """The hash of one entry, given the hash before it.

    Canonical JSON with sorted keys and no whitespace, so the same decision
    hashes identically on any machine and in any Python build.
    """
    body = {field: entry.get(field) for field in SEALED_FIELDS}
    payload = json.dumps(
        {"prev_hash": prev_hash, "entry": body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Ledger:
    """Open, append, read, verify. Nothing here updates or deletes a row."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self):
        self._db.close()

    def head(self):
        """Hash of the newest row, or the genesis value on an empty ledger."""
        row = self._db.execute(
            "SELECT hash FROM decisions ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["hash"] if row else GENESIS

    def append(self, entry):
        """Seal one decision onto the end of the chain. Returns the stored row."""
        prev_hash = self.head()
        next_seq = (
            self._db.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM decisions")
            .fetchone()["n"]
        )
        sealed = {**entry, "seq": next_seq}
        row = {
            **sealed,
            "rule_ids": ",".join(sealed.get("rule_ids") or ()),
            "prev_hash": prev_hash,
        }
        row["hash"] = seal({**sealed, "rule_ids": row["rule_ids"]}, prev_hash)

        columns = ", ".join(row)
        placeholders = ", ".join(f":{name}" for name in row)
        self._db.execute(f"INSERT INTO decisions ({columns}) VALUES ({placeholders})", row)
        self._db.commit()
        return row

    def rows(self):
        return [
            dict(r)
            for r in self._db.execute("SELECT * FROM decisions ORDER BY seq ASC")
        ]

    def verify(self):
        """Walk the chain and recompute every hash.

        Returns (ok, problems). A problem names the seq it was found at and what
        broke, because "the ledger is invalid" is not a usable answer to an
        auditor asking which decision was altered.
        """
        problems = []
        expected_prev = GENESIS

        for row in self.rows():
            if row["prev_hash"] != expected_prev:
                problems.append(
                    f"seq {row['seq']}: prev_hash does not match the row before it"
                )
            recomputed = seal(row, row["prev_hash"])
            if recomputed != row["hash"]:
                problems.append(
                    f"seq {row['seq']}: contents do not match the sealed hash, "
                    "this row was altered after it was recorded"
                )
            expected_prev = row["hash"]

        return (not problems), tuple(problems)
