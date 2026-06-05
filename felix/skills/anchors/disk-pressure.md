---
name: Disk & System Pressure Triage
description: Find and safely reclaim disk space, and triage CPU/memory/process pressure on Linux/macOS without deleting data.
---

# Disk & system diagnostics

## Probe order

1. `disk_usage` — which filesystem is full and by how much.
2. `docker_df` — Docker's share (images, containers, volumes, build cache).
3. Locate the weight: large directories, old logs, package/build caches, temp files. Use `find_files` / shell `du` on the offending mount.
4. For load: `system_overview`, `memory_info`, `process_list` to spot a runaway process or memory pressure.

## Safe reclaim (in order of safety)

- Clear temp files and rotate/remove old logs.
- Prune dangling Docker images and stopped containers (`docker_cleanup_preview` first to see what would go).
- Clear build caches.

## Never without confirmation

- Deleting data directories, named volumes, or backups.
- `rm -rf` on broad or system paths.
- Pruning Docker with `--volumes`.

## Verify

Re-run `disk_usage` and confirm free space increased past the threshold; report exactly what was removed.
