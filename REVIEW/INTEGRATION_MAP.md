# Integration Map

_Generated: 2026-05-29T09:32:44.268490+00:00 by M3 Data Architecture Agent (Claude Code)_

### Integration Layer Assessment

**No external integrations confirmed.** Zero API endpoints, zero message queue references, zero ETL pipeline definitions, and zero external system references detected in evidence. The architecture is a self-contained SQL Server relational database system.

All integration points are internal to the SQL layer.

---

### Internal Integration Points

#### Integration Point 1 — Stored Procedures as Business Logic Interface

| Attribute | Detail |
|---|---|
| Type | SQL Stored Procedure |
| Count | 12 procedures |
| Direction | Read-only (SELECT only) |
| Consumers | Unknown — no application layer detected |
| Risk | Procedures are the only confirmed business logic layer |

Procedures effectively act as a read API for the database:

```
External Consumer (unknown)
         ↓
[dbo.CustOrdersOrders(@CustomerID)]     → Orders table
[dbo.CustOrdersDetail(@OrderID)]        → Products table
[dbo.CustOrderHist(@CustomerID)]        → Products table
[dbo.Employee Sales by Country(dates)]  → Employees + Order Subtotals view
[dbo.Sales by Year(dates)]              → Order Subtotals view + Orders
[dbo.Ten Most Expensive Products]       → Products
[dbo.SalesByCategory(@CategoryName)]    → Order Details
[dbo.byroyalty(@percentage)]            → titleauthor
[dbo.reptq1/reptq2/reptq3]             → titles
```

---

#### Integration Point 2 — Views as Reporting Interface (17 views)

Views serve as the reporting and aggregation layer between raw tables and consumers.

| Confirmed Views | Source Tables (inferred) | Used By |
|---|---|---|
| Order Subtotals | Orders | Employee Sales by Country, Sales by Year procs |
| Order Details Extended | Categories, Order Details, Products | inferred |
| (15 additional views) | DDL not fully extracted | Unknown |

---

#### Integration Point 3 — Cross-Database Reference Risk

**INFERRED — LOW confidence**

`EmployeeTerritories.EmployeeID` has inferred relationships to both `Northwind.Employees` and `pubs.employee`. If this is a genuine cross-database join in any application, it represents a tight coupling between two logically independent databases.

```
Northwind.EmployeeTerritories.EmployeeID
    → Northwind.Employees.EmployeeID  [correct domain relationship]
    → pubs.employee.emp_id            [potential cross-DB coupling — LOW confidence]
```

**Risk:** Any application-layer code performing this cross-database join creates an undocumented integration boundary that breaks database independence.

---

#### Integration Point 4 — stub Procedure (dbo.section)

`dbo.section` is a confirmed stored procedure with zero reads and zero writes. It may be:
- An empty/stub placeholder
- A procedure whose DDL was not parseable
- A deleted procedure with retained signature

**Risk:** Unknown. If this procedure is called by an application, it produces no data — silent integration failure.

---

### Integration Map Summary

```
┌───────────────────────────────────────────────────────────┐
│              UNKNOWN EXTERNAL CONSUMERS                   │
│         (application layer not detected)                  │
└─────────────────┬─────────────────────────────────────────┘
                  │
                  ▼
┌───────────────────────────────────────────────────────────┐
│         SQL SERVER INTEGRATION LAYER                      │
│                                                           │
│  ┌─────────────────────────┐  ┌────────────────────────┐  │
│  │  12 Stored Procedures   │  │  17 Views              │  │
│  │  (all read-only)        │  │  (reporting layer)     │  │
│  └──────────┬──────────────┘  └───────────┬────────────┘  │
│             │                             │               │
│             └──────────────┬──────────────┘               │
│                            ▼                              │
│  ┌────────────────────────────────────────────────────┐   │
│  │  NORTHWIND DATABASE (24 tables confirmed)          │   │
│  └────────────────────────────────────────────────────┘   │
│  ┌────────────────────────────────────────────────────┐   │
│  │  PUBS DATABASE (11 tables confirmed)               │   │
│  └────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────┘
         ⚠ Cross-DB coupling risk (LOW confidence)
```

---
