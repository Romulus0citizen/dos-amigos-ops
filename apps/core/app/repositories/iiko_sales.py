from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from apps.core.app.models.sales import (
    IikoSalesDaily,
    IikoSalesDailyPayment,
    IikoSalesDailyProduct,
    IikoSalesSyncRun,
)
from integrations.iiko.sales import NormalizedSalesReport


@dataclass(frozen=True)
class SalesPersistenceResult:
    persisted_rows: int
    unchanged: bool


class IikoSalesRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_daily(self, organization_id: str, business_date: date) -> IikoSalesDaily | None:
        return self.session.get(
            IikoSalesDaily,
            {
                "organization_id": organization_id,
                "business_date": business_date,
            },
        )

    def list_payments(
        self,
        organization_id: str,
        business_date: date,
    ) -> list[IikoSalesDailyPayment]:
        statement = (
            select(IikoSalesDailyPayment)
            .where(IikoSalesDailyPayment.organization_id == organization_id)
            .where(IikoSalesDailyPayment.business_date == business_date)
            .order_by(
                IikoSalesDailyPayment.payment_category,
                IikoSalesDailyPayment.payment_type_name,
                IikoSalesDailyPayment.payment_type_key,
            )
        )
        return list(self.session.scalars(statement))

    def list_top_products(
        self,
        organization_id: str,
        business_date: date,
        *,
        limit: int,
    ) -> list[IikoSalesDailyProduct]:
        statement = (
            select(IikoSalesDailyProduct)
            .where(IikoSalesDailyProduct.organization_id == organization_id)
            .where(IikoSalesDailyProduct.business_date == business_date)
            .order_by(
                IikoSalesDailyProduct.net_sales.desc(),
                IikoSalesDailyProduct.quantity.desc(),
                IikoSalesDailyProduct.product_id,
                IikoSalesDailyProduct.product_size_key,
            )
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def persist_report(
        self,
        report: NormalizedSalesReport,
        *,
        started_at: datetime,
        finished_at: datetime,
    ) -> SalesPersistenceResult:
        daily = report.daily
        existing = self.get_daily(daily.organization_id, daily.business_date)
        if existing and existing.source_checksum == daily.source_checksum:
            self.session.add(
                self._sync_run(
                    report,
                    started_at=started_at,
                    finished_at=finished_at,
                    persisted_rows=0,
                )
            )
            return SalesPersistenceResult(persisted_rows=0, unchanged=True)

        self.session.execute(
            delete(IikoSalesDailyPayment)
            .where(IikoSalesDailyPayment.organization_id == daily.organization_id)
            .where(IikoSalesDailyPayment.business_date == daily.business_date)
        )
        self.session.execute(
            delete(IikoSalesDailyProduct)
            .where(IikoSalesDailyProduct.organization_id == daily.organization_id)
            .where(IikoSalesDailyProduct.business_date == daily.business_date)
        )
        if existing:
            self.session.delete(existing)
            self.session.flush()

        self.session.add(
            IikoSalesDaily(
                organization_id=daily.organization_id,
                business_date=daily.business_date,
                currency_code=daily.currency_code,
                gross_sales=daily.gross_sales,
                reported_discounts=daily.reported_discounts,
                reported_increases=daily.reported_increases,
                net_sales=daily.net_sales,
                unexplained_adjustment=daily.unexplained_adjustment,
                refunds=daily.refunds,
                checks_count=daily.checks_count,
                guests_count=daily.guests_count,
                average_check=daily.average_check,
                source_rows_count=daily.source_rows_count,
                source_checksum=daily.source_checksum,
                result_status=daily.result_status.value,
                reconciliation_error_code=daily.reconciliation_error_code,
                imported_at=finished_at,
                updated_at=finished_at,
            )
        )
        for payment in report.payments:
            self.session.add(
                IikoSalesDailyPayment(
                    organization_id=payment.organization_id,
                    business_date=payment.business_date,
                    payment_type_id=payment.payment_type_id,
                    payment_type_key=payment.payment_type_key,
                    payment_type_name=payment.payment_type_name,
                    payment_category=payment.payment_category.value,
                    sales_amount=payment.sales_amount,
                    refund_amount=payment.refund_amount,
                    transactions_count=payment.transactions_count,
                    imported_at=finished_at,
                    updated_at=finished_at,
                )
            )
        for product in report.products:
            self.session.add(
                IikoSalesDailyProduct(
                    organization_id=product.organization_id,
                    business_date=product.business_date,
                    product_id=product.product_id,
                    product_size_id=product.product_size_id,
                    product_size_key=product.product_size_key,
                    product_name_snapshot=product.product_name_snapshot,
                    quantity=product.quantity,
                    gross_sales=product.gross_sales,
                    discounts=product.discounts,
                    net_sales=product.net_sales,
                    refund_quantity=product.refund_quantity,
                    refund_amount=product.refund_amount,
                    imported_at=finished_at,
                    updated_at=finished_at,
                )
            )

        persisted_rows = 1 + len(report.payments) + len(report.products)
        self.session.add(
            self._sync_run(
                report,
                started_at=started_at,
                finished_at=finished_at,
                persisted_rows=persisted_rows,
            )
        )
        return SalesPersistenceResult(persisted_rows=persisted_rows, unchanged=False)

    def record_failed_run(
        self,
        *,
        organization_id: str,
        business_date: date,
        status: str,
        started_at: datetime,
        finished_at: datetime,
        fetched_rows: int,
        error_code: str,
        error_message_redacted: str,
    ) -> None:
        self.session.add(
            IikoSalesSyncRun(
                organization_id=organization_id,
                date_from=business_date,
                date_to=business_date,
                dataset="orders_or_sales",
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                fetched_rows=fetched_rows,
                persisted_rows=0,
                source_checksum=None,
                error_code=error_code,
                error_message_redacted=error_message_redacted,
            )
        )

    @staticmethod
    def _sync_run(
        report: NormalizedSalesReport,
        *,
        started_at: datetime,
        finished_at: datetime,
        persisted_rows: int,
    ) -> IikoSalesSyncRun:
        daily = report.daily
        return IikoSalesSyncRun(
            organization_id=daily.organization_id,
            date_from=daily.business_date,
            date_to=daily.business_date,
            dataset="orders_or_sales",
            status=daily.result_status.value,
            started_at=started_at,
            finished_at=finished_at,
            fetched_rows=daily.source_rows_count,
            persisted_rows=persisted_rows,
            source_checksum=daily.source_checksum,
            error_code=daily.reconciliation_error_code,
            error_message_redacted=None,
        )
