"""warrant as an MCP server.

An agent connects here instead of to the systems themselves. It can propose an
action, read the decision record, and check the chain. It cannot execute a Class
2 action, because the tool that would do so does not exist: the only thing this
server will hand back for one is an elicitation telling the client a human has to
decide out of band.

That last part is the reason this is an MCP server at all rather than a library.
URL mode elicitation is the protocol's own mechanism for "this cannot proceed
until someone completes an interaction elsewhere", and a human approving a
privileged action is exactly that. The refusal is not advice the agent may
ignore; it is a protocol-level error carrying the URL where the decision happens.

Transport is stateless streamable HTTP with JSON responses, so the server holds
no session between calls and can sit behind an ordinary load balancer.
"""

import os

from mcp.server.fastmcp import FastMCP
from mcp.shared.exceptions import McpError, UrlElicitationRequiredError
from mcp.types import ElicitRequestURLParams, ErrorData

from .gateway import BudgetExceeded, Gateway
from .systems import build_systems, describe

LEDGER_PATH = os.environ.get("WARRANT_LEDGER", "var/warrant-ledger.db")
BUDGET_USD = float(os.environ.get("WARRANT_BUDGET_USD", "1.00"))
APPROVAL_BASE = os.environ.get("WARRANT_APPROVAL_URL", "https://warrant.invalid/approve")

systems = build_systems()
gateway = Gateway(ledger_path=LEDGER_PATH, budget_usd=BUDGET_USD, systems=systems)

mcp = FastMCP("warrant", stateless_http=True, json_response=True)


def _needs_human(outcome):
    """Turn a Class 2 refusal into the protocol's own out-of-band interaction.

    The elicitation id is the sealed hash of the refusal, so the approval a human
    completes is bound to the exact proposal that was refused and cannot be
    replayed against a different one.
    """
    record = outcome["record"]
    # UrlElicitationRequiredError specifically, not a bare McpError: FastMCP wraps
    # every other exception into a generic ToolError, which would flatten the
    # elicitation into an error string and lose the -32042 code. Tested, not assumed.
    raise UrlElicitationRequiredError(
        elicitations=[
            ElicitRequestURLParams(
                message=(
                    f"{outcome['decision']['action']}\n\n"
                    f"Authority: {outcome['authority']}\n"
                    f"Approver: {outcome['approver']}"
                ),
                url=f"{APPROVAL_BASE}/{record}",
                elicitationId=record,
            )
        ],
        message=(
            f"Class 2. {outcome['reason']} "
            f"Approver: {outcome['approver']}. "
            f"Until then the agent may {outcome['agent_may']}."
        ),
    )


@mcp.tool()
def propose_action(action: str, system: str, cost_usd: float = 0.0) -> dict:
    """Ask to perform one action against one system.

    Class 1 actions run and return the system's response. Class 2 actions do not
    run: the call fails with a URL elicitation naming the human who decides. Any
    action matching no rule is treated as Class 2, because an action nobody has
    ruled on is not thereby safe.
    """
    try:
        outcome = gateway.propose(action, system, cost_usd=cost_usd)
    except BudgetExceeded as stop:
        raise McpError(ErrorData(code=-32000, message=str(stop))) from stop

    if outcome["verdict"] == "needs_human":
        _needs_human(outcome)

    return {
        "verdict": outcome["verdict"],
        "decided_class": outcome["decision"]["class"],
        "rules": list(outcome["decision"]["rules"]),
        "authority": outcome["decision"]["authority"],
        "record": outcome["record"],
        "result": outcome.get("result"),
        "reason": outcome.get("reason"),
    }


@mcp.tool()
def verify_ledger() -> dict:
    """Recompute every hash in the decision chain and report where it parts."""
    ok, problems = gateway.verify()
    return {"intact": ok, "rows": len(gateway.ledger.rows()), "problems": list(problems)}


@mcp.tool()
def read_decisions(limit: int = 20) -> dict:
    """The sealed decision record, newest last, for reading an incident back."""
    rows = gateway.ledger.rows()[-max(1, min(limit, 200)):]
    return {
        "decisions": [
            {
                "seq": row["seq"],
                "recorded_at": row["recorded_at"],
                "action": row["action"],
                "class": row["decided_class"],
                "verdict": row["verdict"],
                "rules": row["rule_ids"],
                "authority": row["authority"],
                "decided_by": row["decided_by"],
                "approves": row["approves"],
                "hash": row["hash"],
            }
            for row in rows
        ],
        "systems": describe(systems),
        "budget_remaining_usd": round(gateway.budget.remaining(), 6),
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
