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
