# Data Lineage

_Generated: 2026-05-29T09:32:44.268490+00:00 by M3 Data Architecture Agent (Claude Code)_

**IMPORTANT:** Zero API endpoints and zero ORM handlers are present in the evidence. No frontend-to-database lineage can be traced. The lineage layer that exists is entirely SQL-internal: stored procedures and views.

---

### Confirmed SQL Lineage Summary

Source: SQL_LINEAGE evidence — 49 edges, 74 chains, 24 tables, 17 views, 12 procedures.

---

### Stored Procedure Lineage

| Procedure | Reads | Writes | Parameters | Lineage Path |
|---|---|---|---|---|
| dbo.Ten Most Expensive Products | Products | — | — | PROC → Products |
| dbo.Employee Sales by Country | Employees, Order Subtotals | — | @Beginning_Date, @Ending_Date | PROC → Employees + VIEW(Order Subtotals) |
| dbo.Sales by Year | Order Subtotals, Orders | — | @Beginning_Date, @Ending_Date | PROC → VIEW(Order Subtotals) → Orders |
| dbo.CustOrdersDetail | Products | — | @OrderID | PROC → Products [implied Order Details join] |
| dbo.CustOrdersOrders | Orders | — | @CustomerID | PROC → Orders |
| dbo.CustOrderHist | Products | — | @CustomerID | PROC → Products [implied join] |
| dbo.SalesByCategory | Order Details | — | @CategoryName, @OrdYear | PROC → Order Details [implied Category join] |
| dbo.section | — | — | — | Empty/stub — no reads or writes confirmed |
| dbo.byroyalty | titleauthor | — | @percentage | PROC → titleauthor |
| dbo.reptq1 | titles | — | — | PROC → titles |
| dbo.reptq2 | titles | — | — | PROC → titles |
| dbo.reptq3 | titles | — | — | PROC → titles [royalty range filter] |

All 12 stored procedures are READ-ONLY (SELECT). Zero WRITE operations detected. CONFIRMED.

---

### View Lineage (Partial — 17 views detected, full DDL not in evidence)

Views confirmed by relationship inference:
- **Order Subtotals** — reads from Orders (MEDIUM confidence)
- **Order Details Extended** — reads from Categories (MEDIUM confidence)

Views referenced by stored procedures:
- `Order Subtotals` — consumed by `Employee Sales by Country` and `Sales by Year`

**Lineage Gap:** Full view DDL not extracted. 17 views exist but their complete column-level lineage cannot be reconstructed from available evidence. CONFIRMED GAP.

---

### Lineage Completeness Assessment

| Lineage Layer | Status | Evidence |
|---|---|---|
| UI Layer | NOT PRESENT — no frontend detected | CONFIRMED |
| API Layer | NOT PRESENT — 0 endpoints | CONFIRMED |
| DTO Layer | NOT PRESENT — no application code | CONFIRMED |
| Service Layer | NOT PRESENT | CONFIRMED |
| Repository Layer | NOT PRESENT — no ORM | CONFIRMED |
| SQL Procedure Layer | PARTIAL — 12 procs traced | CONFIRMED |
| View Layer | PARTIAL — 17 views, limited DDL | INFERRED |
| Table Layer | COMPLETE — 24 tables catalogued | CONFIRMED |
| External Integration | NOT DETECTED | CONFIRMED |

**Overall Lineage Completeness: LOW** — the architecture is a pure SQL database layer with no confirmed application tier. End-to-end lineage from any user interaction to data storage cannot be reconstructed from available evidence.

---
