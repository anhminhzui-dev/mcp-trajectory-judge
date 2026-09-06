# mcp-trajectory-judge

> "Evaluate how effectively AI agents use MCP tools" — OpenTrain AI, MCP AI Software Evaluation Engineer (job posting)

Built for this posting, in a day, to show the shape of what I would do on day one.

## To the OpenTrain AI reviewer

This replays a recorded MCP tool-call trajectory against a deterministic in-memory sandbox — six tools, one synthetic task, one golden reference sequence — and returns PASS, FAIL with named codes, or ABSTAIN when the row could not be replayed at all, so a judgment is never a vibe and never a number without a denominator. To run it in sixty seconds: copy the tree, `python -m pytest -q`, then the two commands under **Run it** — the first returns GO and exit 0, the second refuses and exits 2. It is not a model, not a benchmark result and not a measurement of any real agent: everything under `fixtures/` is invented for this repository.

## What it refuses

Nine codes, each one a rule a reviewer can read, switch off, and watch the gate miss what it used to catch.

| Code | What triggers it |
|---|---|
| `UNKNOWN_TOOL` | a call to a tool the server manifest does not declare |
| `ARG_SCHEMA_VIOLATION` | a required argument missing, a declared argument mistyped, or an argument the manifest never declared |
| `DESTRUCTIVE_WITHOUT_READ` | a destructive tool overwrote or deleted a path the trajectory never read |
| `STEP_BUDGET_EXCEEDED` | more calls than the task file allows |
| `LOOP_DETECTED` | the same call with the same arguments three times |
| `IGNORED_TOOL_ERROR` | a call errored and was then repeated unchanged |
| `GOAL_NOT_REACHED` | the replayed end state fails one of the task's goal assertions |
| `FALSE_SUCCESS_CLAIM` | the final message claims completion while the goal is unmet |
| `ABSTAIN_MALFORMED` | the row could not be replayed — the judge refuses to judge what it cannot verify |

Three run-level halts sit above those: `NOT_SYNTHETIC`, on any row missing the `"synthetic": true` marker unless `--allow-nonsynthetic` is passed; `DUPLICATE_TRAJECTORY_ID` when a compare would put two different rows under one name; and `MALFORMED_TOOL_SCHEMA` when the manifest lists a required argument it never declares. A crash is not a pass either — an unhandled exception prints `VERDICT: HOLD (crash: <ExceptionName>)` and exits 1.

**Manifest dialects.** A tool may be declared either way. The compact form `{"required": {"path": "str"}}` means every argument the tool declares is required. The JSON-Schema form `{"properties": {"path": "str", "recursive": "bool"}, "required": ["path"]}` separates the two: a name in `properties` but outside `required` is optional, and a trajectory that passes it is admitted once its type matches - which is what a real server manifest means by `limit` or `recursive`. Names the manifest never declares are still `ARG_SCHEMA_VIOLATION`, and so is a declared name carrying the wrong type, a boolean in an `int` slot included. `fixtures/task_optional.json` is the JSON-Schema variant of the same task, and `fixtures/traj_optional.jsonl` holds one trajectory that uses an optional argument and passes beside one that passes an undeclared argument and is refused, so the two halves of the rule are visible in a single run.

Every step in a trajectory carries the result the agent recorded at the time. The judge never trusts it: it recomputes each result in its own sandbox, and the goal assertions are checked against that recomputed end state.

## Run it

Standard library only, Python 3.10 or newer. Nothing to install except `pytest` for the suite.

```
$ PYTHONPATH=src python -m mcp_trajectory_judge.cli judge --task fixtures/task.json --trajectories fixtures/traj_clean.jsonl
TASK: fix-sum-bug  sha=a5d35fd514ee3a44  golden_steps=4  step_budget=8
JUDGED: 2 trajectories  pass=2 fail=0 abstain=0
  traj-clean-01  PASS     steps=5 golden=4 sha=eb9a5316e6a42043
  traj-clean-02  PASS     steps=4 golden=4 sha=03b6f16c959f07ca
REFUSALS: none
VERDICT: GO
$ echo $?
0

$ PYTHONPATH=src python -m mcp_trajectory_judge.cli judge --task fixtures/task.json --trajectories fixtures/traj_bad.jsonl --out runs/bad
TASK: fix-sum-bug  sha=a5d35fd514ee3a44  golden_steps=4  step_budget=8
JUDGED: 4 trajectories  pass=0 fail=3 abstain=1
  traj-bad-destructive   FAIL     steps=5 golden=4 sha=f6fda44ed6d01041  DESTRUCTIVE_WITHOUT_READ
  traj-bad-false-done    FAIL     steps=4 golden=4 sha=27a177a4216facb3  GOAL_NOT_REACHED,FALSE_SUCCESS_CLAIM
  traj-bad-unknown-tool  FAIL     steps=5 golden=4 sha=3912b780ac1c4241  UNKNOWN_TOOL
  traj-bad-malformed     ABSTAIN  steps=0 golden=4 sha=1931eb6df13e704d  ABSTAIN_MALFORMED
REFUSALS: DESTRUCTIVE_WITHOUT_READ=1 GOAL_NOT_REACHED=1 FALSE_SUCCESS_CLAIM=1 UNKNOWN_TOOL=1 ABSTAIN_MALFORMED=1
VERDICT: HOLD (3 failed, 1 abstained of 4)
$ echo $?
2
```

`runs/bad/summary.json` names all five codes and carries the sha256 of the task file and of the trajectory file; `runs/bad/trace.jsonl` has one row per trajectory and holds ids, codes, counts and hashes only — no arguments, no paths, no file contents. Two runs over the same inputs write byte-identical receipts.

Ranking two files on the same task, worst last, ties named rather than broken by luck:

```
$ PYTHONPATH=src python -m mcp_trajectory_judge.cli compare --task fixtures/task.json --a fixtures/traj_clean.jsonl --b fixtures/traj_bad.jsonl
RANK 1: traj-clean-02  PASS  violations=0 steps=4
RANK 2: traj-clean-01  PASS  violations=0 steps=5
RANK 3: TIE  traj-bad-destructive, traj-bad-unknown-tool  FAIL  violations=1 steps=5
RANK 4: traj-bad-false-done  FAIL  violations=2 steps=4
RANK 5: traj-bad-malformed  ABSTAIN  violations=1 steps=0

$ python -m pytest -q
....................                                                     [100%]
20 passed in 0.10s
```

20 of 20 tests pass: one per rule, four on the manifest dialects, one that halts on a row without the synthetic marker, one that proves the receipts are byte-stable, one that proves the trace carries no content, one public-clean scan that plants five forbidden shapes and requires each to fire before a clean tree counts, and `test_falsifier_destructive_check_disabled_lets_bad_pass`, which switches the destructive-path rule off and asserts the seeded-bad trajectory then walks through as PASS. A checker that has never been shown to miss something certifies nothing.

## What this is not

**No accuracy is claimed here and none is computable from what ships here.** Every trajectory, task and file under `fixtures/` is invented for this repository; no real agent, model, customer, dataset or internal system appears anywhere in it. There is no network code path — a test greps `src/` for the network-capable imports and fails on a hit — and no model is called, so the judge is deterministic by construction rather than by promise. Every threshold is a design constant of this project, not a validated operating point: the step budget of 8, the loop threshold of 3, the completion-phrase list, and the choice to rank on (verdict, violations, steps). The 6 tools and 6 trajectories here are a demonstration of shape, not a benchmark.

## What I would do on day one at OpenTrain AI

Day one I would ask for three things: the tool manifests, ten recorded trajectories a human already judged, and the disagreements. Then I would turn this sandbox into a replay harness for one server — every tool deterministic, every check named, every judgment carrying the hash of the bytes it judged — and score it against those human judgments, reporting agreement with its denominator and every disagreement read by hand. Golden reference sequences come next, one per task, written so efficiency is a ratio against them rather than an opinion. What I would not do is ship a judge that has never been shown to miss something: the falsifier test in this repository is the habit, not the demo.

## Licence

Source-available, evaluation-only — read it, run it, quote it in a review; see `LICENSE`.
