---
name: reconcile-codex-sidebars
description: Audit and safely reconcile Codex Desktop projects and threads with the catalogue used by Codex Remote on iPhone. Use when Desktop and Remote show different thread groupings, projects or threads appear missing, project assignments need repair, throwaway threads need recoverable archival, or the Desktop project and per-project thread order should be alphabetical.
---

# Reconcile Codex Sidebars

Reconcile the shared Codex task catalogue with Desktop's local project metadata,
then mirror that reviewed grouping into synchronized App Server thread sections.
Keep every live operation read-only until the user has reviewed the proposed
archives, assignments, projects, sections, and ordering.

## Workflow

1. Read [contracts.md](references/contracts.md) and confirm that the installed
   Codex contracts still match. Stop before mutation if they differ.
2. Run `scripts/reconcile.py inventory`. Save detailed output only in an ignored
   private directory.
3. Read unmatched thread context through supported App Server `thread/read`
   calls. Classify durable, intentionally projectless, duplicate, and completed
   coordinator/worker threads. Never infer throwaway status from age alone.
4. Present exact proposed archives and destinations. Archive only after explicit
   approval, using App Server `thread/archive`; verify each thread is recoverable
   with an archived `thread/list` query.
5. Create a private JSON spec from `references/spec.example.json`. Prefer thread
   IDs for explicit rules; use title plus cwd only when the ID is unavailable.
6. Run `build-plan`, then `audit-plan`. Require zero missing, zero overlap, zero
   invalid projects, and alphabetical project/per-project thread orders.
7. Show the user the counts and named project changes without exposing private
   IDs. Obtain approval to apply and restart Desktop.
8. On macOS, run `arm-macos-restart`; ask the user to quit Codex normally. The
   one-shot writes a backup, rejects stale state, atomically applies the plan,
   verifies it, and relaunches Codex. Never edit state while Desktop runs.
   If the user changes direction before quitting, run `disarm-macos-restart`.
9. After relaunch, run `check-macos-restart` and `verify-state`. Follow
   [recovery.md](references/recovery.md) if a check fails.
10. Run `scripts/sync_sections.py build-plan` into the ignored private
    directory. Review the exact section names and counts. Preserve existing
    `Pinned`; refuse unexpected or duplicate server sections.
11. Run `sync_sections.py apply-plan`. It creates missing sections through
    `threadSection/create`, moves tasks through `thread/section/move`, rolls back
    partial writes on failure, and verifies every assigned and projectless task.
12. Run `sync_sections.py verify`, inspect Desktop, then ask the user to refresh
    and confirm iPhone Remote. Server verification proves synchronized section
    state; only the user can prove the final mobile rendering.

For later reconciliation runs, preserve `Pinned` as an overlay, ignore tasks
that have since been archived, and adopt reviewed tasks already placed in a
project-named synchronized section even when Desktop's local project metadata
has not yet caught up. New unsectioned tasks still require classification.

## Commands

Run `python3 scripts/reconcile.py --help` for all options. Typical private flow:

```bash
python3 scripts/reconcile.py inventory --output /private/inventory.json
python3 scripts/reconcile.py build-plan \
  --spec /private/spec.json --output /private/plan.json
python3 scripts/reconcile.py audit-plan --plan /private/plan.json
python3 scripts/reconcile.py arm-macos-restart --plan /private/plan.json
python3 scripts/sync_sections.py build-plan \
  --output /private/section-plan.json
python3 scripts/sync_sections.py apply-plan \
  --plan /private/section-plan.json \
  --receipt /private/section-receipt.json
python3 scripts/sync_sections.py verify \
  --plan /private/section-plan.json
```

Use `--threads-json` only for synthetic tests or an intentional offline audit.
Never commit inventory, spec, plan, receipt, backup, or transcript data.

## Safety invariants

- Use App Server for thread listing, reading, archive, and restore operations.
- Treat Desktop project assignments and ordering as versioned local state.
- Treat App Server thread sections as the synchronized cross-client grouping.
- Sort Desktop projects and custom sections with Desktop's supported reorder
  operations. Do not claim that App Server synchronizes section order; its
  section contract currently exposes names, appearance, and membership only.
- Refuse direct Desktop JSON mutation while Desktop is running. Apply
  synchronized sections only through App Server methods.
- Refuse stale plans and plans with unmatched or ambiguous active threads.
- Back up before replacement; write and fsync a temporary file; replace
  atomically; verify only owned sidebar fields after relaunch.
- Preserve explicitly projectless threads and archived assignments unless the
  user explicitly changes them.
- Do not move or rename workspaces merely because a sidebar assignment changes.
- Keep Remote verification distinct from Desktop metadata verification.
