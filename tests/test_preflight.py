import pytest

from felix.pipeline.preflight import check

# Out-of-scope: how-to / informational / authoring with no problem to diagnose.
OUT_OF_SCOPE = [
    "how to copy files over to the Linux machine via ssh",
    "how do I set up an ssh key",
    "what's the best way to free disk space",
    "what is docker",
    "explain how systemd units work",
    "steps to install nginx",
    "write me a bash script to back up /etc",
    "give me an example rsync command",
]

# In scope: a target/symptom to diagnose, fix, audit, or verify. The diagnostic
# signal overrides informational phrasing ("explain why X crashes", "how do I
# fix the 502").
IN_SCOPE = [
    "ssh into Linux machine and check its disk usage and memory pressure",
    "diagnose why docker-container-1 crashes with ModuleNotFoundError",
    "explain why docker-container-1 keeps crashing",
    "how do I fix the 502 on the api container",
    "audit infra-mysql-1: TLS protocols and user privileges",
    "nginx returns 502, fix it",
    "the api is unreachable, investigate",
]


@pytest.mark.parametrize("prompt", OUT_OF_SCOPE)
def test_informational_prompts_are_out_of_scope(prompt):
    pf = check(prompt)
    assert not pf.ok
    assert pf.kind == "out_of_scope"


@pytest.mark.parametrize("prompt", IN_SCOPE)
def test_diagnostic_prompts_pass(prompt):
    assert check(prompt).ok


def test_diagnostic_signal_overrides_informational_phrasing():
    # "explain" alone is out of scope; "explain why X crashes" is a diagnosis.
    assert not check("explain how scp works").ok
    assert check("explain why nginx keeps crashing").ok


def test_empty_prompt_is_incomplete_not_out_of_scope():
    pf = check("")
    assert not pf.ok
    assert pf.kind == "incomplete"


def test_vague_only_prompt_is_incomplete():
    pf = check("make it work")
    assert not pf.ok
    assert pf.kind == "incomplete"


def test_dangling_reference_without_target_is_incomplete():
    pf = check("fix the issue in the screenshot")
    assert not pf.ok
    assert pf.kind == "incomplete"


def test_named_identifier_resolves_dangling_reference():
    # "the image" is resolvable when a concrete container is named.
    assert check("diagnose docker-container-1 and fix the image dependencies").ok
