# Ddd Analysis

_Generated: 2026-05-29T09:32:44.268490+00:00 by M3 Data Architecture Agent (Claude Code)_

### Bounded Context Mapping from SQL Evidence

Domain boundaries are inferred from FK relationship density, table naming conventions, and schema-level clustering. No explicit DDD annotations are present in the evidence.

---

### Northwind Bounded Contexts

#### Context 1: Order Management

**Aggregate Root Candidate:** Orders
**Tables:** Orders, Order Details
**Evidence:** Orders is the FK target from Order Details (HIGH confidence, FK_Order_Details_Orders). Order Details has no independent lifecycle — it is subordinate to Orders.

```
OrderAggregate
├── Orders (aggregate root candidate)
│   ├── OrderID (identity)
│   ├── CustomerID (FK → Customer context)
│   ├── EmployeeID (FK → Employee context)
│   └── ShipVia (FK → Logistics context)
└── Order Details (aggregate child)
    ├── OrderID (FK → Orders)
    └── ProductID (FK → Catalog context)
```

**Cross-context dependencies:** CustomerID → Customer context, EmployeeID → Employee context, ShipVia → Logistics context, ProductID → Catalog context. Order Management has the highest cross-context coupling in the database.

**DDD Classification:** INFERRED — no DDD annotations in evidence. Classification based on FK density and naming.

---

#### Context 2: Product Catalog

**Aggregate Root Candidate:** Products
**Tables:** Products, Categories
**Evidence:** Products references Categories via FK_Products_Categories (HIGH). Categories has no independent transactional behavior.

```
CatalogAggregate
├── Products (aggregate root candidate)
│   ├── ProductID (identity)
│   ├── CategoryID (FK → Categories — within context)
│   └── SupplierID (FK → Supplier context)
└── Categories (reference data within context)
```

**Cross-context dependencies:** SupplierID → Supplier context (external). Products is consumed by Order Management context.

---

#### Context 3: Customer Management

**Aggregate Root Candidate:** Customers
**Tables:** Customers, CustomerCustomerDemo, CustomerDemographics
**Evidence:** CustomerCustomerDemo has a LOW-confidence FK to Customers. CustomerDemographics is referenced by CustomerCustomerDemo.

```
CustomerAggregate
├── Customers (aggregate root candidate)
│   └── CustomerID (identity)
├── CustomerCustomerDemo (demographic junction — missing PK)
│   ├── CustomerID (FK → Customers)
│   └── CustomerTypeID (FK → CustomerDemographics)
└── CustomerDemographics (demographic type reference)
    └── CustomerTypeID (identity — no PK enforced)
```

**Governance risk:** CustomerCustomerDemo and CustomerDemographics both lack PKs — aggregate integrity is currently unenforced at the SQL level.

---

#### Context 4: Employee / HR

**Aggregate Root Candidate:** Employees
**Tables:** Employees, EmployeeTerritories
**Evidence:** Employees has a self-referencing FK (ReportsTo) for hierarchy. EmployeeTerritories links Employees to Territories.

```
EmployeeAggregate
├── Employees (aggregate root candidate)
│   ├── EmployeeID (identity)
│   └── ReportsTo (self-FK — management hierarchy)
└── EmployeeTerritories (territory assignment — missing PK)
    ├── EmployeeID (FK → Employees — LOW confidence)
    └── TerritoryID (FK → Territories — inferred)
```

**Cross-context ambiguity:** EmployeeTerritories.EmployeeID also inferred to pubs.employee — cross-database pollution risk. CONFIRMED naming collision.

---

#### Context 5: Geography

**Tables:** Region, Territories
**Evidence:** Territories references Region via RegionID (LOW confidence inference).

```
GeographyContext (Reference Data)
├── Region (no PK enforced)
│   └── RegionID (identity — missing constraint)
└── Territories (no PK enforced)
    ├── TerritoryID (identity — missing constraint)
    └── RegionID (FK → Region — LOW)
```

**Assessment:** Geography is a reference data context. Both tables lack PKs — this context is currently ungoverned. CONFIRMED.

---

#### Context 6: Supplier Management

**Aggregate Root Candidate:** Suppliers
**Tables:** Suppliers (standalone — no child tables confirmed)
**Evidence:** Suppliers is an FK target from Products (HIGH). No tables are subordinate to Suppliers.

**Cross-context dependency:** Products depends on Suppliers — Catalog context depends on Supplier context.

---

#### Context 7: Logistics

**Tables:** Shippers (standalone)
**Evidence:** Shippers is an FK target from Orders via ShipVia (HIGH).

**Assessment:** Shippers is a thin reference-data context. CONFIRMED.

---

### pubs Bounded Contexts

#### Context 8: Publishing Content

**Aggregate Root Candidate:** titles
**Tables:** titles, authors, titleauthor
**Evidence:** titleauthor joins titles and authors (junction table, HIGH structure).

```
ContentAggregate
├── titles (aggregate root candidate — nullable PK VIOLATION)
│   └── title_id (identity — NULLABLE)
├── authors (contributing entity — nullable PK VIOLATION)
│   └── au_id (identity — NULLABLE)
└── titleauthor (authorship junction — missing PK)
    ├── au_id (FK → authors)
    └── title_id (FK → titles)
```

**CRITICAL:** Both root candidates have nullable PKs. Aggregate integrity is fundamentally broken.

---

#### Context 9: Publisher Operations

**Tables:** publishers, pub_info, employee, jobs
**Evidence:** employee references publishers via pub_id. pub_info is a 1:1 extension of publishers (INFERRED).

```
PublisherAggregate
├── publishers (aggregate root)
│   └── pub_id (identity)
├── pub_info (1:1 extension — no FK confirmed)
└── PublishingEmployeeAggregate
    ├── employee (aggregate root candidate — nullable PK VIOLATION)
    │   └── emp_id (identity — NULLABLE)
    └── jobs (job classification reference)
```

---

#### Context 10: Bookstore Sales

**Tables:** sales, stores, discounts
**Evidence:** sales references stores via stor_id. discounts optionally references stores.

```
SalesAggregate
├── stores (aggregate root candidate)
│   └── stor_id (identity)
├── sales (sales transaction — no PK)
│   ├── stor_id (FK → stores)
│   └── title_id (FK → titles — nullable)
└── discounts (discount rules — no PK)
    └── stor_id (optional FK → stores)
```

---

#### Context 11: Royalty Management

**Tables:** roysched
**Evidence:** roysched references titles via title_id. Stored procedure `dbo.byroyalty` reads titleauthor.

```
RoyaltyContext
└── roysched (royalty schedule — no PK)
    └── title_id (FK → titles)
```

---

### Cross-Context Dependency Graph (DDD)

```
Northwind:
  Order Management  →  Customer Management (CustomerID)
  Order Management  →  Employee/HR (EmployeeID)
  Order Management  →  Logistics (ShipVia)
  Order Management  →  Product Catalog (ProductID via Order Details)
  Product Catalog   →  Supplier Management (SupplierID)
  Product Catalog   →  Geography (via Categories — indirect)
  Employee/HR       →  Geography (TerritoryID via EmployeeTerritories)

pubs:
  Bookstore Sales   →  Publishing Content (title_id)
  Publisher Ops     →  Publishing Content (pub_id)
  Royalty Mgmt      →  Publishing Content (title_id)

Cross-database (LOW confidence — potential false positive):
  Northwind.EmployeeTerritories → pubs.employee (ambiguous EmployeeID)
```

---

### Aggregate Root Recommendations Summary

| Context | Recommended Aggregate Root | Current PK Status | Confidence |
|---|---|---|---|
| Order Management | Orders | CONFIRMED, valid | HIGH |
| Product Catalog | Products | CONFIRMED, valid | HIGH |
| Customer Management | Customers | CONFIRMED, valid | HIGH |
| Employee/HR | Employees | CONFIRMED, valid | HIGH |
| Geography | Region | MISSING PK — cannot serve as root | MEDIUM |
| Supplier Management | Suppliers | CONFIRMED, valid | HIGH |
| Logistics | Shippers | CONFIRMED, valid | HIGH |
| Publishing Content | titles | NULLABLE PK — currently invalid as root | HIGH |
| Publisher Operations | publishers | CONFIRMED, valid | HIGH |
| Bookstore Sales | stores | CONFIRMED, valid | HIGH |
| Royalty Management | titles (foreign) | No independent root | MEDIUM |

---
