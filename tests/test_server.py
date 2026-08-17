"""The MCP surface: what an agent can and cannot get from this server.

Run: python tests/test_server.py
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP = tempfile.mkdtemp()
os.environ["WARRANT_LEDGER"] = str(Path(TMP) / "ledger.db")
os.environ["WARRANT_BUDGET_USD"] = "1.00"
os.environ["WARRANT_APPROVAL_URL"] = "https://warrant.test/approve"

from mcp.shared.exceptions import UrlElicitationRequiredError  # noqa: E402
from mcp.types import URL_ELICITATION_REQUIRED  # noqa: E402

from warrant import server  # noqa: E402


def payload(result):
    """FastMCP hands back content blocks; the tool's dict is the JSON inside."""
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


async def main():
    tools = {t.name for t in await server.mcp.list_tools()}
    assert tools == {"propose_action", "verify_ledger", "read_decisions"}, tools
    assert "execute" not in " ".join(tools), "there is no tool that runs a Class 2 action"

    routine = payload(
        await server.mcp.call_tool(
            "propose_action",
            {"action": "draft a reply to the ticket", "system": "n8n:msp-ticket-triage", "cost_usd": 0.001},
        )
    )
    assert routine["verdict"] == "executed", routine
    assert routine["decided_class"] == 1, routine

    # A Class 2 proposal must fail at the protocol level, carrying the elicitation.
    # A generic ToolError would mean the agent got a string it could ignore.
    try:
        await server.mcp.call_tool(
            "propose_action",
            {"action": "reset the domain admin password", "system": "n8n:msp-ticket-triage", "cost_usd": 0.001},
        )
        raise AssertionError("a Class 2 proposal must not return normally")
    except UrlElicitationRequiredError as required:
        assert required.error.code == URL_ELICITATION_REQUIRED, required.error.code
        elicitation = required.elicitations[0]
        assert len(elicitation.elicitationId) == 64, "the handle is the sealed refusal hash"
        assert elicitation.url.endswith(elicitation.elicitationId), elicitation.url
        assert "security lead" in elicitation.message, elicitation.message

    unruled = None
    try:
        await server.mcp.call_tool(
            "propose_action",
            {"action": "reticulate the client splines", "system": "n8n:msp-ticket-triage", "cost_usd": 0.001},
        )
        raise AssertionError("an unruled action must fail closed, not run")
    except UrlElicitationRequiredError as required:
        unruled = required.elicitations[0].elicitationId

    audit = payload(await server.mcp.call_tool("read_decisions", {"limit": 10}))
    verdicts = [d["verdict"] for d in audit["decisions"]]
    assert verdicts == ["executed", "refused", "refused"], verdicts
    assert audit["systems"]["n8n:msp-ticket-triage"] == "dry run", audit["systems"]
    assert any(d["hash"] == unruled for d in audit["decisions"]), "the refusal is on the record"

    chain = payload(await server.mcp.call_tool("verify_ledger", {}))
    assert chain["intact"] is True and chain["rows"] == 3, chain

    print(
        "server self-test passed: three tools and none of them execute a Class 2 action, "
        "refusals raise -32042 with the sealed hash as the elicitation handle, "
        "unruled fails closed, every decision is on the chain and the chain verifies"
    )


if __name__ == "__main__":
    asyncio.run(main())
