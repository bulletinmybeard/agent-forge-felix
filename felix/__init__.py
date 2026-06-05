"""Felix — autonomous local diagnostic-repair agent.

diagnose. fix. verify.

Felix is a thin client over AgentForge's web API. The remote AgentForge service is the brain executing tools are dispatched to a local SAQ worker.
Felix drives a run over the WebSocket, renders the live event stream, enforces a risk-tiered confirmation policy, verifies before/after,
and persists a local run-store + rollback ledger + report.
"""

__version__ = "0.1.0"
