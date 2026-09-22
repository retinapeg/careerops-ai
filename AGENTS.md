# Engineering guidance

- Inspect the current branch, status and relevant code before changing anything.
  Preserve concurrent edits and never reset or clean another task's work.
- Keep this repository generic. Use synthetic fixtures; never commit a personal
  profile, CV, database, contact detail, source document, private path or credential.
- Runtime data belongs in ignored `local_data/`. Do not read or migrate another
  workspace's data without an explicit request.
- Candidate facts come from approved evidence. Preserve uncertainty and conflicts;
  never invent qualifications, employment, results or eligibility.
- Keep the generator and both reviewers independent of mutable live state. Freeze
  inputs, retain stage receipts and preserve previous document versions.
- Reviewers receive the same initial packet and do not see their peer's response.
  Do not describe sequential execution as parallel or mocked checks as live calls.
- Preserve duplicate-request protection, bounded revisions and explicit handling
  of uncertain dispatches. A failed review must never appear as a passed review.
- Keep applications and messages under human control. Do not add automatic sends,
  submissions, paid-provider fallbacks or background searches without authorization.
- Reuse existing code and standard-library features before adding dependencies or
  abstractions. Keep the UI clear, keyboard accessible and free of implementation
  detail unless it helps the user make a decision.
- Run the checks relevant to the change. Use temporary synthetic databases and
  mocked providers by default; distinguish those results from actual model runs.
- Before publishing, inspect the complete staged tree for private artifacts and
  verify that runtime data and secrets remain ignored.
