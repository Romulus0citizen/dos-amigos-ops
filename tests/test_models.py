from sqlalchemy.dialects.postgresql import JSONB, UUID

from apps.core.app.models import IntegrationCapability
from apps.core.app.models.base import Base


def test_required_integration_tables_are_registered() -> None:
    assert {
        "integration_connections",
        "integration_capabilities",
        "sync_runs",
        "raw_payloads",
    }.issubset(Base.metadata.tables)


def test_capability_table_name() -> None:
    assert IntegrationCapability.__tablename__ == "integration_capabilities"


def test_persistence_contract_columns() -> None:
    assert set(Base.metadata.tables["integration_connections"].columns.keys()) == {
        "id",
        "provider",
        "mode",
        "base_url",
        "organization_ref",
        "status",
        "last_verified_at",
        "created_at",
        "updated_at",
    }

    assert set(Base.metadata.tables["sync_runs"].columns.keys()) == {
        "id",
        "provider",
        "dataset",
        "status",
        "started_at",
        "finished_at",
        "records_received",
        "payloads_saved",
        "error_code",
        "error_message_sanitized",
        "trace_id",
        "request_fingerprint",
        "created_at",
    }

    assert set(Base.metadata.tables["raw_payloads"].columns.keys()) == {
        "id",
        "provider",
        "dataset",
        "external_reference",
        "fetched_at",
        "source_updated_at",
        "http_status",
        "content_type",
        "payload_json",
        "payload_sha256",
        "schema_hint",
        "sync_run_id",
        "created_at",
    }


def test_postgresql_storage_types() -> None:
    connection_id_type = Base.metadata.tables["integration_connections"].c.id.type
    payload_type = Base.metadata.tables["raw_payloads"].c.payload_json.type

    assert isinstance(connection_id_type, UUID)
    assert isinstance(payload_type, JSONB)


def test_raw_payload_idempotency_indexes() -> None:
    indexes = {
        index.name: index
        for index in Base.metadata.tables["raw_payloads"].indexes
    }

    assert indexes["uq_raw_payload_with_external_reference"].unique
    assert indexes["uq_raw_payload_without_external_reference"].unique
