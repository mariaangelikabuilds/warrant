"""One real session, recorded for the case study.

This is not a mock of the flow; it is the flow. A live model works the
destructive-cleanup ticket, every tool call goes through the actual MCP server
in-process, Class 2 proposals fail with the protocol's own -32042 elicitation,
a named human approves one refusal out of band via the gateway, and the chain
verifies on camera.

Writes demo/session-events.json (timestamped lines) for the replay renderer,
and prints the same lines live.

Run: python demo/record_session.py   (needs ANTHROPIC_API_KEY in env or .env)
"""

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")
logging.disable(logging.INFO)

# A fresh ledger per recording, so the chain on camera is exactly this session.
os.environ["WARRANT_LEDGER"] = str(Path(tempfile.mkdtemp()) / "session-ledger.db")

import anthropic  # noqa: E402
from mcp.shared.exceptions import UrlElicitationRequiredError  # noqa: E402

from evals.harness import MODEL, PHRASING, SYSTEM_PROMPT, TOOLS, api_key, usd  # noqa: E402
from warrant import server  # noqa: E402

SYSTEM = "n8n:msp-ticket-triage"
TASK = next(
    s["task"] for s in json.loads(
        (Path(__file__).resolve().parent.parent / "evals" / "scenarios.json").read_text(encoding="utf-8")
    )["scenarios"] if s["id"] == "destructive-cleanup"
)

EVENTS = []
T0 = time.time()

ANSI = {"dim": "\033[90m", "cmd": "\033[97m", "ok": "\033[32m", "warn": "\033[33m",
        "err": "\033[31m", "hash": "\033[36m", "": ""}


def emit(kind, text):
    EVENTS.append({"t": round(time.time() - T0, 3), "k": kind, "s": text})
    print(f"{ANSI.get(kind, '')}{text}\033[0m", flush=True)


async def call(tool, args):
    """FastMCP hands back content blocks; the tool's dict is the JSON inside."""
    result = await server.mcp.call_tool(tool, args)
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


async def main():
    emit("dim", f"$ python demo/record_session.py")
    emit("dim", f"warrant · one real session · model {MODEL} · ledger fresh, 0 rows")
    emit("", "")
    emit("cmd", f"ticket in: {TASK}")
    emit("", "")

    client = anthropic.Anthropic(api_key=api_key())
    messages = [{"role": "user", "content": TASK}]
    held = []  # (record_hash, phrase)

    for turn in range(1, 7):
        reply = client.messages.create(
            model=MODEL, max_tokens=2000, system=SYSTEM_PROMPT,
            tools=TOOLS, messages=messages,
        )
        turn_cost = usd(reply.usage)
        calls = [b for b in reply.content if b.type == "tool_use"]
        emit("dim", f"model turn {turn} · ${turn_cost:.4f} · {len(calls)} tool call{'s' if len(calls) != 1 else ''}")
        if not calls:
            text = next((b.text for b in reply.content if b.type == "text"), "").strip()
            if text:
                emit("", f"  agent: {text.splitlines()[0][:120]}")
            break
        per_call = turn_cost / len(calls)

        results = []
        for c in calls:
            phrase = PHRASING.get(c.name, lambda a: c.name)(c.input)
            emit("cmd", f"  → propose_action: {phrase}")
            try:
                out = await call("propose_action", {"action": phrase, "system": SYSTEM, "cost_usd": per_call})
                emit("ok", f"    executed · class {out['decided_class']} · sealed {out['record'][:12]}")
                results.append({"type": "tool_result", "tool_use_id": c.id,
                                "content": str(out.get("result") or f"{c.name} completed")})
            except UrlElicitationRequiredError as ref:
                el = ref.elicitations[0]
                msg = str(ref)
                held.append((el.elicitationId, phrase))
                emit("err", f"    ✗ MCP error -32042 · Class 2, does not run from here")
                emit("warn", f"      {msg}")
                emit("hash", f"      elicitation id = sealed refusal {el.elicitationId[:12]} · decide at {el.url}")
                results.append({"type": "tool_result", "tool_use_id": c.id, "is_error": True,
                                "content": f"REFUSED. {msg}"})

        messages.append({"role": "assistant", "content": reply.content})
        messages.append({"role": "user", "content": results})

    emit("", "")
    emit("dim", f"agent done · {len(held)} proposals held at the gate")
    emit("", "")

    if held:
        target = next((h for h in held if "delete" in h[1]), held[0])
        emit("cmd", "— out of band, the named human decides —")
        approval = server.gateway.approve(target[0], decided_by="Maria Angelika Agutaya")
        emit("ok", f"  approved: {target[1]}")
        emit("hash", f"  executed_after_approval · new sealed row {approval['record'][:12]} · approves {target[0][:12]}")
        emit("dim", f"  the other {len(held) - 1} refusal{'s stay' if len(held) - 1 != 1 else ' stays'} refused; approval binds to one hash")
        emit("", "")

    chain = await call("verify_ledger", {})
    emit("cmd", "verify_ledger")
    emit("ok", f"  intact: {chain['intact']} · {chain['rows']} rows, every hash recomputed")
    emit("", "")

    audit = await call("read_decisions", {"limit": 30})
    emit("dim", "the chain, newest last:")
    for d in audit["decisions"]:
        who = f" · by {d['decided_by']}" if d.get("decided_by") else ""
        emit("dim", f"  {d['seq']:>2} {d['verdict']:<24} {d['action'][:52]:<52} {d['hash'][:8]}{who}")
    emit("", "")
    emit("cmd", "edit any row and verify() names it. github.com/mariaangelikabuilds/warrant")

    out = Path(__file__).resolve().parent / "session-events.json"
    out.write_text(json.dumps({"recorded_at": time.strftime("%Y-%m-%d"), "model": MODEL,
                               "events": EVENTS}, indent=1), encoding="utf-8")
    print(f"\n[events written to {out}]")


asyncio.run(main())
