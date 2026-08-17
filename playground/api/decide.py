"""The playground endpoint. Standard library only, no MCP SDK, no network calls.

Two things this deliberately does not do. It never reaches a real system: the only
system registered is a recorder, so a visitor typing "delete everything" moves
nothing anywhere. And it holds no state between requests, so each visit builds its
own ledger in a temporary file and throws it away. A public endpoint that wrote to
a shared chain would be a shared mutable log with an open front door.

The demo action runs a whole sequence in one request, because the interesting
property is not a single verdict. It is that the chain notices when a past decision
is edited, and you cannot show that with one row.
"""

import json
import sqlite3
import sys
import tempfile
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from warrant.gateway import Gateway  # noqa: E402
from warrant.ledger import seal  # noqa: E402

MAX_ACTION = 300


def build(tmpdir):
    return Gateway(
        ledger_path=Path(tmpdir) / "playground.db",
        budget_usd=1.0,
        # A recorder, not a system. Nothing here can reach anything real.
        systems={"demo:service-desk": lambda action: {"recorded": action}},
    )


def decide(action):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        gateway = build(tmp)
        try:
            outcome = gateway.propose(action[:MAX_ACTION], "demo:service-desk")
            row = gateway.ledger.rows()[-1]
            return {
                "verdict": outcome["verdict"],
                "decided_class": outcome["decision"]["class"],
                "basis": outcome["decision"]["basis"],
                "rules": list(outcome["decision"]["rules"]),
                "authority": outcome["decision"]["authority"],
                "why": outcome["decision"]["citation"],
                "approver": outcome["decision"].get("approver"),
                "agent_may": outcome["decision"].get("automation_may"),
                "policy_citation": row["policy_citation"],
                "record_hash": row["hash"],
            }
        finally:
            gateway.close()


def tamper_demo():
    """Seal three decisions, edit one behind the gateway's back, verify again."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        path = Path(tmp) / "playground.db"
        gateway = Gateway(
            ledger_path=path, budget_usd=1.0,
            systems={"demo:service-desk": lambda action: {"recorded": action}},
        )
        try:
            for action in (
                "draft a reply to ticket T-2041",
                "delete 40000 rows from table backup_index",
                "password reset for verified user ana",
            ):
                gateway.propose(action, "demo:service-desk")

            before_ok, _ = gateway.verify()
            chain = [
                {"seq": r["seq"], "action": r["action"], "class": r["decided_class"],
                 "verdict": r["verdict"], "hash": r["hash"][:16]}
                for r in gateway.ledger.rows()
            ]

            # The careful forgery: change the row and reseal its own hash so it is
            # internally consistent. The break then moves to the row after it.
            rows = gateway.ledger.rows()
            forged = {**rows[1], "decided_class": 1, "verdict": "executed"}
            forged_hash = seal(forged, forged["prev_hash"])
            raw = sqlite3.connect(path)
            raw.execute(
                "UPDATE decisions SET decided_class = 1, verdict = 'executed', hash = ? WHERE seq = 2",
                (forged_hash,),
            )
            raw.commit()
            raw.close()

            after_ok, problems = gateway.verify()
            return {
                "chain": chain,
                "before": {"intact": before_ok},
                "edit": "seq 2 rewritten from refused Class 2 to executed Class 1, and its own hash resealed to match",
                "after": {"intact": after_ok, "problems": list(problems)},
            }
        finally:
            gateway.close()


class handler(BaseHTTPRequestHandler):  # noqa: N801 - Vercel expects this name
    def _send(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            length = int(self.headers.get("content-length") or 0)
            request = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            return self._send({"error": "expected a JSON body"}, 400)

        if request.get("demo") == "tamper":
            return self._send(tamper_demo())

        action = (request.get("action") or "").strip()
        if not action:
            return self._send({"error": "give an action in plain words"}, 400)
        return self._send(decide(action))

    def do_GET(self):
        self._send({
            "usage": "POST {\"action\": \"...\"} for a verdict, or POST {\"demo\": \"tamper\"} for the chain demo",
            "note": "no real system is reachable from here and no state is kept between requests",
        })
