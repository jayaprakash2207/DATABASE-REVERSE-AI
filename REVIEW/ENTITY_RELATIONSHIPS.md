# Entity Relationships

_Generated: 2026-05-29T09:32:44.268490+00:00 by M3 Data Architecture Agent (Claude Code)_

### All Relationships — Master Table

| # | Source | Target | Cardinality | FK Field | Constraint Name | Confidence | Type | Evidence |
|---|---|---|---|---|---|---|---|---|
| 1 | Order Details | Orders | Many→One | OrderID | FK_Order_Details_Orders | HIGH | FK_CONSTRAINT | SQL_RELATIONSHIPS |
| 2 | Order Details | Products | Many→One | ProductID | FK_Order_Details_Products | HIGH | FK_CONSTRAINT | SQL_RELATIONSHIPS |
| 3 | Orders | Customers | Many→One | CustomerID | FK_Orders_Customers | HIGH | FK_CONSTRAINT | SQL_RELATIONSHIPS |
| 4 | Orders | Employees | Many→One | EmployeeID | FK_Orders_Employees | HIGH | FK_CONSTRAINT | SQL_RELATIONSHIPS |
| 5 | Orders | Shippers | Many→One | ShipVia | FK_Orders_Shippers | HIGH | FK_CONSTRAINT | SQL_RELATIONSHIPS |
| 6 | Products | Suppliers | Many→One | SupplierID | FK_Products_Suppliers | HIGH | FK_CONSTRAINT | SQL_RELATIONSHIPS |
| 7 | Products | Categories | Many→One | CategoryID | FK_Products_Categories | HIGH | FK_CONSTRAINT | SQL_RELATIONSHIPS |
| 8 | Employees | Employees | Self-ref | ReportsTo | FK_Employees_Employees | HIGH | FK_CONSTRAINT | SQL_RELATIONSHIPS |
| 9 | Categories | Products | Many→One | (inferred) | — | MEDIUM | INFERRED_JOIN | SQL_RELATIONSHIPS |
| 10 | Categories | Order Details Extended | Many→One | (inferred) | — | MEDIUM | INFERRED_JOIN | SQL_RELATIONSHIPS |
| 11 | Customers | Suppliers | Many→One | (inferred) | — | MEDIUM | INFERRED_JOIN | SQL_RELATIONSHIPS |
| 12 | Customers | Orders | Many→One | (inferred) | — | MEDIUM | INFERRED_JOIN | SQL_RELATIONSHIPS |
| 13 | Customers | Order Subtotals | Many→One | (inferred) | — | MEDIUM | INFERRED_JOIN | VIEW_JOIN |
| 14 | Order Details | Shippers | Many→One | (inferred) | — | MEDIUM | INFERRED_JOIN | SQL_RELATIONSHIPS |
| 15 | Order Subtotals | Orders | Many→One | (inferred) | — | MEDIUM | INFERRED_JOIN | VIEW_JOIN |
| 16 | CustomerCustomerDemo | Customers | Many→One | CustomerID | — | LOW | INFERRED_JOIN | SQL_RELATIONSHIPS |
| 17 | EmployeeTerritories | employee (pubs) | Many→One | EmployeeID | — | LOW | INFERRED_JOIN | SQL_RELATIONSHIPS |
| 18 | EmployeeTerritories | Employees (Northwind) | Many→One | EmployeeID | — | LOW | INFERRED_JOIN | SQL_RELATIONSHIPS |
| 19 | Orders | employee (pubs) | Many→One | EmployeeID | — | LOW | INFERRED_JOIN | SQL_RELATIONSHIPS |
| 20 | Territories | Region | Many→One | RegionID | — | LOW | INFERRED_JOIN | SQL_RELATIONSHIPS |

**CONFIRMED (source+constraint evidence):** Relationships 1–8
**INFERRED (view/join analysis):** Relationships 9–15
**LOW — ambiguous or cross-database:** Relationships 16–20

---

### ERD — Northwind Core (Mermaid)

```mermaid
erDiagram
    Customers {
        string CustomerID PK
        string CompanyName
        string ContactName
        string Phone
        string Address
    }
    Orders {
        int OrderID PK
        string CustomerID FK
        int EmployeeID FK
        int ShipVia FK
        datetime OrderDate
        money Freight
    }
    OrderDetails {
        int OrderID FK
        int ProductID FK
        money UnitPrice
        smallint Quantity
        real Discount
    }
    Products {
        int ProductID PK
        string ProductName
        int SupplierID FK
        int CategoryID FK
        money UnitPrice
        bit Discontinued
    }
    Categories {
        int CategoryID PK
        string CategoryName
    }
    Suppliers {
        int SupplierID PK
        string CompanyName
        string Phone
    }
    Employees {
        int EmployeeID PK
        string LastName
        string FirstName
        int ReportsTo FK
    }
    Shippers {
        int ShipperID PK
        string CompanyName
        string Phone
    }
    Region {
        int RegionID
        nchar RegionDescription
    }
    Territories {
        nvarchar TerritoryID
        nchar TerritoryDescription
        int RegionID FK
    }
    EmployeeTerritories {
        int EmployeeID FK
        nvarchar TerritoryID FK
    }

    Customers ||--o{ Orders : "places"
    Orders ||--o{ OrderDetails : "contains"
    Orders }o--|| Employees : "processed by"
    Orders }o--|| Shippers : "shipped via"
    OrderDetails }o--|| Products : "references"
    Products }o--|| Categories : "classified by"
    Products }o--|| Suppliers : "sourced from"
    Employees }o--o| Employees : "reports to"
    Territories }o--|| Region : "belongs to"
    EmployeeTerritories }o--|| Employees : "assigned to"
```

---

### ERD — pubs Core (Mermaid)

```mermaid
erDiagram
    publishers {
        string pub_id PK
        string pub_name
        string city
        string country
    }
    titles {
        tid title_id PK
        string title
        string type
        string pub_id FK
        decimal price
        integer ytd_sales
    }
    authors {
        id au_id PK
        string au_lname
        string au_fname
        string phone
        boolean contract
    }
    titleauthor {
        id au_id FK
        tid title_id FK
        integer au_ord
        integer royaltyper
    }
    stores {
        string stor_id PK
        string stor_name
        string city
    }
    sales {
        string stor_id FK
        string ord_num
        tid title_id FK
        integer qty
        datetime ord_date
    }
    roysched {
        tid title_id FK
        integer lorange
        integer hirange
        integer royalty
    }
    discounts {
        string discounttype
        string stor_id FK
        dec discount
    }
    jobs {
        integer job_id PK
        string job_desc
        integer min_lvl
        integer max_lvl
    }
    employee {
        empid emp_id PK
        string fname
        string lname
        integer job_id FK
        string pub_id FK
        datetime hire_date
    }
    pub_info {
        string pub_id FK
        bytes logo
        string pr_info
    }

    publishers ||--o{ titles : "publishes"
    publishers ||--o{ employee : "employs"
    publishers ||--|| pub_info : "described by"
    titles ||--o{ titleauthor : "authored by"
    authors ||--o{ titleauthor : "writes"
    titles ||--o{ sales : "sold as"
    titles ||--o{ roysched : "royalty schedule"
    stores ||--o{ sales : "sells at"
    stores ||--o{ discounts : "discount applies to"
    jobs ||--o{ employee : "classifies"
```

---

### Cross-Database Ambiguity Flags

**AMBIGUITY 1 — EmployeeTerritories EmployeeID target:**
- Maps LOW confidence to `Employees.EmployeeID` (Northwind) AND `employee.emp_id` (pubs)
- These are different entities in different databases
- Resolution: Northwind.Employees is the correct target based on domain context
- Confidence: LOW — no FK constraint confirmed

**AMBIGUITY 2 — Orders.EmployeeID secondary inferred mapping to pubs.employee:**
- Relationship 19 is almost certainly a false inference
- Orders belongs to Northwind; employee belongs to pubs
- Recommendation: Discard this relationship as cross-database noise

---
