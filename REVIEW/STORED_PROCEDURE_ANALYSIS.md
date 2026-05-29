# Stored Procedure Analysis

_Generated: 2026-05-29T09:26:53.461477+00:00_

**Total Procedures:** 12 | **Functions:** 0 | **Command:** 0 | **Query:** 11 | **With Dynamic SQL:** 0 | **High Risk:** 0 | **With Transactions:** 0

## Tables Touched by Stored Procedures (7 total)

`Employees`, `Order Details`, `Order Subtotals`, `Orders`, `Products`, `titleauthor`, `titles`

## All Stored Procedures

| Name | Type | CRUD | Tables | Inputs | Outputs | Calls | Depth | Txn | DynSQL |
|------|------|------|--------|--------|---------|-------|-------|-----|--------|
| `Ten Most Expensive Products` | PROCEDURE | SELECT | Products | 0 | 0 | 0 | 0 | — | — |
| `Employee Sales by Country` | PROCEDURE | SELECT | Employees, Order Subtotals | 2 | 0 | 0 | 0 | — | — |
| `Sales by Year` | PROCEDURE | SELECT | Order Subtotals, Orders | 2 | 0 | 0 | 0 | — | — |
| `CustOrdersDetail` | PROCEDURE | SELECT | Products | 1 | 0 | 0 | 0 | — | — |
| `CustOrdersOrders` | PROCEDURE | SELECT | Orders | 2 | 0 | 0 | 0 | — | — |
| `CustOrderHist` | PROCEDURE | SELECT | Products | 1 | 0 | 0 | 0 | — | — |
| `SalesByCategory` | PROCEDURE | SELECT | Order Details | 2 | 0 | 0 | 0 | — | — |
| `section` | PROCEDURE |  |  | 0 | 0 | 0 | 0 | — | — |
| `byroyalty` | PROCEDURE | SELECT | titleauthor | 1 | 0 | 0 | 0 | — | — |
| `reptq1` | PROCEDURE | SELECT | titles | 0 | 0 | 0 | 0 | — | — |
| `reptq2` | PROCEDURE | SELECT | titles | 0 | 0 | 0 | 0 | — | — |
| `reptq3` | PROCEDURE | SELECT | titles | 4 | 0 | 0 | 0 | — | — |

