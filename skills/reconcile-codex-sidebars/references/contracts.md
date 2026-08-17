# Codex Sidebar Contracts

## Last verified shape

The workflow was verified on macOS against Codex Desktop's bundled App Server
and these local presentation fields in `~/.codex/.codex-global-state.json`:

- `local-projects`: project ID to project metadata, including `name` and
  `rootPaths`
- `project-order`: ordered project IDs
- `thread-project-assignments`: thread ID to `{projectKind, projectId}`
- `projectless-thread-ids`: explicitly unassigned thread IDs
- `sidebar-project-thread-orders`: project ID to explicit `threadIds`

The shared catalogue is available from App Server `thread/list`. Supported
thread operations include `thread/read`, `thread/archive`, and the restore
equivalent exposed by the installed version. Remote consumes the shared
catalogue but may apply its own client-side filters and presentation rules.
Desktop project metadata is local and is not itself the authoritative thread
store.

## Inputs used by the script

1. Installed App Server executable, discovered from `CODEX_EXECUTABLE`, the
   macOS ChatGPT/Codex application bundle, or `PATH`.
2. Desktop global state, discovered from `CODEX_HOME` or `~/.codex`.
3. Active thread records containing at least `id`, `cwd`, and a display field
   such as `name` or `preview`.
4. A user-reviewed private spec containing new/updated projects and explicit
   assignment rules.
5. macOS process and launchd behavior for the restart-gated apply step.

## Release-change audit

Before mutation after a Codex update:

1. Run `inventory` and confirm required state keys and value types.
2. Query `thread/list` read-only and confirm pagination fields (`data` and the
   next-cursor field) and active/archived filters.
3. Inspect one existing local assignment and one project-order entry.
4. Confirm the Desktop main-process path used by the running guard.
5. Build a synthetic plan and run the bundled unit tests.
6. If any contract differs, update the script, this reference, fixtures, and
   tests together. Do not weaken stale-state, backup, or verification gates to
   force compatibility.

## Known boundary

Programmatic checks can prove catalogue membership and Desktop metadata. They
cannot prove what the iPhone Remote client ultimately renders. Require visual
user confirmation for that final boundary.
