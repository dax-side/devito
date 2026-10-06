# LOOP.md: the recurring cycle (read this, not the 14 docs)

The other docs are the reference. This page is the loop; `runner/ship.py` enforces it.
Why: across 4 tasks, the most common calibration verdict was "no local check ran", and 6 of
CBOR's 10 fails came from rules that already existed. Rules nobody enforces get skipped.

## Two lanes, always
- **Lane A**: the task on the platform.
- **Lane B**: the next task (scout, candidate card, prototype).
- Any time lane A waits on the platform (TQ/SQ, rollouts, FP, Auto Review, maintenance), work lane B.
  A day with lane A waiting and lane B empty shows as idle on `ship.py board`.
- Keep 2-3 vetted candidate cards ready. A card says: repo, base SHA, feature in one line,
  the five questions answered, lean LOC estimate, 1-2 stated traps, dedupe search done.

## The cycle (lane A)

| Step | Do | Done when |
|---|---|---|
| 0 New task | `ship.py init DIR --lang go\|python`; clone at SHA; run the base suite 5 times, list the flaky ones in test.sh's skip list | Skip list exists before any new code |
| 1 Design | About 10 behaviours, 1-2 stated traps (absent vs empty, a default, a wiring path). No tolerance traps. Estimate each trap's hit rate; product 20-30% | Traps written in the notes |
| 2 Build | Sentence, then failing test, then code. `ship.py trace DIR`, then fill each test's quote | `ship.py check` shows no F fails |
| 3 Evidence | `ship.py ev DIR proof -- <harness>`; `sweep -- <edge sweep>`; `pretq --file`; `presq --file` (Opus, fresh, Rule A) | Every E line clean |
| 4 Predict (first time only) | 4 fresh Sonnet agents, repo + description only; grade with the hidden tests; `ev DIR predict --file grades.txt --verdict ok` when 1-2 of 4 pass | `ship.py check DIR --rollouts` clean. 0/4: loosen. 3-4/4: tighten. Before any upload |
| 5 Ship | `ship.py check DIR` prints SHIP | Upload the bundle/ bytes only |
| 6 Wait | Lane B | Platform answers |
| 7 Log | `ship.py log DIR CHECK pass\|fail --local agree\|miss\|false\|none --cls "<rule>"` | Row in ledger |
| 8 Triage | Write the finding as a general rule. Fix it at the lowest layer that sees every request. Add one sweep row for the whole class. Collect every other pending change. | One edit, then back to 3 |

## Triage rules (from every real fail)
- **Fix the rule, not the finding.** If `log` prints REPEAT, the last fix was narrow.
- **TQ unfair:** the test asserts something no sentence states. Delete the assertion, or add the sentence. Never test randomness or uniqueness.
- **SQ on unstated behaviour:** a code fix only, with no hidden test (a test would be unfair at TQ).
- **SQ on a stated rule:** sweep every route (retry, poll, redirects, scheme changes, the user's own header or object) before re-uploading.
- **Advisory on a PASS:** log it. Fix it only if another change goes out anyway.
- **DQ:** fix every finding. The author writes the prose; I check facts and count words.
- **Rollouts:** read only the failing-test column. One test explaining most fails is the trap doing its work.

## Stop rules (ship.py prints these)
- TQ or SQ over 2 uploads: before another, write why local gates missed it.
- The same check fails 3 times, or the same rule repeats: decide continue / cut / drop with the user. Recommend one.
- A narrow fix is reward hacking on the grader. The goal is that the rule holds everywhere, not that the finding disappears.

## Weekly
`ship.py board ROOT`. Targets: TQ <= 2 and SQ <= 2 uploads per task, 0 no-local rows, 0 repeats, 0 idle days.
For each miss, add one line to the sweep or this page, and delete one line nobody used.
