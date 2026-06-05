from felix.report import ReportInput, build_report, clean_agent_text


def test_clean_agent_text_normalizes_separators():
    out = clean_agent_text("SUMMARY: ok|||ROOT CAUSE: x")
    assert "**SUMMARY:**" in out and "ok" in out
    assert "**ROOT CAUSE:**" in out and "x" in out
    assert "|||" not in out


def test_clean_agent_text_dedupes_repeated_report():
    raw = ("SUMMARY: all good\nVERDICT: FIXED\n" * 6).strip()
    out = clean_agent_text(raw)
    assert out.count("SUMMARY:") == 1
    assert out.count("VERDICT:") == 1


def test_clean_agent_text_cuts_after_end_marker():
    raw = "SUMMARY: ok|||FILES CHANGED: none|||END OF REPORT.|||Felix out|||Felix out"
    out = clean_agent_text(raw)
    assert "Felix out" not in out
    assert "**FILES CHANGED:**" in out and out.rstrip().endswith("none")


def test_clean_agent_text_collapses_runaway_repetition():
    raw = "\n".join(["Awaiting your response."] * 50)
    assert clean_agent_text(raw) == "Awaiting your response."


def test_build_report_uses_cleaned_text():
    md = build_report(
        ReportInput(
            prompt="check docker",
            agent_text="SUMMARY: fine|||END OF REPORT.|||Felix out|||Felix out",
            verification=None,
            commands=[],
            file_changes=[],
            risk_notes=[],
            rollback_hints=[],
        )
    )
    assert "**SUMMARY:**" in md and "fine" in md
    assert "Felix out" not in md
