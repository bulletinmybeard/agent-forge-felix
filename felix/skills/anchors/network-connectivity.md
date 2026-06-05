---
name: Network Connectivity Triage
description: Diagnose connectivity failures — DNS resolution, port reachability, listening processes, and container-to-container networking.
---

# Network connectivity triage

## Probe order

1. `dns_lookup <host>` — does the name resolve, and to the expected address?
2. `net_probe <host> <ports>` — is the port reachable? is a local process bound to it?
3. `process_list` / `lsof -i :<port>` — what is (or isn't) listening.
4. For containers: `docker_networks` + `docker_inspect` — are the containers on the same network? is the target addressed by service name, not localhost?

## Distinguish the failure mode

- Resolves but refused: nothing listening, or bound to 127.0.0.1 instead of 0.0.0.0.
- Does not resolve: DNS/record/hosts-file problem.
- Reachable locally but not across containers: wrong network or using `localhost` instead of the service name.
- Intermittent: upstream flapping — correlate with `analyze_logs`.

## Fix + verify

Apply the narrowest fix (bind address, network attach, DNS record, firewall rule — high-risk, confirm), then re-run `net_probe`/`dns_lookup` and confirm reachability.
