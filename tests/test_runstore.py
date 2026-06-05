from felix.pipeline.preflight import check
from felix.runstore.ledger import ChangeRecord, Ledger
from felix.runstore.reader import RunView, load_run
from felix.runstore.redact import redact
from felix.runstore.store import RunStore
from felix.runstore.undo import plan_undo


def test_redact_token():
    out = redact("export API_KEY=sk-abcdefghij1234567890ABCD and password=hunter2")
    assert "sk-abcdefghij1234567890ABCD" not in out
    assert "hunter2" not in out
    assert "REDACTED" in out


def test_runstore_writes_and_reads(tmp_path):
    store = RunStore(tmp_path, run_id="20260601-120000").create()
    store.write_prompt("why is the Docker container xyz-1 down?")
    store.append_command({"name": "shell", "command": "docker ps", "tier": "read_only"})
    store.append_observation({"tool": "docker_ps", "output": "xyz-1 unhealthy", "phase": "before"})
    store.write_report("# Felix Report\nok")

    view = load_run(tmp_path)
    assert view is not None
    assert view.run_id == "20260601-120000"
    assert "xyz-1" in (view.prompt or "")
    assert len(view.commands) == 1
    assert view.observations[0]["phase"] == "before"
    assert "Felix Report" in (view.report or "")


def test_ledger_file_changes_and_stamp(tmp_path):
    store = RunStore(tmp_path, run_id="r1").create()
    led = Ledger(store.path)
    led.append(ChangeRecord(kind="file", reason="edit", file_path="/etc/x", pre_hash="hash123", action="edited"))
    led.append(ChangeRecord(kind="command", reason="restart", command="docker restart x", tier="low"))

    changes = led.file_changes()
    assert len(changes) == 1
    assert changes[0]["pre_hash"] == "hash123"

    led.stamp_verification("Fixed")
    assert all(r.get("verification_result") == "Fixed" for r in led.read())


def test_plan_undo_dedups_repeated_file_changes(tmp_path):
    store = RunStore(tmp_path, run_id="r2").create()
    led = Ledger(store.path)
    # diff-preview emits a file.diff for both propose + apply: same path + pre_hash.
    for _ in range(2):
        led.append(ChangeRecord(kind="file", reason="edit", file_path="/tmp/x.yml", pre_hash="abc", action="edited"))
    files, manual = plan_undo(RunView(store.path))
    assert len(files) == 1
    assert files[0]["pre_hash"] == "abc"


def test_preflight_dangling_reference():
    assert check("check this log").ok is False
    assert check("fix the issue in the screenshot").ok is False


def test_preflight_proceeds_with_target():
    assert check("docker container xyz-1 website is not responding").ok is True
    assert check("my VM is running out of disk space").ok is True
    assert check("why does localhost:3000 not respond?").ok is True


def test_preflight_permissive_for_any_system_domain():
    # Felix targets any system problem — not just docker/disk/http.
    assert check("why is pod X crashlooping?").ok is True
    assert check("nginx returns 500 after the cert renewal").ok is True
    assert check("systemd service keeps restarting").ok is True


def test_preflight_blocks_only_vague_filler():
    assert check("make it work").ok is False
    assert check("fix it").ok is False
    assert check("help").ok is False
