"""Felix client configuration.

Loaded from ~/.felix/config.yaml (override path via FELIX_CONFIG).
Every field has a sane default and an env override so Felix runs
out of the box against a local or remote agent-forge.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yaml

DEFAULT_API_BASE = "http://localhost:8100"
DEFAULT_SEARCH_BASE = "http://localhost:8200"
CONFIG_ENV = "FELIX_CONFIG"
HOME_DIR_ENV = "FELIX_HOME"


def felix_home() -> Path:
    """Root for config + run-store. ~/.felix unless FELIX_HOME overrides."""
    return Path(os.environ.get(HOME_DIR_ENV, str(Path.home() / ".felix")))


def _config_path() -> Path:
    if env := os.environ.get(CONFIG_ENV):
        return Path(env)
    return felix_home() / "config.yaml"


def _derive_ws_url(api_base: str) -> str:
    """http(s)://host -> ws(s)://host/ws/chat."""
    parts = urlsplit(api_base)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urlunsplit((scheme, parts.netloc, "/ws/chat", "", ""))


@dataclass
class Config:
    api_base: str = DEFAULT_API_BASE
    ws_url: str = ""
    api_key: str | None = None
    # Custom agent alias the Felix run targets AgentForge-side.
    default_agent: str = "@felix"
    # Read-only probe agent used for before/after verification snapshots.
    probe_agent: str = "@felix"
    # Stamped on sessions so Felix runs stay out of the human chat sidebar.
    source: str = "felix"
    # Backend provider for this run (ollama | bedrock | deepinfra | openrouter).
    # Sent as overrides.provider; agent-forge remaps every capability tier to
    # that provider's concrete models (provider_override_map). None = server default.
    provider: str | None = None
    # Per-iteration safety net; the server still enforces its own cap.
    request_timeout: float = 600.0
    # Skill fleet (Part C): indexer/search service + retrieval knobs.
    search_base: str = DEFAULT_SEARCH_BASE
    skills_source_name: str = "felix-skills"
    skills_top_k: int = 5
    skills_score_threshold: float = 0.5
    # Dynamic discovery against the skills.sh registry (same data as `npx skills`).
    skills_search_api: str = "https://skills.sh/api/search"
    skills_discover_limit: int = 3
    # Auto-fetch+index skills from skills.sh when local retrieval finds none.
    skills_auto_discover: bool = True
    # Where chunk JSON is written for the indexer to read. Must be (or be synced
    # to) the indexer host's `chunks_dir`. Empty -> ~/.felix/chunks.
    skills_chunks_dir: str = ""
    # Skill firewall: vet acquired skills before they reach the catalog.
    # quarantine = hold failing skills for review (default); warn = log but keep;
    # off = no vetting. The LLM judge runs only if vetting_provider is set —
    # otherwise vetting is static-scan-only (still the deterministic floor).
    skills_vetting: str = "quarantine"
    vetting_provider: str | None = None  # openrouter | deepinfra | ollama | ...
    vetting_model: str = ""
    vetting_base_url: str = ""  # provider chat-completions endpoint
    vetting_allow_hosts: list[str] = field(default_factory=list)  # don't trip exfil rule
    vetting_api_key: str | None = None  # set via env FELIX_VETTING_API_KEY
    # Hosts the outbound diagnostic tools (http_check/dns_lookup/net_probe) may
    # reach without a confirm. Local/private/internal targets are always allowed;
    # arbitrary external hosts are gated (exfil channel). Add your public targets.
    egress_allow_hosts: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.ws_url:
            self.ws_url = _derive_ws_url(self.api_base)

    @property
    def runs_dir(self) -> Path:
        return felix_home() / "runs"

    @property
    def catalog_dir(self) -> Path:
        return felix_home() / "skills-catalog"

    @property
    def quarantine_dir(self) -> Path:
        return felix_home() / "quarantine"

    @property
    def chunks_dir(self) -> Path:
        return Path(self.skills_chunks_dir) if self.skills_chunks_dir else felix_home() / "chunks"

    def auth_headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}", "X-API-Key": self.api_key}


def load_config() -> Config:
    """Read config file, then apply env overrides (env wins)."""
    data: dict = {}
    path = _config_path()
    if path.is_file():
        loaded = yaml.safe_load(path.read_text()) or {}
        if isinstance(loaded, dict):
            data = loaded

    known = {f for f in Config.__dataclass_fields__ if f != "extra"}
    kwargs = {k: v for k, v in data.items() if k in known}
    kwargs["extra"] = {k: v for k, v in data.items() if k not in known}

    if env := os.environ.get("FELIX_API_BASE"):
        kwargs["api_base"] = env
        kwargs.pop("ws_url", None)  # re-derive from the new base
    if env := os.environ.get("FELIX_WS_URL"):
        kwargs["ws_url"] = env
    if env := os.environ.get("FELIX_API_KEY"):
        kwargs["api_key"] = env
    if env := os.environ.get("FELIX_SEARCH_BASE"):
        kwargs["search_base"] = env
    if env := os.environ.get("FELIX_PROVIDER"):
        kwargs["provider"] = env
    if env := os.environ.get("FELIX_VETTING_API_KEY"):
        kwargs["vetting_api_key"] = env

    return Config(**kwargs)
