from felix.ui.secret import ask_secret


def test_ask_secret_returns_value(monkeypatch):
    assert ask_secret("pw?", getpass_fn=lambda p: "hunter2", isatty=lambda: True) == "hunter2"


def test_ask_secret_non_tty_returns_none():
    assert ask_secret("pw?", getpass_fn=lambda p: "x", isatty=lambda: False) is None


def test_ask_secret_empty_is_cancel():
    assert ask_secret("pw?", getpass_fn=lambda p: "", isatty=lambda: True) is None
