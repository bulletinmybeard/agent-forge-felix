from felix.pipeline.orchestrator import _enforcement_nudge, _is_activation_command


def test_activation_commands_detected():
    for cmd in [
        "cd /opt/docker-container-1 && bash scripts/deploy-remote.sh --web-only",
        "docker compose -f docker-compose.yml up -d mcp",
        "docker compose build web",
        "docker compose -f x.yml restart web",
        "docker build -t docker-container-1:latest .",
        "systemctl restart nginx",
        "systemctl reload nginx",
        "scp server.py host:/opt/app/",
        "rsync -a ./ host:/opt/app/",
        "kubectl apply -f deploy.yaml",
        "kubectl rollout restart deploy/api",
        "terraform apply -auto-approve",
        "make deploy",
    ]:
        assert _is_activation_command(cmd), f"missed activation: {cmd}"


def test_read_commands_not_activation():
    for cmd in [
        "docker ps -a",
        "docker logs docker-container-1 --tail 100",
        "docker inspect docker-container-1",
        "cat /opt/docker-container-1/server.py",
        "ls -la /opt/docker-container-1",
        "grep -n mcp.app server.py",
        "df -h",
        "du -sh ~/Library/Caches/* | sort -hr | head -20",
        # Path substrings that used to false-positive on bare `service\\b`
        # (Chrome Service Worker cache) and flip applied_any → false Fixed.
        # Synthetic paths only — never use a real username/home path in fixtures.
        "du -sh /Users/example/Library/Application\\ Support/Google/Chrome/Default/Service\\ Worker/CacheStorage/abc 2>/dev/null",
        "du -sh '/Users/example/Library/Application Support/Google/Chrome/Default/Service Worker/CacheStorage/x'",
        "strings /Users/example/Library/Application Support/Google/Chrome/Default/Service Worker/foo | head -20",
        "docker system df -v | head -50",
        "",
    ]:
        assert not _is_activation_command(cmd), f"false activation: {cmd}"


def test_enforcement_nudge():
    # Healthy host (nothing was broken) > never enforce, regardless of applied state.
    assert _enforcement_nudge(False, False, False) is None
    assert _enforcement_nudge(False, True, True) is None
    # Broken + nothing applied (diagnosed and stopped) > re-prompt to apply.
    assert _enforcement_nudge(True, False, False) == "apply"
    # Broken + applied but not activated > re-prompt to activate.
    assert _enforcement_nudge(True, True, True) == "activate"
    # Broken + applied AND activated > let verification judge (no nudge).
    assert _enforcement_nudge(True, True, False) is None
