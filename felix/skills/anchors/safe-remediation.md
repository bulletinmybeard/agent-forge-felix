---
name: Safe Remediation and Cleanup
description: Cross-cutting safety discipline for any repair — read-only first, reversible changes, risk tiers, and mandatory before/after verification.
---

# Safe cleanup and remediation

Diagnose before you touch anything. Read-only probes first, always.

## Risk tiers

- Read-only (run freely): ps, logs, inspect, df, du, status, http checks.
- Low risk (announce, then do): restart an unhealthy container, prune dangling images, clear temp files, reload a service.
- Medium risk (confirm first): edit a config file, recreate a container, prune build cache, remove old logs, change an env var.
- High risk (refuse by default): rm -rf on broad paths, delete volumes or backups, database mutations, firewall/credential/package changes.

## Reversibility

Prefer changes you can undo. Use `code_edit` for config changes — it shows a diff and records a `snapshot_id` you can pass to `revert_file`. Track every change so you can list exact rollback steps in the report.

## Verify

After any change, re-run the read-only probes you started with and compare. Never claim "fixed" without before/after evidence.
