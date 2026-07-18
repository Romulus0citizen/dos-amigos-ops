# iiko Sales Discovery

Status: PARTIAL

Date: 2026-07-18

## Confirmed Read-Only Source

The read-only source for S1.6.1 is iikoServer OLAP reports.

Metadata:

```text
GET /api/v2/reports/olap/columns
query: reportType=SALES
```

Data:

```text
POST /api/v2/reports/olap
query: key=<auth-token>
content-type: application/json
```

The response shape is:

```json
{
  "data": [],
  "summary": []
}
```

The token is supplied only as an authenticated iiko session key. It must not be logged, printed, stored, or embedded in docs.

## Daily Request

`groupByRowFields`:

```json
[
  "OpenDate.Typed",
  "Department.Id",
  "Storned",
  "OrderDeleted"
]
```

`aggregateFields`:

```json
[
  "DishSumInt",
  "DishDiscountSumInt",
  "DiscountSum",
  "IncreaseSum",
  "DishReturnSum",
  "UniqOrderId.OrdersCount"
]
```

Filters:

```json
{
  "OpenDate.Typed": {
    "filterType": "DateRange",
    "periodType": "CUSTOM",
    "from": "YYYY-MM-DD",
    "to": "YYYY-MM-DD",
    "includeLow": true,
    "includeHigh": true
  },
  "Department.Id": {
    "filterType": "IncludeValues",
    "values": ["<organization-id>"]
  }
}
```

Normal sale rows are only:

- `Storned = "FALSE"`
- `OrderDeleted = "NOT_DELETED"`

Other status values are diagnostics and must not be silently included in ordinary revenue.

## Payment Request

`groupByRowFields`:

```json
[
  "OpenDate.Typed",
  "Department.Id",
  "PayTypes.Group",
  "PayTypes.GUID",
  "PayTypes",
  "Storned",
  "OrderDeleted"
]
```

`aggregateFields` are the same as the daily request.

Rules:

- Payment amount uses `DishDiscountSumInt`.
- Payment row totals must match daily `net_sales` within `0.01`.
- `UniqOrderId.OrdersCount` from payment rows must not be summed because mixed-payment orders can appear in multiple rows.
- `PayTypes.Group = CASH` maps to `cash`.
- `PayTypes.Group = CARD` maps to `card`.
- `PayTypes.Group = NON_CASH` maps to `other` unless an explicit `PayTypes.GUID` mapping is configured.
- Unknown payment groups map to `unknown` and make the result `PARTIAL`.

## Product Request

`groupByRowFields`:

```json
[
  "OpenDate.Typed",
  "Department.Id",
  "DishId",
  "DishName",
  "DishSize.Id",
  "Storned",
  "OrderDeleted"
]
```

`aggregateFields`:

```json
[
  "DishAmountInt",
  "DishSumInt",
  "DishDiscountSumInt",
  "DishReturnSum"
]
```

Rules:

- `DishId` is the iiko product id.
- `DishName` is stored as a reporting snapshot.
- `DishSize.Id` is nullable.
- `DishAmountInt` is quantity.
- `DishSumInt` is product gross sales.
- `DishDiscountSumInt` is product net sales.
- `DishReturnSum` is refund amount.
- `refund_quantity` remains null because no stable refund quantity field is confirmed.

Confirmed real-day aggregate checks, without raw rows, product names, payment GUIDs, tokens, or PII:

- Product rows: 38
- Product quantity total: 83
- Product gross total: 34645
- Product net total: 31955.25
- Product refund total: 0
- Product gross/net totals matched daily totals.
- Payment `DishDiscountSumInt` totals matched daily `net_sales`.

## Field Semantics

| Field | Use | Status |
|---|---|---|
| `DishSumInt` | gross sales before discounts | PROVEN |
| `DishDiscountSumInt` | net sales after discounts/payment amount | PROVEN |
| `UniqOrderId.OrdersCount` | checks count from daily request only | PROVEN |
| `DiscountSum` | reported discounts | PARTIAL |
| `IncreaseSum` | reported increases | PARTIAL |
| `DishReturnSum` | refund amount | PARTIAL until non-zero refund day is verified |
| `PayTypes.GUID` | payment type id | PROVEN |
| `PayTypes.Group` | payment category source enum | PARTIAL for unknown/NON_CASH mapping |
| `DishAmountInt` | product quantity | PROVEN |
| `DishId` | product id | PROVEN |
| `DishSize.Id` | optional product size id | PROVEN nullable |

Do not assume:

```text
gross_sales - DiscountSum + IncreaseSum = net_sales
```

When the absolute unexplained adjustment is greater than `0.01`, keep the import, mark the daily result `PARTIAL`, and set:

```text
IIKO_DISCOUNT_RECONCILIATION_MISMATCH
```

## Safety

The implementation does not include:

- real dish names;
- real payment GUIDs;
- raw production responses;
- passwords;
- password hashes;
- tokens;
- guest names or phones;
- card data;
- employee personal data.
