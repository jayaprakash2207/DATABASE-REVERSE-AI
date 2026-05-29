# Data Architecture

_Generated: 2026-05-29T09:32:44.268490+00:00 by M3 Data Architecture Agent (Claude Code)_

**Architecture Pattern:** Database-first, SQL Server relational schema. No ORM layer, API layer, or application code detected. Architecture is pure SQL with stored procedures and views as the integration and reporting layer.

**Databases Detected:** Two co-located SQL Server databases confirmed in evidence — **Northwind** (commerce/ERP domain) and **pubs** (publishing domain). These share a single SQL Server instance but represent logically distinct business domains with no confirmed FK cross-database constraints.

---

### Domain Summary Table

| Domain | Database | Tables | Core Aggregate | Description |
|---|---|---|---|---|
| Order Management | Northwind | Orders, Order Details | OrderAggregate | Transactional order lifecycle |
| Customer Management | Northwind | Customers, CustomerCustomerDemo, CustomerDemographics | CustomerAggregate | Customer identity and segmentation |
| Product Catalog | Northwind | Products, Categories | ProductAggregate / CategoriesAggregate | Product classification and pricing |
| Supplier Management | Northwind | Suppliers | SupplierAggregate | External supplier records |
| Employee / HR | Northwind | Employees, EmployeeTerritories | EmployeeAggregate | Staff hierarchy and territory assignments |
| Logistics | Northwind | Shippers | ShippersAggregate | Shipping carrier registry |
| Geography | Northwind | Region, Territories | RegionAggregate / TerritoriesAggregate | Geographic segmentation |
| Publishing Content | pubs | titles, authors, titleauthor | TitlesAggregate / AuthorsAggregate | Book catalog and authorship |
| Publishing Operations | pubs | publishers, pub_info | PublishersAggregate | Publisher registry and metadata |
| Bookstore Sales | pubs | sales, stores, discounts | SalesAggregate / StoresAggregate | Retail sales and store discounts |
| Royalties | pubs | roysched, titleauthor | RoyschedAggregate | Author royalty schedule logic |
| Publishing HR | pubs | employee, jobs | EmployeeAggregate | Publishing staff and job classification |

---

### Aggregate Root Assessment (INFERRED — no ORM or DDD annotations present in evidence)

All 24 entities carry `agg_root=False` in extraction evidence. Aggregate root candidates are inferred from FK relationship density and naming:

| Candidate Aggregate Root | Basis | Confidence |
|---|---|---|
| Orders | Central FK target from Order Details; high FK density | MEDIUM |
| Customers | FK target from Orders, CustomerCustomerDemo | MEDIUM |
| Products | FK target from Order Details, referenced by 3 stored procs | MEDIUM |
| Employees | Self-referencing hierarchy; FK target from Orders | MEDIUM |
| titles | FK target from titleauthor, sales, roysched | MEDIUM |
| publishers | FK target for employee (pub_id) | MEDIUM |

---

### Bounded Context Dependency Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  DATABASE: Northwind                                                        │
│                                                                             │
│  ┌──────────────────┐     ┌────────────────────┐     ┌──────────────────┐  │
│  │  Customer Ctx    │────▶│  Order Mgmt Ctx    │────▶│  Logistics Ctx   │  │
│  │  Customers       │     │  Orders            │     │  Shippers        │  │
│  │  CustCustomerDemo│     │  Order Details     │     └──────────────────┘  │
│  │  CustDemographics│     └────────┬───────────┘                           │
│  └──────────────────┘             │                                        │
│                                   │                                        │
│  ┌──────────────────┐             ▼                                        │
│  │  Employee Ctx    │     ┌────────────────────┐     ┌──────────────────┐  │
│  │  Employees       │◀────│  Product Catalog   │     │  Geography Ctx   │  │
│  │  EmpTerritories  │     │  Products          │     │  Region          │  │
│  └──────────────────┘     │  Categories        │     │  Territories     │  │
│           │               └────────────────────┘     └──────────────────┘  │
│           │                        ▲                                        │
│           │               ┌────────────────────┐                           │
│           │               │  Supplier Ctx      │                           │
│           │               │  Suppliers         │                           │
│           │               └────────────────────┘                           │
└───────────┼─────────────────────────────────────────────────────────────────┘
            │
            │ ⚠ LOW-CONFIDENCE cross-database coupling
            │   (EmployeeTerritories → employee [pubs])
            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  DATABASE: pubs                                                             │
│                                                                             │
│  ┌──────────────────┐     ┌────────────────────┐     ┌──────────────────┐  │
│  │  Publishing HR   │     │  Publishing Content│     │  Royalty Ctx     │  │
│  │  employee        │     │  titles            │────▶│  roysched        │  │
│  │  jobs            │     │  authors           │     │  titleauthor     │  │
│  └──────────────────┘     │  titleauthor       │     └──────────────────┘  │
│                           └────────┬───────────┘                           │
│  ┌──────────────────┐             ▼                                        │
│  │  Publisher Ctx   │     ┌────────────────────┐                           │
│  │  publishers      │     │  Bookstore Sales   │                           │
│  │  pub_info        │     │  sales             │                           │
│  └──────────────────┘     │  stores            │                           │
│                           │  discounts         │                           │
│                           └────────────────────┘                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

**Key Architectural Observations:**

1. **No application tier detected.** Zero API endpoints and zero ORM entities confirmed. Architecture is SQL-only. All business logic resides in 12 stored procedures and 17 views. CONFIRMED (evidence: APIS=0, STACK=SQL only).
2. **Two logically independent databases co-located.** Northwind and pubs share a SQL Server instance. No confirmed FK constraints cross databases. CONFIRMED.
3. **Cross-database naming collision.** `Employees` (Northwind) and `employee` (pubs) represent different business entities in different domains but share an EmployeeID/emp_id concept. CONFIRMED — both appear in relationship evidence.
4. **No event-driven architecture.** No messaging, queue, or event table patterns detected. CONFIRMED by absence.

---
