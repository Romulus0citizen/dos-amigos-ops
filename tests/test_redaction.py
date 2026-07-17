from integrations.iiko.redaction import redact


def test_redaction_hides_secrets_but_preserves_business_ids() -> None:
    source = {
        "username": "api_dos_amigos",
        "password": "do-not-store",
        "apiLogin": "do-not-store",
        "licenseKey": "do-not-store",
        "organization_id": "8340002",
        "nested": {
            "Authorization": "Bearer secret",
            "Set-Cookie": "session=secret",
            "trace_id": "trace-1",
        },
    }

    result = redact(source)

    assert result["username"] == "api_dos_amigos"
    assert result["password"] == "***REDACTED***"
    assert result["apiLogin"] == "***REDACTED***"
    assert result["licenseKey"] == "***REDACTED***"
    assert result["organization_id"] == "8340002"
    assert result["nested"]["Authorization"] == "***REDACTED***"
    assert result["nested"]["Set-Cookie"] == "***REDACTED***"
