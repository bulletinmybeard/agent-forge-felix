from felix.safety.gate import Decision, ModeFlags, evaluate
from felix.safety.tiers import RiskTier, classify


def test_read_only_tool_is_read_only():
    assert classify(tool_name="docker_ps") == RiskTier.READ_ONLY


def test_destructive_guard_is_high():
    assert classify(tool_name="shell", command="rm -rf /var", guard_threat="destructive") == RiskTier.HIGH


def test_rm_rf_root_pattern_is_high_without_guard():
    assert classify(tool_name="shell", command="rm -rf /etc/nginx") == RiskTier.HIGH


def test_docker_restart_is_low():
    assert classify(tool_name="shell", command="docker restart xyz-1") == RiskTier.LOW


def test_sudo_threat_is_medium():
    assert classify(tool_name="shell", command="systemctl restart x", guard_threat="sudo") == RiskTier.LOW


def test_confirm_prompt_implies_at_least_medium():
    assert classify(tool_name="code_edit", confirm_prompt="Overwrite config?") == RiskTier.MEDIUM


def test_unknown_mutating_tool_defaults_medium():
    assert classify(tool_name="write_file") == RiskTier.MEDIUM


# -- gate decisions ------------------------------------------------------


def test_read_only_flag_denies_mutations():
    g = evaluate(RiskTier.MEDIUM, ModeFlags(read_only=True))
    assert g.decision == Decision.DENY


def test_read_only_flag_allows_reads():
    g = evaluate(RiskTier.READ_ONLY, ModeFlags(read_only=True))
    assert g.decision == Decision.APPROVE


def test_low_risk_auto_applies_with_apply():
    assert evaluate(RiskTier.LOW, ModeFlags(apply=True)).decision == Decision.APPROVE


def test_low_risk_prompts_without_apply():
    assert evaluate(RiskTier.LOW, ModeFlags()).decision == Decision.PROMPT


def test_medium_prompts_by_default():
    assert evaluate(RiskTier.MEDIUM, ModeFlags()).decision == Decision.PROMPT


def test_medium_auto_confirms_with_yes_and_sets_auto_accept():
    g = evaluate(RiskTier.MEDIUM, ModeFlags(yes=True))
    assert g.decision == Decision.APPROVE
    assert g.auto_accept is True


def test_high_blocked_by_default():
    assert evaluate(RiskTier.HIGH, ModeFlags()).decision == Decision.DENY


def test_high_prompts_even_with_yes_never_auto():
    g = evaluate(RiskTier.HIGH, ModeFlags(yes=True, apply=True))
    assert g.decision == Decision.PROMPT
    assert g.auto_accept is False


# -- egress-aware gating of outbound network diagnostics -----------------


def test_http_check_localhost_is_read_only():
    assert classify(tool_name="http_check", args={"url": "http://localhost:3000/health"}) == RiskTier.READ_ONLY


def test_net_probe_private_ip_is_read_only():
    assert classify(tool_name="net_probe", args={"host": "10.0.0.5", "port": 8080}) == RiskTier.READ_ONLY


def test_http_check_internal_service_name_is_read_only():
    assert classify(tool_name="http_check", args={"url": "http://localhost:8080/"}) == RiskTier.READ_ONLY


def test_http_check_external_host_is_gated():
    assert classify(tool_name="http_check", args={"url": "https://evil.tld/?d=secret"}) == RiskTier.MEDIUM


def test_dns_lookup_external_domain_is_gated():
    assert classify(tool_name="dns_lookup", args={"name": "exfil.evil.tld"}) == RiskTier.MEDIUM


def test_external_host_on_allowlist_is_read_only():
    tier = classify(
        tool_name="http_check",
        args={"url": "https://api.mycorp.com/health"},
        egress_allow_hosts=["api.mycorp.com"],
    )
    assert tier == RiskTier.READ_ONLY


def test_network_tool_without_resolvable_host_stays_read_only():
    # No destination in args -> can't judge -> keep current read-only behavior.
    assert classify(tool_name="dns_lookup", args={}) == RiskTier.READ_ONLY
    assert classify(tool_name="http_check") == RiskTier.READ_ONLY


def test_destructive_still_wins_over_network_destination():
    tier = classify(tool_name="http_check", args={"url": "http://localhost/"}, guard_threat="destructive")
    assert tier == RiskTier.HIGH
