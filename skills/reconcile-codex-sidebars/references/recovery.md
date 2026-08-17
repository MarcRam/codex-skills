# Recovery

## Apply failure

The one-shot fails closed when Desktop remains running, the source-state hash
changes, the plan payload is corrupt, or post-write sidebar verification fails.
Read the private receipt and log before retrying. Rebuild stale plans rather
than overriding the hash check.

## Restore a backup

1. Quit Codex Desktop.
2. Confirm the backup path printed in the receipt is inside
   `~/.codex/backups/sidebar-reconcile/`.
3. Preserve the failed current state for diagnosis.
4. Restore the exact backup with owner-only permissions and an atomic replace.
5. Relaunch Codex and confirm the original sidebar returned.

Do not restore while Desktop is running. Do not select a backup using a broad
glob or delete other backups during recovery.

## Cleanup

After successful Desktop and Remote verification, run
`check-macos-restart --purge-private-runtime`. It removes only the exact
per-run staging directory recorded by the manifest. The durable backup remains
available for rollback.
