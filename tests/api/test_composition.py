from pathlib import Path

import pytest

from spanvouch.api import composition
from spanvouch.api.app import _ensure_database_parent, build_default_diagnosis_service
from spanvouch.contracts.diagnosis import DiagnoserKind
from spanvouch.diagnosis.engine import DiagnosisEngine
from spanvouch.diagnosis.errors import ProviderConfigurationError


def test_default_runtime_stays_offline_without_provider_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_configuration() -> object:
        raise ProviderConfigurationError("missing configuration")

    monkeypatch.setattr(composition.DeepSeekConfig, "from_env", missing_configuration)

    diagnosers, _, semantic_verifier = composition.default_runtime()

    assert tuple(diagnosers) == (DiagnoserKind.RULES.value,)
    assert semantic_verifier is None


def test_default_runtime_composes_enabled_provider_without_calling_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = object()
    monkeypatch.setattr(composition.DeepSeekConfig, "from_env", lambda: object())
    monkeypatch.setattr(composition, "DeepSeekProvider", lambda _: provider)

    diagnosers, _, semantic_verifier = composition.default_runtime()

    assert set(diagnosers) == {DiagnoserKind.RULES.value, DiagnoserKind.DEEPSEEK.value}
    assert semantic_verifier is not None


def test_database_parent_is_created_for_default_api_composition(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "spanvouch.db"

    _ensure_database_parent(database)

    assert database.parent.is_dir()


def test_default_api_diagnosis_service_uses_outer_composition() -> None:
    assert isinstance(build_default_diagnosis_service(), DiagnosisEngine)
