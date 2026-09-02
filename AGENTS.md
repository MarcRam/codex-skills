# Codex Skills Repository

This repository contains public, reusable Codex skills. Keep every skill safe to
publish: never commit account identifiers, thread IDs, private transcripts,
machine-specific paths, generated reconciliation plans, receipts, backups, or
screenshots of private sidebars.

## Purpose

`reconcile-codex-sidebars` audits the thread catalogue shared by Codex Desktop
and Remote, reconciles Desktop's local project assignments, then migrates them
into server-owned projects and `Thread.projectId` membership. Custom thread
sections are a separate presentation contract and are not a substitute for
Remote-visible project membership.

## Maintenance

- Treat installed Codex behavior and official App Server documentation as the
  current authority; the bundled references describe the last verified shape,
  not a permanent API guarantee.
- Run contract inspection and a read-only plan before changing live metadata.
- Update the adapters and references together when Codex state keys, App Server
  methods, process names, or Remote filtering change.
- Preserve the dry-run, stale-state refusal, backup, atomic-write, rollback,
  server-section verification, and post-restart verification gates.
- Test only with synthetic fixtures in this repository. Run live tests from an
  ignored private directory.
- Require explicit user approval before archiving threads, applying a plan,
  quitting/restarting Codex, restoring a backup, or publishing repository
  changes.

## Validation

Run the skill validator and the bundled unit tests before committing. A valid
skill is not proof of compatibility with a new Codex release; perform the
read-only contract audit described in `references/contracts.md` as well.
