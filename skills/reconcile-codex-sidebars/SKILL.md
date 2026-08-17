---
name: reconcile-codex-sidebars
description: Audit and safely reconcile Codex Desktop projects and threads with the catalogue used by Codex Remote on iPhone. Use when Desktop and Remote show different thread groupings, projects or threads appear missing, project assignments need repair, throwaway threads need recoverable archival, or the Desktop project and per-project thread order should be alphabetical.
---

# Reconcile Codex Sidebars

Reconcile the shared Codex thread catalogue with Desktop's local project
presentation metadata. Keep every live operation read-only until the user has
reviewed the proposed archives, assignments, new projects, and ordering.

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
9. After relaunch, run `check-macos-restart` and `verify-state`, then inspect the
   Desktop sidebar. Follow [recovery.md](references/recovery.md) if a check fails.
10. Ask the user to confirm the iPhone Remote view. App Server can verify the
    shared catalogue, but it cannot prove the final client-side mobile layout.

## Commands

Run `python3 scripts/reconcile.py --help` for all options. Typical private flow:

```bash
python3 scripts/reconcile.py inventory --output /private/inventory.json
python3 scripts/reconcile.py build-plan \
  --spec /private/spec.json --output /private/plan.json
python3 scripts/reconcile.py audit-plan --plan /private/plan.json
python3 scripts/reconcile.py arm-macos-restart --plan /private/plan.json
```

Use `--threads-json` only for synthetic tests or an intentional offline audit.
Never commit inventory, spec, plan, receipt, backup, or transcript data.

## Safety invariants

- Use App Server for thread listing, reading, archive, and restore operations.
- Treat Desktop project assignments and ordering as versioned local state.
- Refuse mutation while Desktop is running.
- Refuse stale plans and plans with unmatched or ambiguous active threads.
- Back up before replacement; write and fsync a temporary file; replace
  atomically; verify only owned sidebar fields after relaunch.
- Preserve explicitly projectless threads and archived assignments unless the
  user explicitly changes them.
- Do not move or rename workspaces merely because a sidebar assignment changes.
- Keep Remote verification distinct from Desktop metadata verification.
