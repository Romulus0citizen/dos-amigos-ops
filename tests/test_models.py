from sqlalchemy.dialects.postgresql import JSONB, UUID

from apps.core.app.models import IikoSalesDaily, IntegrationCapability
from apps.core.app.models.base import Base
from apps.core.app.models.sales import HermesReportRecipientDelivery


def test_required_integration_tables_are_registered() -> None:
    assert {
        "integration_connections",
        "integration_capabilities",
        "sync_runs",
        "raw_payloads",
        "iiko_sales_sync_runs",
        "iiko_sales_daily",
        "iiko_sales_daily_payments",
        "iiko_sales_daily_products",
        "daily_coo_report_runs",
        "hermes_report_recipient_deliveries",
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
    indexes = {index.name: index for index in Base.metadata.tables["raw_payloads"].indexes}

    assert indexes["uq_raw_payload_with_external_reference"].unique
    assert indexes["uq_raw_payload_without_external_reference"].unique


def test_sales_daily_columns_and_uniqueness() -> None:
    table = Base.metadata.tables["iiko_sales_daily"]

    assert IikoSalesDaily.__tablename__ == "iiko_sales_daily"
    assert {
        "organization_id",
        "business_date",
        "gross_sales",
        "reported_discounts",
        "reported_increases",
        "net_sales",
        "unexplained_adjustment",
        "refunds",
        "checks_count",
        "average_check",
        "result_status",
        "reconciliation_error_code",
        "source_checksum",
    }.issubset(table.columns.keys())
    assert any(
        constraint.name == "uq_iiko_sales_daily_org_date" for constraint in table.constraints
    )


def test_sales_payment_and_product_deterministic_keys() -> None:
    payment_constraints = Base.metadata.tables["iiko_sales_daily_payments"].constraints
    product_constraints = Base.metadata.tables["iiko_sales_daily_products"].constraints

    assert any(
        constraint.name == "uq_iiko_sales_daily_payment_key" for constraint in payment_constraints
    )
    assert any(
        constraint.name == "uq_iiko_sales_daily_product_key" for constraint in product_constraints
    )


def test_report_recipient_delivery_has_cascade_foreign_key() -> None:
    foreign_keys = list(HermesReportRecipientDelivery.__table__.foreign_keys)

    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "hermes_report_outbox.id"
    assert foreign_keys[0].ondelete == "CASCADE"
