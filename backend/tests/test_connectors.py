import pytest

from recruiting.connectors import ConnectorRegistry, ConnectorType, UnknownConnector
from recruiting.connectors.secrets import EnvironmentSecrets, register_builtin_secrets


def test_activate_and_swap_adapters_by_name():
    registry = ConnectorRegistry()
    registry.register(ConnectorType.EMAIL, "gmail", lambda s: ("gmail", s.get("domain")))
    registry.register(ConnectorType.EMAIL, "m365", lambda s: ("m365", s.get("domain")))
    assert registry.available(ConnectorType.EMAIL) == ["gmail", "m365"]

    registry.activate(ConnectorType.EMAIL, "m365", {"domain": "mail.example.com"})
    registry.activate(ConnectorType.EMAIL, "gmail")
    assert set(registry.active(ConnectorType.EMAIL)) == {"gmail", "m365"}

    registry.deactivate(ConnectorType.EMAIL, "gmail")
    assert registry.active(ConnectorType.EMAIL) == {"m365": ("m365", "mail.example.com")}


def test_unknown_adapter_and_duplicate_registration():
    registry = ConnectorRegistry()
    with pytest.raises(UnknownConnector):
        registry.activate(ConnectorType.SECRETS, "aws_secrets_manager")
    registry.register(ConnectorType.LLM, "claude", lambda s: object())
    with pytest.raises(ValueError):
        registry.register(ConnectorType.LLM, "claude", lambda s: object())


def test_environment_secrets_provider():
    registry = ConnectorRegistry()
    register_builtin_secrets(registry)
    assert registry.available(ConnectorType.SECRETS) == ["environment"]

    secrets = EnvironmentSecrets({"RAP_SECRET_AFFINDA_API_KEY": "k-123"})
    assert secrets.get("affinda_api_key") == "k-123"
    with pytest.raises(KeyError):
        secrets.get("missing")
