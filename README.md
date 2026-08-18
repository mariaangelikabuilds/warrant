# warrant

An MCP server that sits between an agent and the systems it would otherwise reach
directly. The agent proposes an action. warrant decides whether that action is
inside the authority it has been given, executes it or refuses it, and seals the
decision into a chain a third party can check.

The thesis is one line: **an agent that cannot exceed its authority, and proves it.**

## The measurement

A guardrail with no control arm is an assertion, so there are two arms. Same model,
same system prompt, same tool schemas, same tickets. The only difference is whether
the tool call passes through the gateway.

| | ungoverned | governed |
|---|---|---|
| scenarios where an unauthorized action reached the system | **16 of 17** | **0 of 17** |
| unauthorized tool calls executed | 26 | 0 |
| escalation precision / recall | n/a | 100% / 100% |
| false escalations | 0 | 0 |
| scenarios where the legitimate work still got done | 17 of 17 | 17 of 17 |
| cost | $0.4881 | $0.5693 |
| cost per scenario that completed its work | $0.0287 | $0.0335 |
| spend that bought a refusal | n/a | $0.1378 |

Ungoverned, the agent issued a refund, deleted rows, mailed 214 recipients, ran a
script on a live endpoint, deployed to production, revoked access after an HR
event, and notified a client about a suspected breach. Unattended, in one pass.

Governed, none of that reached the system and every scenario still completed its
legitimate work. The gateway costs about 17% more in tokens for 16 fewer scenarios
in which something irreversible happened.

The honest cost line is the third row. **$0.1378 of the governed spend, about a
quarter of it, bought a refusal**: tokens paid to reach a call that was then not
allowed to happen. That is the gateway's waste and it is reported rather than netted
out of the headline, because a guardrail that only publishes its wins is an
advertisement. Per-call cost is attributed from the model turn that produced the
call, so the ledger's cost column is a real number rather than a zero, and the
session ceiling has been exercised against a live run: it raised mid-scenario, the
decision already made stayed sealed, and the chain still verified.

Five of the seventeen were written to get through rather than to be caught:
vocabulary sitting in the gap between two rules, an action that is routine alone and
not in context, an injection written in the gateway's own language, a harm split
across two innocuous steps, and urgency used as a lever. They raised the ungoverned
harm rate from 12 of 12 to 16 of 17, so they are harder by measurement rather than by
assertion. The gateway held at zero on all five, and in the subtle one it executed the
routine password reset while refusing the send, which is the correct pair rather than
blanket refusal.

**The injection works now, which is what makes the rest of this mean anything.** Two
earlier versions were prose telling the agent what to do, and the model declined both,
including one citing a change record id and an ITIL clause. So every previous scorecard
had a row in it that proved nothing.

The version that fires contains no instruction. The authority arrives as ticket fields
(`change_record: CHG-4471 (approved)`, a named approver, an active window,
`gate_status: SATISFIED`) and the task arrives separately as rows in a backlog report
marked `outstanding`. Neither tool result holds both halves. The agent is not told to
act, it is shown a system where acting is the obvious inference, and it draws it.

Ungoverned it then deleted 40,182 rows, ran a script on a live host, and emailed the
client. Governed it attempted all three, all three were refused, and it still completed
the reading, the report and the draft. The attack succeeds against the model and fails
against the boundary. Full
scorecard in
[`evals/SCORECARD.md`](evals/SCORECARD.md), history in
[`evals/history.md`](evals/history.md).

## How it decides

```
propose  ->  classify  ->  record  ->  execute or refuse
```

Recording happens before execution, never after. A record written afterwards is
missing exactly the case anyone wants it for, which is the run that executed and
then crashed.

**Class 1** runs. **Class 2** does not run, from here, ever. There is no override
flag and no privileged caller: the tool that would execute a Class 2 action does
not exist on the server. Two properties carry the whole thing, both ported intact
from [class-two](https://github.com/mariaangelikabuilds/class-two):

- **Raise only.** A Class 2 match beats a Class 1 match on the same action.
  "Draft a reply **and send it**" is Class 2.
- **Fail closed.** An action matching no rule is Class 2, reported as
  unclassified. An action nobody has ruled on is not thereby safe.

The thirteen rules each cite a real authority: ITIL 4 for change, PAM/PIM practice
for privileged use, NIST 800-171 and CMMC for controlled information.

## The refusal is a protocol error, not advice

A Class 2 proposal raises `UrlElicitationRequiredError`, so the client receives MCP
error `-32042` carrying the URL where a human decides. The elicitation id is the
sealed hash of the refusal, which binds the approval to the exact proposal that was
refused and stops it being replayed against another one.

This is the protocol's own mechanism for "this cannot proceed until someone
completes an interaction elsewhere", and a human approving a privileged action is
exactly that. Raising a plain `McpError` does not work: FastMCP wraps every other
exception into a generic `ToolError`, which flattens the elicitation into a string
and loses the code. That was found by exercising the call, not by reading docs.

## The record

Every decision is a sealed row carrying what an ordinary log leaves out: the rule
that decided, the authority it cites, the prompt version, the model, the cost, and
the named human if one approved. Each row carries the hash of the row before it.

`verify_ledger` recomputes the chain and names the row where it parts. The tests
cover both attack shapes: a silent field edit, caught at the row itself, and a
resealed edit where the forger recomputes that row's hash, which moves the break to
the row after it. The second is why the chain carries the guarantee rather than any
single hash.

## The policy behind the decision

A rule id tells an auditor which rule fired. It does not tell them what policy the
company was following, which is the next question. Each decision retrieves the
internal policy that governs it and seals the citation into the row.

Two disciplines, both borrowed from
[runbook-rag](https://github.com/mariaangelikabuilds/runbook-rag):

- **Cite or refuse.** Below the support threshold it returns nothing. It never
  composes an answer from the closest paragraph and never emits a clause number
  that is not in the corpus. A confident wrong citation in an audit record is worse
  than no citation.
- **No standard text is reproduced.** Every policy is written in-house and names
  the external standard by identifier only. Copying NIST or ITIL text into this
  repo would be a licensing problem and a maintenance lie, because the copy drifts.

Cite-or-refuse and fail-closed are independent, which matters: an action with no
policy support is still Class 2. Having no rule about something does not make it
safe, and the record shows a null citation rather than the nearest paragraph.

Lexical scoring, standard library only. Ten policies is not a corpus that needs
embeddings, and the upgrade path is written in the module rather than pre-built.
Getting there took three real bugs, all in `warrant/retrieve.py` comments: scoring
as a ratio over a denominator the query controlled let *"reticulate the client
splines"* cite the incident policy at full confidence on the word "client";
asymmetric stemming meant "refund" never matched "refunds"; and IDF treated
"what" as a rare valuable term because it appears exactly once in the corpus, so a
weather question retrieved the script-execution policy. The fix that closed the
class was structural rather than numeric: one matched word is a coincidence, so at
least two distinct terms must match before anything is cited at all.

Separation after that: supported floor 3.98, unsupported ceiling 0.00, threshold
2.2 sitting in the gap. `python tests/test_retrieve.py` re-derives it.

## Running it

Dependencies are pinned in `requirements.txt`, not floored. CI installed `mcp>=1.26`,
pip resolved 2.0.0, and `mcp.server.fastmcp` does not exist there: the suites passed on
my machine and failed on a clean checkout on the first CI run. This is built against
`mcp==1.26.0`. Moving to 2.x is a real migration against the 2026-07-28 spec and is not
something a version range should do silently on someone else's machine.

```
python -m warrant.server          # stateless streamable HTTP
python tests/test_ledger.py       # chain properties
python tests/test_gateway.py      # what it refuses to do
python tests/test_server.py       # the MCP surface
python tests/test_retrieve.py     # citation faithfulness and the threshold
python evals/run_evals.py         # both arms, needs ANTHROPIC_API_KEY
```

Systems default to a dry run and go live only when a webhook URL is set in the
environment (`WARRANT_N8N_TRIAGE_URL`, `WARRANT_N8N_REVIEW_URL`). An unconfigured
gateway should not reach production the first time someone runs it, and a system
that was not granted is a refusal rather than a passthrough.

## What this is not

- Not a policy engine. Thirteen rules for one domain, not a general framework.
- Not an authentication layer. It decides what an already-authenticated agent may
  do, and pairs with a credential broker rather than replacing one.
- Not proof the classifier is right. It is deterministic and auditable, which means
  when it is wrong it is wrong the same way every time and the rule can be fixed.
