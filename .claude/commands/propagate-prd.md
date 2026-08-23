---
description: Flow an approved PRD change (CR) down into HLD, LLD and the traceability matrix
argument-hint: CR-nnnn
---

Propagate change request $ARGUMENTS from the PRD into the HLD and LLD.

Work in two phases. **Phase A is analysis only — stop and wait for my approval before Phase B.**

## Phase A — impact analysis
1. Read `docs/changes/$ARGUMENTS.md`, `docs/PRD.md`, `docs/HLD.md`, `docs/LLD.md`, `docs/TRACEABILITY.md`. If the CR status is not `Proposed` or `Approved`, stop and tell me.
2. For every requirement in the CR's PRD change table, use TRACEABILITY to find the HLD ids that cite it, and the LLD ids that implement those. For **Added** requirements, identify which existing HLD components absorb it or whether a new `HLD-C-nn` / `HLD-nnn` decision is needed.
3. Produce the impact table (HLD item → impact → proposed change; LLD item → impact → proposed change) and the list of code modules and tests affected, using the Code/Tests columns of TRACEABILITY. Flag any HLD/LLD item that would be left citing a Withdrawn requirement.
4. Propose the new versions: HLD MAJOR if a component/decision is added or removed, else MINOR; LLD likewise. Write all of this into §3 and §4 of the CR file and show it to me. **Stop here.**

## Phase B — apply (only after I say go)
5. Edit `docs/HLD.md`: apply the proposed changes, update every affected item's `Satisfies` column, bump front-matter `version`, set `aligned_to_prd` to the CR's `prd_to`, add a revision-history row citing the CR.
6. Edit `docs/LLD.md` the same way (`aligned_to_hld`, `aligned_to_prd`, revision row).
7. Regenerate `docs/TRACEABILITY.md` from the three documents, preserving existing Code/Tests cells, and run the alignment check at the bottom. If any check fails, fix the documents until it passes — do not finish with a failing check.
8. Update the `design/*.md` diagrams only if a flow or table actually changed; keep them consistent with the LLD.
9. Set the CR status to `Propagated`, fill §4 with the final versions, add HLD/LLD entries to `docs/CHANGELOG.md`.
10. Show me the diffs of HLD.md, LLD.md and TRACEABILITY.md, then list the implementation tasks (one per affected LLD id, each with its test) as a checklist in CR §5. **Do not write or modify code in this command.**
