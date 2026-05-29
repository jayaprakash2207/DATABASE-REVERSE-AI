# Canonical Model

_Generated: 2026-05-29T09:32:44.268490+00:00 by M3 Data Architecture Agent (Claude Code)_

The canonical model defines the authoritative business concept for each major domain entity, independent of its specific SQL representation.

---

### Canonical Concept: Customer

| Attribute | Value |
|---|---|
| Canonical Name | Customer |
| Source Entity | Northwind.Customers |
| Authoritative Fields | CustomerID, CompanyName, ContactName, ContactTitle, Address (canonical), Phone, Fax |
| PII Fields | ContactName, Address, Phone, Fax |
| Invariants | CustomerID is non-null, unique 5-char identifier; CompanyName must not be null |
| Lifecycle | Created on first order; no soft-delete detected; GDPR retention undefined |
| Relationships | Places Orders; has CustomerDemographic classifications |
| Governance | PII — requires encryption, masking, retention policy |
| Confidence | HIGH |

---

### Canonical Concept: Order

| Attribute | Value |
|---|---|
| Canonical Name | Order |
| Source Entity | Northwind.Orders + Northwind.Order Details |
| Authoritative Fields | OrderID, CustomerID, EmployeeID, OrderDate, ShipVia, Freight, line items (OrderDetails) |
| Financial Fields | Freight, UnitPrice, Discount |
| Invariants | OrderID is unique integer; CustomerID must reference a valid Customer; OrderDate not null |
| Lifecycle | Created → Shipped (ShippedDate); no cancellation state detected |
| Relationships | Contains Order Details (aggregate child); placed by Customer; processed by Employee; shipped via Shipper |
| Governance | Freight is financial data; ShipAddress constitutes PII snapshot |
| Confidence | HIGH |

---

### Canonical Concept: Product

| Attribute | Value |
|---|---|
| Canonical Name | Product |
| Source Entity | Northwind.Products |
| Authoritative Fields | ProductID, ProductName, CategoryID, SupplierID, UnitPrice, UnitsInStock, Discontinued |
| Invariants | ProductID unique; ProductName not null; Discontinued=false for active products |
| Lifecycle | Active (Discontinued=0) or retired (Discontinued=1) — soft-delete pattern confirmed |
| Relationships | Classified by Category; supplied by Supplier; referenced in Order Details |
| Governance | UnitPrice is financial data; Discontinued flag governs availability |
| Confidence | HIGH |

---

### Canonical Concept: Employee (Commerce)

| Attribute | Value |
|---|---|
| Canonical Name | Employee |
| Source Entity | Northwind.Employees |
| Authoritative Fields | EmployeeID, FirstName, LastName, Title, HireDate, ReportsTo |
| PII Fields | BirthDate, Address, HomePhone, Photo, Notes |
| Invariants | EmployeeID unique integer; ReportsTo self-reference for hierarchy |
| Lifecycle | Hired (HireDate); no termination date detected — GDPR gap |
| Relationships | Processes Orders; has Territories (via EmployeeTerritories); reports to manager (self-FK) |
| Governance | HIGH PII exposure — DOB, photo, home address; no retention column |
| Confidence | HIGH |

---

### Canonical Concept: Title (Publication)

| Attribute | Value |
|---|---|
| Canonical Name | PublishedTitle |
| Source Entity | pubs.titles |
| Authoritative Fields | title_id, title, type, pub_id, price, ytd_sales, pubdate |
| Financial Fields | price, advance, royalty |
| Invariants | title_id should be non-null and unique (CURRENTLY VIOLATED — nullable PK) |
| Lifecycle | Published (pubdate); sold in stores; royalties calculated from roysched |
| Relationships | Published by Publisher; authored by Authors (via titleauthor); sold in sales; royalty-scheduled in roysched |
| Governance | Financial data (advance, royalty) — requires access control; nullable PK is integrity violation |
| Confidence | HIGH |

---

### Canonical Concept: Author

| Attribute | Value |
|---|---|
| Canonical Name | Author |
| Source Entity | pubs.authors |
| Authoritative Fields | au_id, au_lname, au_fname, phone, address, contract |
| PII Fields | au_lname, au_fname, phone, address |
| Invariants | au_id should be non-null unique (CURRENTLY VIOLATED — nullable PK) |
| Lifecycle | Contracted (contract=true) or not; no termination or retention date |
| Relationships | Writes Titles (via titleauthor); receives royalties |
| Governance | PII — full name, address, phone unencrypted; GDPR retention undefined |
| Confidence | HIGH |

---

### Canonical Concept: Publisher

| Attribute | Value |
|---|---|
| Canonical Name | Publisher |
| Source Entity | pubs.publishers |
| Authoritative Fields | pub_id, pub_name, city, country |
| Invariants | pub_id unique string |
| Lifecycle | No lifecycle columns detected |
| Relationships | Publishes Titles; employs employees; described by pub_info (1:1 extension) |
| Governance | Low PII risk (organizational entity) |
| Confidence | HIGH |

---
