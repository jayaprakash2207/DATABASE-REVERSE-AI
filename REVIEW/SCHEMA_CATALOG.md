# Schema Catalog

_Generated: 2026-05-29T09:32:44.268490+00:00 by M3 Data Architecture Agent (Claude Code)_

### Northwind Database Entities

---

**Entity: Orders** | Aggregate: OrderAggregate | Line: L239 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| OrderID | INTEGER | Primary Key | — |
| CustomerID | STRING(5) | FK → Customers | — |
| EmployeeID | INTEGER | FK → Employees | — |
| OrderDate | DATETIME | Business date | — |
| RequiredDate | DATETIME | Business date | — |
| ShippedDate | DATETIME | Business date | NULLABLE — fulfillment gap risk |
| ShipVia | INTEGER | FK → Shippers | — |
| Freight | MONEY | Financial amount | — |
| ShipName | STRING | Denormalized shipping field | Redundancy risk |
| ShipAddress | STRING | Denormalized shipping field | PII (WARNING) |
| ShipCity | STRING | Denormalized shipping field | — |
| ShipRegion | STRING | Denormalized shipping field | — |
| ShipPostalCode | STRING | Denormalized shipping field | — |
| ShipCountry | STRING | Denormalized shipping field | — |

FK Relationships: Orders → Customers (CustomerID, HIGH), Orders → Employees (EmployeeID, HIGH), Orders → Shippers (ShipVia, HIGH)
Navigation: Order Details (reverse, many), inferred Order Subtotals (view)
Audit Columns: MISSING (WARNING)
GDPR: ShipAddress constitutes personal delivery data — retention gap present

---

**Entity: Order Details** | Aggregate: OrderAggregate | Line: L339 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| OrderID | INTEGER | FK → Orders | Composite PK candidate |
| ProductID | INTEGER | FK → Products | Composite PK candidate |
| UnitPrice | MONEY | Price snapshot | Denormalized from Products |
| Quantity | SMALLINT | Order line quantity | — |
| Discount | REAL | Discount rate | — |

FK Relationships: Order Details → Orders (OrderID, HIGH), Order Details → Products (ProductID, HIGH)
Aggregate: Embedded within OrderAggregate (no independent lifecycle)
Note: UnitPrice is a price-at-time-of-order snapshot — intentionally denormalized. INFERRED.

---

**Entity: Customers** | Aggregate: CustomerAggregate | Line: L178 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| CustomerID | STRING(5) | Primary Key | — |
| CompanyName | STRING | Business identity | — |
| ContactName | STRING | PII — person name | WARNING |
| ContactTitle | STRING | PII — contact role | WARNING |
| Address | STRING | PII medium-risk | WARNING |
| City | STRING | Location | — |
| Region | STRING | Location | — |
| PostalCode | STRING | Location | — |
| Country | STRING | Location | — |
| Phone | STRING | PII medium-risk | WARNING |
| Fax | STRING | PII medium-risk | WARNING |

FK Relationships: FK target from Orders (CustomerID, HIGH)
Audit Columns: MISSING (WARNING)
GDPR: Address, Phone, Fax, ContactName — retention gap confirmed (WARNING)

---

**Entity: Employees** | Aggregate: EmployeeAggregate | Line: L127 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| EmployeeID | INTEGER | Primary Key | — |
| LastName | STRING | PII — name | — |
| FirstName | STRING | PII — name | — |
| Title | STRING | HR role | — |
| TitleOfCourtesy | STRING | PII — salutation | — |
| BirthDate | DATETIME | PII high-risk — DOB | — |
| HireDate | DATETIME | HR date | — |
| Address | STRING | PII medium-risk | WARNING |
| City | STRING | Location | — |
| Region | STRING | Location | — |
| PostalCode | STRING | Location | — |
| Country | STRING | Location | — |
| HomePhone | STRING | PII — contact | — |
| Extension | STRING | Internal extension | — |
| Photo | IMAGE | Binary — employee photo | PII (biometric-adjacent) |
| Notes | NTEXT | Freetext — HR notes | PII risk — unstructured |
| ReportsTo | INTEGER | Self-FK (hierarchy) | — |
| PhotoPath | STRING | File path | — |

FK Relationships: Self-reference Employees → Employees via ReportsTo (HIGH, constraint=FK_Employees_Employees)
Audit Columns: MISSING (WARNING)
GDPR: BirthDate, Address, HomePhone, Photo, Notes — HIGH GDPR exposure; retention gap confirmed

---

**Entity: Products** | Aggregate: ProductAggregate | Line: L295 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| ProductID | INTEGER | Primary Key | — |
| ProductName | STRING | Product identity | — |
| SupplierID | INTEGER | FK → Suppliers | — |
| CategoryID | INTEGER | FK → Categories | — |
| QuantityPerUnit | STRING | Packaging descriptor | — |
| UnitPrice | MONEY | Current price | — |
| UnitsInStock | SMALLINT | Inventory level | — |
| UnitsOnOrder | SMALLINT | Pending inventory | — |
| ReorderLevel | SMALLINT | Inventory threshold | — |
| Discontinued | BIT | Soft-delete flag | — |

FK Relationships: Products → Suppliers (SupplierID, HIGH), Products → Categories (CategoryID, HIGH)
Note: Discontinued=BIT is a soft-delete pattern. CONFIRMED.

---

**Entity: Categories** | Aggregate: CategoriesAggregate | Line: L164 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| CategoryID | INTEGER | Primary Key | — |
| CategoryName | STRING | Category label | — |
| Description | NTEXT | Freetext description | — |
| Picture | IMAGE | Binary image | — |

Audit Columns: MISSING (WARNING)

---

**Entity: Suppliers** | Aggregate: SupplierAggregate | Line: L215 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| SupplierID | INTEGER | Primary Key | — |
| CompanyName | STRING | Business identity | — |
| ContactName | STRING | PII — person name | WARNING |
| ContactTitle | STRING | PII — role | WARNING |
| Address | STRING | PII medium-risk | WARNING |
| City | STRING | Location | — |
| Region | STRING | Location | — |
| PostalCode | STRING | Location | — |
| Country | STRING | Location | — |
| Phone | STRING | PII medium-risk | WARNING |
| Fax | STRING | PII medium-risk | WARNING |
| HomePage | NTEXT | Supplier URL | — |

Audit Columns: MISSING (WARNING)
GDPR: Address, Phone, Fax, ContactName — retention gap confirmed

---

**Entity: Shippers** | Aggregate: ShippersAggregate | Line: L205 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| ShipperID | INTEGER | Primary Key | — |
| CompanyName | STRING | Carrier identity | — |
| Phone | STRING | PII medium-risk | WARNING |

Audit Columns: MISSING (WARNING)
GDPR: Phone — retention gap confirmed

---

**Entity: CustomerCustomerDemo** | Aggregate: CustomerAggregate | Line: L9116 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| CustomerID | STRING | FK → Customers | Composite PK — NOT ENFORCED |
| CustomerTypeID | NCHAR | FK → CustomerDemographics | Composite PK — NOT ENFORCED |

PRIMARY KEY: MISSING (CRITICAL)
Note: Junction table with no enforced PK. Data integrity risk.

---

**Entity: CustomerDemographics** | Aggregate: CustomerAggregate | Line: L9122 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| CustomerTypeID | NCHAR | Primary Key — NOT ENFORCED | CRITICAL — missing PK |
| CustomerDesc | NTEXT | Demographic description | — |

PRIMARY KEY: MISSING (CRITICAL)

---

**Entity: Region** | Aggregate: RegionAggregate | Line: L9128 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| RegionID | INTEGER | Primary Key — NOT ENFORCED | CRITICAL — missing PK |
| RegionDescription | NCHAR | Region label | — |

PRIMARY KEY: MISSING (CRITICAL)

---

**Entity: Territories** | Aggregate: TerritoriesAggregate | Line: L9134 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| TerritoryID | NVARCHAR | Primary Key — NOT ENFORCED | CRITICAL — missing PK |
| TerritoryDescription | NCHAR | Territory label | — |
| RegionID | INTEGER | FK → Region (LOW confidence) | — |

PRIMARY KEY: MISSING (CRITICAL)

---

**Entity: EmployeeTerritories** | Aggregate: EmployeeAggregate | Line: L9141 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| EmployeeID | INTEGER | FK → Employees (LOW) / employee (LOW) | Composite PK — NOT ENFORCED |
| TerritoryID | NVARCHAR | FK → Territories (implied) | Composite PK — NOT ENFORCED |

PRIMARY KEY: MISSING (CRITICAL)
Cross-domain ambiguity: EmployeeID maps to BOTH Northwind.Employees AND pubs.employee at LOW confidence.

---

### pubs Database Entities

---

**Entity: authors** | Aggregate: AuthorsAggregate | Line: L67 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| au_id | ID | Primary Key — NULLABLE | CRITICAL |
| au_lname | STRING | PII — surname | — |
| au_fname | STRING | PII — first name | — |
| phone | STRING | PII — contact | — |
| address | STRING | PII medium-risk | — |
| city | STRING | Location | — |
| state | STRING | Location | — |
| zip | STRING | Location | — |
| contract | BOOLEAN | Contract status flag | — |

Nullable PK: au_id is nullable — CRITICAL governance violation.
Note: Naming convention uses snake_case (pubs) vs PascalCase (Northwind) — naming inconsistency across databases.

---

**Entity: publishers** | Aggregate: PublishersAggregate | Line: L95 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| pub_id | STRING | Primary Key | — |
| pub_name | STRING | Publisher name | — |
| city | STRING | Location | — |
| state | STRING | Location | — |
| country | STRING | Location | — |

---

**Entity: titles** | Aggregate: TitlesAggregate | Line: L115 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| title_id | TID (constrained) | Primary Key — NULLABLE | CRITICAL |
| title | STRING | Book title | — |
| type | STRING | Genre/type classification | — |
| pub_id | STRING | FK → publishers | — |
| price | DECIMAL | Retail price | — |
| advance | DECIMAL | Author advance | Financial data |
| royalty | INTEGER | Royalty percentage | Financial data |
| ytd_sales | INTEGER | Year-to-date sales | — |
| notes | STRING | Freetext notes | — |
| pubdate | DATETIME | Publication date | — |

Nullable PK: title_id is nullable — CRITICAL governance violation.

---

**Entity: titleauthor** | Aggregate: TitleauthorAggregate | Line: L144 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| au_id | ID | FK → authors | Composite PK — NOT ENFORCED |
| title_id | TID | FK → titles | Composite PK — NOT ENFORCED |
| au_ord | INTEGER | Author ordering | — |
| royaltyper | INTEGER | Per-author royalty share | Financial data |

PRIMARY KEY: MISSING (CRITICAL)

---

**Entity: stores** | Aggregate: StoresAggregate | Line: L163 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| stor_id | STRING | Primary Key | — |
| stor_name | STRING | Store name | — |
| stor_address | STRING | PII medium-risk | — |
| city | STRING | Location | — |
| state | STRING | Location | — |
| zip | STRING | Location | — |

---

**Entity: sales** | Aggregate: SalesAggregate | Line: L178 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| stor_id | STRING | FK → stores | Composite PK candidate |
| ord_num | STRING | Order number | Composite PK candidate |
| ord_date | DATETIME | Order date | — |
| qty | INTEGER | Quantity sold | — |
| payterms | STRING | Payment terms | Financial data |
| title_id | TID | FK → titles — NULLABLE PK | CRITICAL |

Nullable FK: title_id is nullable (used as part of composite PK) — CRITICAL data integrity risk.

---

**Entity: roysched** | Aggregate: RoyschedAggregate | Line: L199 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| title_id | TID | FK → titles | Composite key — NOT ENFORCED |
| lorange | INTEGER | Lower royalty bound | Financial data |
| hirange | INTEGER | Upper royalty bound | Financial data |
| royalty | INTEGER | Royalty rate | Financial data |

PRIMARY KEY: MISSING (CRITICAL)

---

**Entity: discounts** | Aggregate: DiscountsAggregate | Line: L212 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| discounttype | STRING | Discount classification | Composite key — NOT ENFORCED |
| stor_id | STRING | FK → stores (nullable) | — |
| lowqty | INTEGER | Lower qty threshold | — |
| highqty | INTEGER | Upper qty threshold | — |
| discount | DECIMAL | Discount rate | Financial data |

PRIMARY KEY: MISSING (CRITICAL)

---

**Entity: jobs** | Aggregate: JobsAggregate | Line: L227 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| job_id | INTEGER | Primary Key — NULLABLE | CRITICAL |
| job_desc | STRING | Job description | — |
| min_lvl | INTEGER | Minimum job level | — |
| max_lvl | INTEGER | Maximum job level | — |

Nullable PK: job_id is nullable — CRITICAL governance violation.

---

**Entity: pub_info** | Aggregate: Pub_infoAggregate | Line: L248 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| pub_id | STRING | FK → publishers | — |
| logo | BYTES (image) | Binary logo asset | — |
| pr_info | STRING | PR/marketing text | — |

Note: pub_info appears to be a 1:1 extension of publishers. INFERRED — no FK constraint confirmed in evidence.

---

**Entity: employee** (pubs) | Aggregate: EmployeeAggregate | Line: L262 | Confidence: HIGH

| Field | Normalized Type | Role | Governance |
|---|---|---|---|
| emp_id | EMPID (constrained) | Primary Key — NULLABLE | CRITICAL |
| fname | STRING | PII — first name | — |
| minit | STRING | PII — middle initial | — |
| lname | STRING | PII — surname | — |
| job_id | INTEGER | FK → jobs | — |
| job_lvl | INTEGER | Job level | — |
| pub_id | STRING | FK → publishers | — |
| hire_date | DATETIME | HR date | — |

Nullable PK: emp_id is nullable — CRITICAL governance violation.
Domain collision: `employee` (pubs) vs `Employees` (Northwind) — two separate entities sharing the EmployeeAggregate label. CONFIRMED naming conflict.

---
