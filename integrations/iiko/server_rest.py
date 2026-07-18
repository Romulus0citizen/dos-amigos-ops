from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4
from xml.etree import ElementTree

import httpx

from integrations.iiko.auth import AuthConfiguration
from integrations.iiko.client import IikoClient
from integrations.iiko.sales import calculate_sales_checksum
from integrations.iiko.schemas import (
    AuthKind,
    AuthResult,
    IikoMode,
    ProbeResult,
    RawResult,
    ResultStatus,
)

UNSUPPORTED_DATASET_REASON = "dataset_not_implemented_for_server_rest_api"
RETRYABLE_READ_STATUSES = {429, 502, 503, 504}

DAILY_GROUP_FIELDS = [
    "OpenDate.Typed",
    "Department.Id",
    "Storned",
    "OrderDeleted",
]
DAILY_AGGREGATE_FIELDS = [
    "DishSumInt",
    "DishDiscountSumInt",
    "DiscountSum",
    "IncreaseSum",
    "DishReturnSum",
    "UniqOrderId.OrdersCount",
]
PAYMENT_GROUP_FIELDS = [
    "OpenDate.Typed",
    "Department.Id",
    "PayTypes.Group",
    "PayTypes.GUID",
    "PayTypes",
    "Storned",
    "OrderDeleted",
]
PRODUCT_GROUP_FIELDS = [
    "OpenDate.Typed",
    "Department.Id",
    "DishId",
    "DishName",
    "DishSize.Id",
    "Storned",
    "OrderDeleted",
]
PRODUCT_AGGREGATE_FIELDS = [
    "DishAmountInt",
    "DishSumInt",
    "DishDiscountSumInt",
    "DishReturnSum",
]


class EndpointUnreachableError(Exception):
    """Internal sanitized network failure marker."""


class ServerRestIikoClient(IikoClient):
    adapter_name = "server_rest_api"
    mode = IikoMode.SERVER_REST_API

    def __init__(
        self,
        *,
        base_url: str,
        organization_ref: str | None,
        auth_configuration: AuthConfiguration,
        verify_tls: bool = True,
        connect_timeout_seconds: int = 10,
        read_timeout_seconds: int = 30,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = self._normalize_base_url(base_url)
        self.organization_ref = organization_ref or None
        self.auth_configuration = auth_configuration
        self.verify_tls = verify_tls
        self.connect_timeout_seconds = self._positive_timeout(connect_timeout_seconds, "connect")
        self.read_timeout_seconds = self._positive_timeout(read_timeout_seconds, "read")
        self.max_retries = max(0, max_retries)
        self._token: str | None = None
        self._client = httpx.AsyncClient(
            base_url=f"{self.base_url}/",
            timeout=httpx.Timeout(
                connect=self.connect_timeout_seconds,
                read=self.read_timeout_seconds,
                write=self.read_timeout_seconds,
                pool=self.connect_timeout_seconds,
            ),
            verify=self.verify_tls,
            transport=transport,
        )

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        normalized = base_url.strip().rstrip("/")
        if not normalized:
            raise ValueError("iiko server_rest_api base_url must not be empty")

        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("iiko server_rest_api base_url must be an absolute http(s) URL")
        if parsed.query or parsed.fragment:
            raise ValueError("iiko server_rest_api base_url must not include query or fragment")
        return normalized

    @staticmethod
    def _positive_timeout(value: int, label: str) -> int:
        if value <= 0:
            raise ValueError(f"iiko {label} timeout must be positive")
        return value

    def _trace_id(self) -> str:
        return str(uuid4())

    def _safe_details(self) -> dict[str, object]:
        return {
            "endpoint_configured": True,
            "writes_enabled": False,
            "verify_tls": self.verify_tls,
            **self.auth_configuration.sanitized_metadata(),
        }

    async def _get(
        self,
        path: str,
        *,
        params: Mapping[str, str],
        retries: int = 0,
    ) -> httpx.Response:
        for attempt in range(max(0, retries) + 1):
            try:
                return await self._client.get(path, params=params)
            except httpx.RequestError as exc:
                if attempt >= retries:
                    raise EndpointUnreachableError from exc
        raise EndpointUnreachableError

    async def _post_json(
        self,
        path: str,
        *,
        params: Mapping[str, str],
        json_payload: Mapping[str, Any],
        retries: int = 0,
        retry_statuses: set[int] | None = None,
    ) -> httpx.Response:
        retry_statuses = retry_statuses or set()
        attempts = max(0, retries) + 1
        for attempt in range(attempts):
            try:
                response = await self._client.post(path, params=params, json=json_payload)
            except httpx.RequestError as exc:
                if attempt >= attempts - 1:
                    raise EndpointUnreachableError from exc
                continue

            if response.status_code not in retry_statuses or attempt >= attempts - 1:
                return response

        raise EndpointUnreachableError

    def _auth_result(
        self,
        *,
        status: ResultStatus,
        authenticated: bool,
        trace_id: str,
        error_code: str | None = None,
        error_message_sanitized: str | None = None,
        extra_details: Mapping[str, object] | None = None,
    ) -> AuthResult:
        details = self._safe_details()
        if extra_details:
            details.update(extra_details)
        return AuthResult(
            status=status,
            authenticated=authenticated,
            adapter=self.adapter_name,
            mode=self.mode,
            organization_ref=self.organization_ref,
            trace_id=trace_id,
            details=details,
            error_code=error_code,
            error_message_sanitized=error_message_sanitized,
        )

    async def authenticate(self) -> AuthResult:
        trace_id = self._trace_id()
        if self._token:
            return self._auth_result(
                status=ResultStatus.PROVEN,
                authenticated=True,
                trace_id=trace_id,
                extra_details={"token_cached": True},
            )

        if self.auth_configuration.kind is not AuthKind.USER_PASSWORD:
            return self._auth_result(
                status=ResultStatus.BLOCKED,
                authenticated=False,
                trace_id=trace_id,
                error_code="authentication_kind_unsupported",
                error_message_sanitized="server_rest_api_requires_user_password_auth",
            )
        if not self.auth_configuration.username or not self.auth_configuration.password:
            return self._auth_result(
                status=ResultStatus.BLOCKED,
                authenticated=False,
                trace_id=trace_id,
                error_code="authentication_configuration_invalid",
                error_message_sanitized="server_rest_api_credentials_not_configured",
            )

        password_sha1 = hashlib.sha1(self.auth_configuration.password.encode("utf-8")).hexdigest()
        try:
            response = await self._get(
                "api/auth",
                params={
                    "login": self.auth_configuration.username,
                    "pass": password_sha1,
                },
                retries=0,
            )
        except EndpointUnreachableError:
            return self._auth_result(
                status=ResultStatus.UNKNOWN,
                authenticated=False,
                trace_id=trace_id,
                error_code="endpoint_unreachable",
                error_message_sanitized="iiko_server_endpoint_unreachable",
            )
        finally:
            password_sha1 = ""

        if response.status_code == 401:
            return self._auth_result(
                status=ResultStatus.BLOCKED,
                authenticated=False,
                trace_id=trace_id,
                error_code="authentication_rejected",
                error_message_sanitized="iiko_server_authentication_rejected",
            )
        if response.status_code != 200:
            return self._auth_result(
                status=ResultStatus.UNKNOWN,
                authenticated=False,
                trace_id=trace_id,
                error_code="authentication_response_unexpected",
                error_message_sanitized="iiko_server_authentication_response_unexpected",
            )

        token = response.text.strip().strip('"')
        if not self._is_valid_uuid_token(token):
            return self._auth_result(
                status=ResultStatus.UNKNOWN,
                authenticated=False,
                trace_id=trace_id,
                error_code="invalid_auth_token",
                error_message_sanitized="iiko_server_authentication_token_invalid",
            )

        self._token = token
        return self._auth_result(
            status=ResultStatus.PROVEN,
            authenticated=True,
            trace_id=trace_id,
        )

    @staticmethod
    def _is_valid_uuid_token(token: str) -> bool:
        if len(token) != 36:
            return False
        try:
            UUID(token)
        except ValueError:
            return False
        return True

    async def probe(self) -> ProbeResult:
        authentication = await self.authenticate()
        endpoint_reachable: bool | None
        if authentication.status is ResultStatus.PROVEN:
            endpoint_reachable = True
        elif authentication.error_code == "endpoint_unreachable":
            endpoint_reachable = False
        elif authentication.error_code == "authentication_rejected":
            endpoint_reachable = True
        else:
            endpoint_reachable = None

        return ProbeResult(
            status=authentication.status,
            adapter=self.adapter_name,
            mode=self.mode,
            trace_id=self._trace_id(),
            endpoint_reachable=endpoint_reachable,
            authenticated=authentication.authenticated,
            details=authentication.details,
            error_code=authentication.error_code,
            error_message_sanitized=authentication.error_message_sanitized,
        )

    async def list_organizations(self) -> RawResult:
        authentication = await self.authenticate()
        if not authentication.authenticated or self._token is None:
            return self._raw_auth_failure("organizations", authentication)

        trace_id = self._trace_id()
        try:
            response = await self._get(
                "api/corporation/departments",
                params={"key": self._token},
                retries=self.max_retries,
            )
        except EndpointUnreachableError:
            return RawResult(
                status=ResultStatus.UNKNOWN,
                adapter=self.adapter_name,
                mode=self.mode,
                dataset="organizations",
                trace_id=trace_id,
                details=self._safe_details(),
                error_code="endpoint_unreachable",
                error_message_sanitized="iiko_server_endpoint_unreachable",
            )

        if response.status_code == 401:
            self._token = None
            return RawResult(
                status=ResultStatus.BLOCKED,
                adapter=self.adapter_name,
                mode=self.mode,
                dataset="organizations",
                trace_id=trace_id,
                details=self._safe_details(),
                error_code="authentication_rejected",
                error_message_sanitized="iiko_server_authentication_rejected",
            )
        if response.status_code != 200:
            return RawResult(
                status=ResultStatus.UNKNOWN,
                adapter=self.adapter_name,
                mode=self.mode,
                dataset="organizations",
                trace_id=trace_id,
                details=self._safe_details(),
                error_code="departments_response_unexpected",
                error_message_sanitized="iiko_server_departments_response_unexpected",
            )

        try:
            payload = self._parse_department_xml(response.text)
        except ElementTree.ParseError:
            return RawResult(
                status=ResultStatus.UNKNOWN,
                adapter=self.adapter_name,
                mode=self.mode,
                dataset="organizations",
                trace_id=trace_id,
                details=self._safe_details(),
                error_code="departments_xml_invalid",
                error_message_sanitized="iiko_server_departments_xml_invalid",
            )

        if self.organization_ref:
            payload = [item for item in payload if item["id"] == self.organization_ref]

        return RawResult.proven(
            adapter=self.adapter_name,
            mode=self.mode,
            dataset="organizations",
            trace_id=trace_id,
            payload=payload,
            records_count=len(payload),
            details={
                "writes_enabled": False,
                "organization_filter_configured": bool(self.organization_ref),
            },
        )

    def _raw_auth_failure(self, dataset: str, authentication: AuthResult) -> RawResult:
        return RawResult(
            status=authentication.status,
            adapter=self.adapter_name,
            mode=self.mode,
            dataset=dataset,
            trace_id=self._trace_id(),
            details=authentication.details,
            error_code=authentication.error_code,
            error_message_sanitized=authentication.error_message_sanitized,
        )

    @classmethod
    def _parse_department_xml(cls, source: str) -> list[dict[str, str]]:
        root = ElementTree.fromstring(source)
        departments: list[dict[str, str]] = []
        for element in root.iter():
            if cls._local_name(element.tag) != "corporateItemDto":
                continue
            department_type = cls._child_text(element, "type")
            if department_type != "DEPARTMENT":
                continue
            departments.append(
                {
                    "id": cls._child_text(element, "id"),
                    "name": cls._child_text(element, "name"),
                    "type": department_type,
                }
            )
        return departments

    @classmethod
    def _child_text(cls, element: ElementTree.Element, child_name: str) -> str:
        for child in element:
            if cls._local_name(child.tag) == child_name:
                return (child.text or "").strip()
        return ""

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", maxsplit=1)[-1]

    def _blocked(self, dataset: str) -> RawResult:
        return RawResult.blocked(
            adapter=self.adapter_name,
            mode=self.mode,
            dataset=dataset,
            trace_id=self._trace_id(),
            reason=UNSUPPORTED_DATASET_REASON,
        )

    async def list_terminal_groups(self, organization_ref: str) -> RawResult:
        return self._blocked("terminal_groups")

    async def fetch_nomenclature(self, organization_ref: str) -> RawResult:
        return self._blocked("nomenclature")

    async def fetch_menu(self, organization_ref: str) -> RawResult:
        return self._blocked("menu")

    async def fetch_orders_or_sales(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        parameters = parameters or {}
        organization_id = str(parameters.get("organization_id") or self.organization_ref or "")
        if not organization_id:
            return RawResult.blocked(
                adapter=self.adapter_name,
                mode=self.mode,
                dataset="orders_or_sales",
                trace_id=self._trace_id(),
                reason="organization_id_required_for_iiko_sales",
            )

        try:
            date_from = self._date_parameter(parameters, "date_from", "business_date")
            date_to = self._date_parameter(parameters, "date_to", "business_date")
        except ValueError as exc:
            return RawResult.blocked(
                adapter=self.adapter_name,
                mode=self.mode,
                dataset="orders_or_sales",
                trace_id=self._trace_id(),
                reason=str(exc),
            )
        authentication = await self.authenticate()
        if not authentication.authenticated or self._token is None:
            return self._raw_auth_failure("orders_or_sales", authentication)

        reports: dict[str, Any] = {}
        for report_name, group_fields, aggregate_fields in (
            ("daily", DAILY_GROUP_FIELDS, DAILY_AGGREGATE_FIELDS),
            ("payments", PAYMENT_GROUP_FIELDS, DAILY_AGGREGATE_FIELDS),
            ("products", PRODUCT_GROUP_FIELDS, PRODUCT_AGGREGATE_FIELDS),
        ):
            report = await self._fetch_olap_sales_report(
                report_name=report_name,
                organization_id=organization_id,
                date_from=date_from,
                date_to=date_to,
                group_fields=group_fields,
                aggregate_fields=aggregate_fields,
            )
            if isinstance(report, RawResult):
                return report
            reports[report_name] = report

        return RawResult.proven(
            adapter=self.adapter_name,
            mode=self.mode,
            dataset="orders_or_sales",
            trace_id=self._trace_id(),
            payload=reports,
            records_count=sum(len(report.get("data", [])) for report in reports.values()),
            details={
                "writes_enabled": False,
                "report_type": "SALES",
                "source_checksum": calculate_sales_checksum(reports),
            },
        )

    @staticmethod
    def _date_parameter(parameters: Mapping[str, Any], primary: str, fallback: str) -> str:
        value = parameters.get(primary) or parameters.get(fallback)
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, str) and value:
            return value
        raise ValueError(f"{primary} or {fallback} is required")

    def _sales_olap_body(
        self,
        *,
        organization_id: str,
        date_from: str,
        date_to: str,
        group_fields: list[str],
        aggregate_fields: list[str],
    ) -> dict[str, Any]:
        return {
            "reportType": "SALES",
            "groupByRowFields": group_fields,
            "aggregateFields": aggregate_fields,
            "filters": {
                "OpenDate.Typed": {
                    "filterType": "DateRange",
                    "periodType": "CUSTOM",
                    "from": date_from,
                    "to": date_to,
                    "includeLow": True,
                    "includeHigh": True,
                },
                "Department.Id": {
                    "filterType": "IncludeValues",
                    "values": [organization_id],
                },
            },
        }

    async def _fetch_olap_sales_report(
        self,
        *,
        report_name: str,
        organization_id: str,
        date_from: str,
        date_to: str,
        group_fields: list[str],
        aggregate_fields: list[str],
    ) -> dict[str, Any] | RawResult:
        trace_id = self._trace_id()
        try:
            response = await self._post_json(
                "api/v2/reports/olap",
                params={"key": self._token or ""},
                json_payload=self._sales_olap_body(
                    organization_id=organization_id,
                    date_from=date_from,
                    date_to=date_to,
                    group_fields=group_fields,
                    aggregate_fields=aggregate_fields,
                ),
                retries=self.max_retries,
                retry_statuses=RETRYABLE_READ_STATUSES,
            )
        except EndpointUnreachableError:
            return RawResult(
                status=ResultStatus.UNKNOWN,
                adapter=self.adapter_name,
                mode=self.mode,
                dataset="orders_or_sales",
                trace_id=trace_id,
                details=self._safe_details(),
                error_code="endpoint_unreachable",
                error_message_sanitized="iiko_server_sales_endpoint_unreachable",
            )

        if response.status_code in {400, 401, 403, 404}:
            if response.status_code == 401:
                self._token = None
            return RawResult(
                status=ResultStatus.BLOCKED,
                adapter=self.adapter_name,
                mode=self.mode,
                dataset="orders_or_sales",
                trace_id=trace_id,
                details={"writes_enabled": False, "report_name": report_name},
                error_code=f"sales_olap_http_{response.status_code}",
                error_message_sanitized="iiko_server_sales_olap_request_blocked",
            )
        if response.status_code != 200:
            return RawResult(
                status=ResultStatus.UNKNOWN,
                adapter=self.adapter_name,
                mode=self.mode,
                dataset="orders_or_sales",
                trace_id=trace_id,
                details={"writes_enabled": False, "report_name": report_name},
                error_code=f"sales_olap_http_{response.status_code}",
                error_message_sanitized="iiko_server_sales_olap_response_unexpected",
            )

        try:
            parsed = json.loads(response.text, parse_float=Decimal, parse_int=Decimal)
        except json.JSONDecodeError:
            return RawResult(
                status=ResultStatus.UNKNOWN,
                adapter=self.adapter_name,
                mode=self.mode,
                dataset="orders_or_sales",
                trace_id=trace_id,
                details={"writes_enabled": False, "report_name": report_name},
                error_code="sales_olap_json_invalid",
                error_message_sanitized="iiko_server_sales_olap_json_invalid",
            )

        if not isinstance(parsed, dict) or not isinstance(parsed.get("data"), list):
            return RawResult(
                status=ResultStatus.UNKNOWN,
                adapter=self.adapter_name,
                mode=self.mode,
                dataset="orders_or_sales",
                trace_id=trace_id,
                details={"writes_enabled": False, "report_name": report_name},
                error_code="sales_olap_structure_unknown",
                error_message_sanitized="iiko_server_sales_olap_structure_unknown",
            )
        return parsed

    async def fetch_payments(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._blocked("payments")

    async def fetch_inventory(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._blocked("inventory")

    async def fetch_writeoffs(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._blocked("writeoffs")

    async def fetch_costs(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._blocked("costs")

    async def fetch_employees_or_shifts(
        self,
        parameters: Mapping[str, Any] | None = None,
    ) -> RawResult:
        return self._blocked("employees_or_shifts")

    async def close(self) -> None:
        try:
            if self._token is not None:
                try:
                    await self._get("api/logout", params={"key": self._token}, retries=0)
                except EndpointUnreachableError:
                    pass
                finally:
                    self._token = None
        finally:
            await self._client.aclose()
