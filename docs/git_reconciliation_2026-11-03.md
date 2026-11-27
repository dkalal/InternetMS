# Office source reconciliation ? 2026-11-03

Office baseline: `ba89500a138b067f4243e556598a9bee2b12b884` on
`feature/tenant-sku-sequence`, 18 dirty tracked paths and six selected new source/
handoff files. The isolated candidate preserves every modified tracked path.
Local logs, patches, operational scripts/plans, exports, screenshots and sample
business documents remain private. No live database or deployment action belongs
to this reconciliation.

## Preservation

A private backup outside all worktrees contains separate binary staged/unstaged
patches, untracked files, relevant ignored source/configuration, local refs and an
all-refs Git bundle. File hashes, bundle verification and patch applicability were
checked. Configuration and manifests are private. This is source preservation,
not a database/media backup. Runtime directories and databases were not copied;
an existing access-restricted backup directory was left untouched.

## Branch reconciliation

The table compares initial refs with the office baseline. Ahead/behind means
candidate-only/ref-only commit counts. Patch-equivalent commits need no replay.

| Branch/ref | Head | Ahead/behind | Unique patches | Equivalent patches | Dependency | Disposition |
|---|---|---|---|---|---|---|
| `agent/secure-multitenant-workflows` | `e2bee82` | 59/0 | 0 | 0 | none | Included ancestor; retain branch |
| `chore/defer-purchase-pack-conversion` | `f261c00` | 52/0 | 0 | 0 | none | Included ancestor; retain branch |
| `chore/governance-v2-purchasing-stabilization` | `f72309a` | 51/0 | 0 | 0 | none | Included ancestor; retain branch |
| `codex/premium-invoice-quotation-workspace` | `acd8954` | 16/0 | 0 | 0 | none | Included ancestor; retain branch |
| `feature/cart-lifecycle-management` | `d56b6a0` | 2/0 | 0 | 0 | none | Included ancestor; retain branch |
| `feature/product-images-pos` | `86c3952` | 56/0 | 0 | 0 | none | Included ancestor; retain branch |
| `feature/purchase-import-review-redesign` | `870b0a5` | 12/0 | 0 | 0 | none | Included ancestor; retain branch |
| `feature/tenant-sku-sequence` | `ba89500` | 0/0 | 0 | 0 | none | Included ancestor; retain branch |
| `feature/unified-purchasing-mixed-import` | `d188010` | 15/0 | 0 | 0 | none | Included ancestor; retain branch |
| `feature/unified-purchasing-phase-0b` | `6dd7d32` | 53/0 | 0 | 0 | none | Included ancestor; retain branch |
| `feature/unified-purchasing-phase-1` | `6dd7d32` | 53/0 | 0 | 0 | none | Included ancestor; retain branch |
| `feature/unified-purchasing-phase-2` | `dd2922a` | 50/0 | 0 | 0 | none | Included ancestor; retain branch |
| `feature/unified-purchasing-phase-2b` | `3567293` | 48/0 | 0 | 0 | none | Included ancestor; retain branch |
| `feature/unified-purchasing-phase-2c` | `8dfaca0` | 46/0 | 0 | 0 | none | Included ancestor; retain branch |
| `feature/unified-purchasing-phase-3a-supplier` | `917ca3a` | 43/0 | 0 | 0 | none | Included ancestor; retain branch |
| `feature/unified-purchasing-phase-3b-category` | `6568283` | 40/0 | 0 | 0 | none | Included ancestor; retain branch |
| `feature/unified-purchasing-phase-3c-product` | `dba55b5` | 38/0 | 0 | 0 | none | Included ancestor; retain branch |
| `feature/unified-purchasing-phase-4a-review` | `ddd29d5` | 36/0 | 0 | 0 | none | Included ancestor; retain branch |
| `feature/unified-purchasing-phase-4b-cart-readiness` | `9ff89e0` | 34/0 | 0 | 0 | none | Included ancestor; retain branch |
| `feature/unified-purchasing-phase-5a-paste-rows` | `2e6a636` | 32/0 | 0 | 0 | none | Included ancestor; retain branch |
| `integration/walk-in-shipping-release` | `d2766ab` | 22/0 | 0 | 0 | none | Included ancestor; retain branch |
| `main` | `bd26604` | 68/0 | 0 | 0 | none | Included ancestor; retain branch |
| `release/unified-purchasing-rc` | `d2766ab` | 22/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin` | `51e0d9a` | 67/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/agent/secure-multitenant-workflows` | `e2bee82` | 59/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/chore/defer-purchase-pack-conversion` | `f261c00` | 52/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/chore/governance-v2-purchasing-stabilization` | `f72309a` | 51/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/codex/premium-invoice-quotation-workspace` | `acd8954` | 16/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/feature/cart-lifecycle-management` | `36bfa1f` | 7/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/feature/product-images-pos` | `86c3952` | 56/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/feature/purchase-import-review-redesign` | `870b0a5` | 12/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/feature/tenant-sku-sequence` | `ba89500` | 0/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/feature/unified-purchasing-mixed-import` | `d188010` | 15/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/feature/unified-purchasing-phase-0b` | `6dd7d32` | 53/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/feature/unified-purchasing-phase-2` | `dd2922a` | 50/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/feature/unified-purchasing-phase-2b` | `3567293` | 48/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/feature/unified-purchasing-phase-2c` | `8dfaca0` | 46/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/feature/unified-purchasing-phase-3a-supplier` | `917ca3a` | 43/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/feature/unified-purchasing-phase-3b-category` | `6568283` | 40/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/feature/unified-purchasing-phase-3c-product` | `dba55b5` | 38/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/feature/unified-purchasing-phase-4a-review` | `ddd29d5` | 36/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/feature/unified-purchasing-phase-4b-cart-readiness` | `9ff89e0` | 34/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/feature/unified-purchasing-phase-5a-paste-rows` | `2e6a636` | 32/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/feature/walk-in-shipping-address` | `14fac4d` | 30/9 | 4 | 5 | historical release chain | Equivalent domain work; CI ported; canceling fix/revert omitted |
| `origin/fix/walk-in-sale-identity` | `2d7d90d` | 30/8 | 4 | 4 | historical release chain | Equivalent domain work; CI ported; canceling fix/revert omitted |
| `origin/main` | `51e0d9a` | 67/0 | 0 | 0 | none | Included ancestor; retain branch |
| `origin/release/unified-purchasing-rc` | `d2766ab` | 22/0 | 0 | 0 | none | Included ancestor; retain branch |

The five local-only cart-lifecycle commits already precede the office baseline;
none is duplicated. PR #1's identity change (`839d89f`) is equivalent to `1dcd648`;
PR #2's shipping change (`14fac4d`) is equivalent to `dbb8b61`. Their migration
files match the office baseline exactly. Existing model-state correction and
preflight documents are equivalent too. The below-cost wording commit and its
revert cancel out; neither is reapplied. CI is adapted from the historical PR.
Old draft PRs #1 and #2 remain preserved and their domain changes are superseded
by this candidate. This does not claim that either PR was merged.

## Explained differences from the office snapshot

- POS workspace, atomic first-write idempotency, activity attribution, checkout
  print redirect and receipt display are preserved with their tests.
- Invalid Unicode/oversized product identifiers are rejected safely; a regression
  reproduces the prior picker server error and covers noncanonical form IDs.
- Optional browser runners accept a configurable channel for Linux Chromium CI.
- New handoff attribution and runbook follow-up omit private office particulars;
  their original text is in the private backup. No operational script is included.
- Two already-published historical audit JSON files have their address values
  redacted. Originals are backed up; published history is not rewritten.
- CI, test-only browser requirements, contributor/release workflow, PR template
  and targeted line-ending policy are new delivery infrastructure.

CI compares the actual PR/push base with its head rather than checking the
synthetic merge commit against every parent. Two inherited extra EOF blank lines
are removed separately to satisfy the full reconciliation whitespace gate. No
repository-wide line-ending renormalization is performed.

## Migration and security review

No migration is introduced or rewritten. Existing published walk-in migrations
are preserved byte-for-byte; no numbering conflict requires a merge migration.
Fresh PostgreSQL migration and synthetic upgrade tests are release gates. Actual
office schema/readiness is a separate deployment preflight, not needed to test the
source baseline. Migration rollback may lose new snapshots/addresses; retain a
verified private predeployment backup and follow the owning domain contracts.

Authorization remains server-side. Workspace signatures bind tenant/actor and
expire; first writes lock the actor and persist atomically with audit events.
Existing cart mutations lock the parent and enforce scoped permissions. Checkout
retains atomic payment/stock processing and retry behavior. Receipt presentation
does not change persisted financial totals. Package conversion, box/meter sales
and barcode scanning remain deferred and are not enabled.

Future optional packaging planning may consider a 305-meter UTP box, separate box
and meter prices, whole meters, internal Box IDs and allocations across boxes.
This is context only; the current same-unit purchasing contract remains binding.
