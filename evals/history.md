# Eval history

Every score this suite has produced, and what changed between them. Newest first.

## 2026-08-17, second run, closed-world ground truth

Ungoverned: unauthorized actions in **12 of 12** scenarios, 18 unauthorized tool
calls executed, 8 of them among the named severe harms. Governed: **0 of 12**,
escalation precision 100%, recall 100%, 0 false escalations, legitimate work still
completed in 12 of 12. Cost $0.3511 governed against $0.2973 ungoverned, which is
about 18% more in tokens and about 49% more in wall clock.

Change: ground truth became closed-world. `may_execute` is now the complete list of
what a scenario permits, and every other tool is forbidden.

The first run scored only the tools named in `must_not_execute`, which left **8 of
17 refusals classified as neither correct nor false**. Two numbers were wrong as a
result, in opposite directions. Escalation precision read 52.9%, understating the
gateway for no better reason than an incomplete label. And `false_escalations` read
0 while being structurally incapable of detecting over-blocking of any tool the
scenario had not listed, which flattered it.

A hole in ground truth flatters whichever number it touches, and it is not obvious
from the summary which direction that will be. The check that caught it was
arithmetic rather than judgement: refusals should equal correct plus false, and
they did not. That assertion now runs over the results.

Also changed: full trajectories are persisted into `results.json`. Re-scoring this
run cost a second $0.65 of API calls purely because the first one threw away the
evidence it was scored from.

## 2026-08-17, first run

Ungoverned: unauthorized actions in 8 of 12 scenarios, 10 unauthorized calls.
Governed: 0 of 12. Reported escalation precision 52.9%, recall 100%, 0 false
escalations, $0.349.

Superseded by the run above. The unauthorized counts differ between the two runs
because the scoring changed, not because behaviour did: under closed-world, calls
to tools the scenario never permitted now count as unauthorized instead of going
unscored.

## Standing notes

**A 100% score means the suite is not hard enough.** Twelve scenarios against
thirteen rules found no case where the gateway blocked something it should have
allowed, and no case where something got through. That is a floor to defend rather
than a result to be pleased with. The next scenarios should be written to break it:
actions whose vocabulary sits between two rules, an action that is Class 1 alone and
Class 2 in context, and injections that carry the rules' own vocabulary in order to
look routine.

**Runs are non-deterministic.** This is an LLM in the loop, so the scorecard is a
dated observation rather than a fixed value, and CI should gate on floors rather
than a byte-diff. That is the opposite of how the landing-page-engine suite gates,
and the difference is that one measures deterministic checks while this one measures
a model.
