from felix.verify.verifier import Verdict, contradicts_fixed, decide, extract_signals, parse_verdict


def test_parse_verdict_extracts_line():
    assert parse_verdict("SUMMARY: ok\nVERDICT: FIXED\n") == Verdict.FIXED
    assert parse_verdict("verdict: not applied") == Verdict.NOT_APPLIED
    assert parse_verdict("VERDICT: PARTIAL") == Verdict.PARTIAL


def test_parse_verdict_absent():
    assert parse_verdict("no verdict here") is None
    assert parse_verdict("") is None


def test_parse_verdict_tolerates_markdown_noise():
    # minimax on Ollama wraps the report in markdown: "VERDICT:\n\n** FIXED".
    assert parse_verdict("VERDICT:\n\n** FIXED\n────") == Verdict.FIXED
    assert parse_verdict("**VERDICT:** FIXED") == Verdict.FIXED
    assert parse_verdict("## VERDICT\n\nPARTIAL") == Verdict.PARTIAL


def _obs(text):
    return {"output": text}


def test_http_recovery_is_fixed():
    before = extract_signals([_obs("curl -> HTTP 502 Bad Gateway")])
    after = extract_signals([_obs("curl -> HTTP 200 OK")])
    result = decide(before, after, applied=True)
    assert result.verdict == Verdict.FIXED


def test_http_still_failing_is_failed():
    before = extract_signals([_obs("status: 502")])
    after = extract_signals([_obs("status: 502")])
    assert decide(before, after, applied=True).verdict == Verdict.FAILED


def test_container_health_improvement_is_fixed():
    before = extract_signals([_obs("xyz-1   Up 2 minutes (unhealthy)")])
    after = extract_signals([_obs("xyz-1   Up 10 seconds (healthy)")])
    assert decide(before, after, applied=True).verdict == Verdict.FIXED


def test_new_errors_make_it_partial():
    before = extract_signals([_obs("HTTP 502 error")])
    after = extract_signals([_obs("HTTP 200 ok"), _obs("connection refused error traceback")])
    # http improved but new errors appeared
    assert decide(before, after, applied=True).verdict == Verdict.PARTIAL


def test_container_still_restarting_is_not_fixed():
    # The activation-miss case: edit applied but the target is still crash-looping
    # (e.g., `docker compose restart` didn't recreate against the rebuilt image).
    before = extract_signals([_obs("docker-container-1   Restarting (1) 5 seconds ago")])
    after = extract_signals([_obs("docker-container-1   Restarting (1) 5 seconds ago")])
    assert decide(before, after, applied=True).verdict == Verdict.FAILED


def test_container_recovery_needs_positive_confirmation():
    # Active failure absent from the after-probe but nothing confirms the target
    # is up (agent turn cut before re-checking) — must not read as FIXED.
    before = extract_signals([_obs("docker-container-1   Restarting (1) 5 seconds ago")])
    after = extract_signals([_obs("the container is starting, will check logs")])
    assert decide(before, after, applied=True).verdict != Verdict.FIXED


def test_count_drop_alone_is_not_fixed():
    # A dormant container leaving the listing drops the global count but the target
    # is still failing — the old heuristic called this FIXED.
    before = extract_signals([_obs("app-1 Restarting (1) 2 seconds ago\nold-job Exited (0) 2 days ago")])
    after = extract_signals([_obs("app-1 Restarting (1) 2 seconds ago")])
    assert decide(before, after, applied=True).verdict == Verdict.FAILED


def test_dormant_exited_is_not_an_active_failure():
    # A stopped 'Exited' container is ambiguous and must not drive a failure verdict.
    s = extract_signals([_obs("old-job-1  Exited (0) 2 days ago  exited")])
    assert s.unhealthy_hits == 0
    assert s.dormant_hits >= 1


def test_contradicts_fixed_flags_persistent_failure():
    # Agent claims FIXED but the re-probe still shows the target restarting.
    before = extract_signals([_obs("app-1   Restarting (1) 5 seconds ago")])
    after = extract_signals([_obs("app-1   Restarting (1) 5 seconds ago")])
    assert contradicts_fixed(before, after) is not None


def test_contradicts_fixed_defers_when_inconclusive():
    # Re-probe shows the target up — no positive contradiction, defer to the agent.
    before = extract_signals([_obs("app-1   Restarting (1) 5 seconds ago")])
    after = extract_signals([_obs("app-1   Up 30 seconds (healthy)")])
    assert contradicts_fixed(before, after) is None


def test_words_in_commands_and_prose_are_not_failures():
    # The Ollama false-FAILED: the after-probe was full of the WORDS
    # restarting/unhealthy in grep patterns, --filter flags, echo labels and prose,
    # while every container was actually healthy. None of these must count.
    text = (
        'grep -E "Restarting|unhealthy|Exited"\n'
        'docker ps --filter "status=restarting" --filter "health=unhealthy"\n'
        'echo "=== checking for unhealthy/restarting containers ==="\n'
        "No unhealthy/restarting/exited containers\n"
        "docker-container-1 Up 5 minutes (healthy)\n"
        "docker-container-1 Up 12 minutes"
    )
    s = extract_signals([_obs(text)])
    assert s.unhealthy_hits == 0, s.unhealthy_hits
    assert s.healthy_hits >= 1


def test_healthy_after_with_noisy_prose_does_not_contradict_fixed():
    # before: target genuinely restarting; after: healthy, but the probe text is full
    # of the words in greps/filters/prose. Must NOT read as a persistent failure.
    before = extract_signals([_obs("docker-container-1   Restarting (1) 10 seconds ago")])
    after = extract_signals(
        [
            _obs(
                'docker ps | grep -E "Restarting|unhealthy"\n'
                "docker-container-1   Up 16 seconds (healthy)\n"
                "No unhealthy/restarting/exited containers"
            )
        ]
    )
    assert contradicts_fixed(before, after) is None
    assert decide(before, after, applied=True).verdict == Verdict.FIXED


def test_file_edit_with_no_runtime_signal_is_fixed_not_failed():
    # A scoped file/config edit (curl timeout, set pipefail, PORT change) has no
    # container/http/disk signal to probe. When the agent applies it but emits no
    # VERDICT, the blind re-probe used to read "no measurable change" -> FAILED,
    # wrongly failing a correct edit. Applied + no regression => not a failure.
    before = extract_signals([_obs("curl -s https://api.example.com/v1/status")])
    after = extract_signals([_obs("curl -s --max-time 10 https://api.example.com/v1/status")])
    assert decide(before, after, applied=True).verdict == Verdict.FIXED


def test_read_only_is_proposed():
    s = extract_signals([_obs("HTTP 502")])
    assert decide(s, s, applied=False, read_only=True).verdict == Verdict.PROPOSED


def test_dry_run_is_not_applied():
    s = extract_signals([])
    assert decide(s, s, applied=False, dry_run=True).verdict == Verdict.NOT_APPLIED


def test_disk_freed_is_fixed():
    before = extract_signals([_obs("/ 95% used")])
    after = extract_signals([_obs("/ 70% used")])
    assert decide(before, after, applied=True).verdict == Verdict.FIXED
