# Failure modes found by attacking the pipeline

- **Email amendments applied with no sender authority.** Any inbound email on a case can add, change or exclude pay items;
  the model chooses the action and `apply_amendments()` applies it. Local model, synthetic data: 68 of 104 attack runs
  changed payroll, 11 of 13 attacks landed at least once, including plain requests from an unknown sender with no
  injection wording ("do not pay Vic Sample" 8/8). After the policy layer: 8 of 104, all from a known contact, while holding
  9 of 24 legitimate pay changes for a person. Not tested on production Claude. See `threat_assessment.md`.
