from apps.core.app.core.config import Settings
from integrations.iiko import AuthKind, ServerRestIikoClient


def test_settings_builds_server_rest_iiko_client_from_iiko_configuration() -> None:
    settings = Settings(
        iiko_mode="server_rest_api",
        iiko_auth_type="user_password",
        iiko_base_url="https://example.iiko.it/resto",
        iiko_username="api_dos_amigos",
        iiko_password="do-not-print",
        iiko_organization_id="department-1",
        iiko_verify_tls=True,
        iiko_connect_timeout_seconds=2,
        iiko_read_timeout_seconds=3,
        iiko_max_retries=0,
    )

    client = settings.build_iiko_client()

    assert isinstance(client, ServerRestIikoClient)
    assert client.organization_ref == "department-1"
    assert client.auth_configuration.kind is AuthKind.USER_PASSWORD
    assert client.verify_tls is True
