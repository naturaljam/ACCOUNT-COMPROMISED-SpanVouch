from spanvouch.evaluation import evaluate_phase5_matrix, run_phase5_matrix


def test_run_cli_has_no_label_argument_and_supports_live_formal_flags() -> None:
    parser = run_phase5_matrix.build_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert {"--config", "--corpus-dir", "--candidate-dir", "--output-dir"} <= option_strings
    assert {"--allow-live-provider", "--formal-run"} <= option_strings
    assert all("label" not in option for option in option_strings)


def test_evaluate_cli_has_only_offline_join_arguments() -> None:
    parser = evaluate_phase5_matrix.build_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert {"--provider-results", "--sealed-labels", "--output-dir"} <= option_strings
    for forbidden in ("endpoint", "api-key", "allow-live", "formal-run"):
        assert all(forbidden not in option for option in option_strings)


def test_cli_modules_accept_injected_offline_commands() -> None:
    called: list[object] = []
    assert run_phase5_matrix.main(
        ["--config", "config.json", "--corpus-dir", "corpus",
         "--candidate-dir", "candidates", "--output-dir", "out"],
        command=lambda request: called.append(request),
    ) == 0
    assert evaluate_phase5_matrix.main(
        ["--provider-results", "provider", "--sealed-labels", "labels",
         "--output-dir", "evaluated"],
        command=lambda request: called.append(request),
    ) == 0
    assert len(called) == 2

