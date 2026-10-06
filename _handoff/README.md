# Handoff (resterm Digest task)

`digest_handoff.zip` contains:
- `resterm_digest/`: submitted files (solution.patch, test.patch, description.txt, SUBMIT.txt, md5.txt, CONTEST_NOTES.md)
- `rollout_analysis/`: cross-task rollout analysis (REPORT.md, runs.csv, harvest.py)
- `workflow_v2/`: playbook, lessons, rulings, HANDOFF_FRIDAY.md (start here)
- `digest_runs1-3/`: agent runs (trajectories omitted), `fpprobe_7d21/`: probe tests

Restore: `unzip _handoff/digest_handoff.zip -d <scratchpad>`, then rebuild the working copy by applying
`resterm_digest/test.patch` and `solution.patch` to unkn0wn-root/resterm at 2e2a3ffdcd73c53b442e0bc1771cc90abb5339fa.
