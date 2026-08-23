---
description: Make a versioned change to docs/PRD.md and open a Change Request
argument-hint: <short title of the change>
---

You are making a controlled change to the Product Requirements Document. Title: $ARGUMENTS

Follow these steps exactly, showing your work:

1. Read `docs/PRD.md`, `docs/CHANGELOG.md`, and list `docs/changes/` to find the highest existing CR number and the highest existing `PRD-F-` / `PRD-N-` ids.
2. Ask me what the change is if it is not already clear from the title and conversation. Restate it as one or more requirement-level edits: **Add** (new id), **Modify** (same id, new wording, note what changed), or **Withdraw** (status → Withdrawn, keep the row). Never reuse or renumber ids.
3. Decide the version bump: MAJOR (x+1.0) if any requirement is added or withdrawn; otherwise MINOR (x.y+1). Tell me and wait for confirmation.
4. Apply the edits to `docs/PRD.md`: requirement tables, the front-matter `version` and `date`, and a new row in §8 Revision history citing the CR id.
5. Create `docs/changes/CR-nnnn.md` from `docs/changes/_TEMPLATE.md` with sections 1–2 filled (Why, PRD change table). Leave §3–5 for `/propagate-prd`. Set `status: Proposed`.
6. Add a `PRD x.y` entry to `docs/CHANGELOG.md` referencing the CR.
7. Show me the diff of `docs/PRD.md`. Do **not** touch `HLD.md`, `LLD.md`, `TRACEABILITY.md`, or any code.
8. Finish by telling me the command to run next: `/propagate-prd CR-nnnn`, and remind me to `git tag docs/prd-vX.Y` after commit.
