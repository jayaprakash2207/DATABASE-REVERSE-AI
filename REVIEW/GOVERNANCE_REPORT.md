# Governance Report

_Generated: 2026-05-29T09:32:44.268490+00:00 by M3 Data Architecture Agent (Claude Code)_

### CRITICAL Findings (13 total)

| Entity | Field | Rule | Finding | Recommendation |
|---|---|---|---|---|
| CustomerCustomerDemo | — | missing_primary_key | Table has no PRIMARY KEY constraint | Add composite PK (CustomerID, CustomerTypeID) |
| CustomerDemographics | — | missing_primary_key | Table has no PRIMARY KEY constraint | Add PK on CustomerTypeID |
| Region | — | missing_primary_key | Table has no PRIMARY KEY constraint | Add PK on RegionID |
| Territories | — | missing_primary_key | Table has no PRIMARY KEY constraint | Add PK on TerritoryID |
| EmployeeTerritories | — | missing_primary_key | Table has no PRIMARY KEY constraint | Add composite PK (EmployeeID, TerritoryID) |
| authors | au_id | nullable_primary_key | PK column is nullable — rows can exist with NULL identity | ALTER COLUMN au_id NOT NULL |
| titles | title_id | nullable_primary_key | PK column is nullable | ALTER COLUMN title_id NOT NULL |
| sales | title_id | nullable_primary_key | PK/FK column is nullable | ALTER COLUMN title_id NOT NULL |
| titleauthor | — | missing_primary_key | Junction table has no PRIMARY KEY | Add composite PK (au_id, title_id) |
| roysched | — | missing_primary_key | No PRIMARY KEY on royalty schedule | Add composite PK (title_id, lorange) |
| discounts | — | missing_primary_key | No PRIMARY KEY on discounts | Add composite PK or surrogate key |
| jobs | job_id | nullable_primary_key | PK column is nullable | ALTER COLUMN job_id NOT NULL |
| employee | emp_id | nullable_primary_key | PK column is nullable | ALTER COLUMN emp_id NOT NULL |

All 13 CRITICAL findings confirmed. Source: SQL_GOVERNANCE evidence.

---

### WARNING Findings — PII Exposure (selected; full list in SQL_GOVERNANCE)

| Entity | Field | Rule | PII Category | Recommendation |
|---|---|---|---|---|
| Employees | Address | pii_medium | Contact/identity — employee home address | Encrypt at rest; apply column-level masking |
| Customers | Address | pii_medium | Contact/identity | Encrypt at rest |
| Customers | Phone | pii_medium | Contact | Encrypt at rest |
| Customers | Fax | pii_medium | Contact | Encrypt at rest |
| Suppliers | Address | pii_medium | Contact/identity | Encrypt at rest |
| Suppliers | Phone | pii_medium | Contact | Encrypt at rest |
| Suppliers | Fax | pii_medium | Contact | Encrypt at rest |
| Shippers | Phone | pii_medium | Contact | Encrypt at rest |

Additional PII fields not in WARNING list but architecturally significant:
- Employees.BirthDate — HIGH-risk PII (date of birth); no masking detected. INFERRED risk.
- Employees.HomePhone — personal contact information. INFERRED risk.
- Employees.Photo — biometric-adjacent binary data. INFERRED risk.
- authors.phone, authors.address, authors.au_fname, authors.au_lname — full PII profile with no encryption marker.

---

### WARNING Findings — Missing Audit Columns

| Entity | Database | Finding |
|---|---|---|
| Employees | Northwind | No created_at, updated_at, or created_by columns |
| Categories | Northwind | No audit columns |
| Customers | Northwind | No audit columns |
| Shippers | Northwind | No audit columns |
| Suppliers | Northwind | No audit columns |

Source: SQL_GOVERNANCE. Pattern applies across all 24 tables — no table in either database has confirmed audit trail columns.

---

### WARNING Findings — GDPR Retention Gaps

| Entity | Database | Personal Data Present | Retention Column | Status |
|---|---|---|---|---|
| Employees | Northwind | YES — name, DOB, address, photo | NOT PRESENT | GDPR gap |
| Customers | Northwind | YES — name, address, phone | NOT PRESENT | GDPR gap |
| Suppliers | Northwind | YES — contact name, address | NOT PRESENT | GDPR gap |
| Shippers | Northwind | YES — phone | NOT PRESENT | GDPR gap |
| authors | pubs | YES — full name, address, phone | NOT PRESENT | GDPR gap |
| employee | pubs | YES — full name, hire date | NOT PRESENT | GDPR gap |

---

### Governance Coverage Summary

| Category | Entities Affected | Status |
|---|---|---|
| Missing PK | 8 tables | CRITICAL — no row-level identity guarantees |
| Nullable PK | 5 tables | CRITICAL — identity integrity broken |
| PII unencrypted | 6+ tables | WARNING — no encryption marker detected |
| Missing audit trail | All 24 tables | WARNING — no change history possible |
| GDPR retention gap | 6+ tables | WARNING — no purge/retention mechanism |
| Soft-delete pattern | Products (Discontinued flag) | NOTE — partial, only 1 table |
| PCI-DSS fields | None detected | NOTE — no payment card fields found |

---
