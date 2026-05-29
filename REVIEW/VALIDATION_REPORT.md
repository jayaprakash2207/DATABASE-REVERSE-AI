# Validation Report

_Generated: 2026-05-29T09:32:44.268490+00:00 by M3 Data Architecture Agent (Claude Code)_

### Cross-Layer Validation Summary

**Note:** No application-layer validation exists (zero API endpoints, zero ORM, zero DTO validation detected). All validation is confined to SQL constraints — and those are substantially incomplete.

---

### CRITICAL Validation Issues

| # | Check Type | Entity | Field | Issue | Evidence | Recommendation |
|---|---|---|---|---|---|---|
| V01 | Primary Key Integrity | CustomerCustomerDemo | — | No PK — rows cannot be uniquely identified | SQL_GOVERNANCE CRITICAL | Add PK (CustomerID, CustomerTypeID) |
| V02 | Primary Key Integrity | CustomerDemographics | — | No PK | SQL_GOVERNANCE CRITICAL | Add PK (CustomerTypeID) |
| V03 | Primary Key Integrity | Region | — | No PK | SQL_GOVERNANCE CRITICAL | Add PK (RegionID) |
| V04 | Primary Key Integrity | Territories | — | No PK | SQL_GOVERNANCE CRITICAL | Add PK (TerritoryID) |
| V05 | Primary Key Integrity | EmployeeTerritories | — | No PK | SQL_GOVERNANCE CRITICAL | Add composite PK |
| V06 | Nullable Identity | authors | au_id | PK is nullable — NULL-keyed rows allowed | SQL_GOVERNANCE CRITICAL | ALTER COLUMN NOT NULL |
| V07 | Nullable Identity | titles | title_id | PK is nullable | SQL_GOVERNANCE CRITICAL | ALTER COLUMN NOT NULL |
| V08 | Nullable Identity | sales | title_id | FK/PK column is nullable | SQL_GOVERNANCE CRITICAL | ALTER COLUMN NOT NULL |
| V09 | Nullable Identity | titleauthor | — | No PK on junction table | SQL_GOVERNANCE CRITICAL | Add composite PK |
| V10 | Nullable Identity | roysched | — | No PK | SQL_GOVERNANCE CRITICAL | Add composite PK |
| V11 | Nullable Identity | discounts | — | No PK | SQL_GOVERNANCE CRITICAL | Add PK |
| V12 | Nullable Identity | jobs | job_id | PK is nullable | SQL_GOVERNANCE CRITICAL | ALTER COLUMN NOT NULL |
| V13 | Nullable Identity | employee | emp_id | PK is nullable | SQL_GOVERNANCE CRITICAL | ALTER COLUMN NOT NULL |

---

### WARNING Validation Issues

| # | Check Type | Entity | Field | Issue | Recommendation |
|---|---|---|---|---|---|
| V14 | Audit Trail | All 24 tables | — | No created_at/updated_at/created_by columns on any table | Add audit columns universally |
| V15 | GDPR Retention | Employees, Customers, Suppliers, Shippers, authors, employee | — | No retention expiry or purge date column | Add RetentionExpiresAt to PII tables |
| V16 | PII Governance | Customers | Address, Phone, Fax | Medium-risk PII without encryption marker | Apply column-level encryption or masking |
| V17 | PII Governance | Suppliers | Address, Phone, Fax | Medium-risk PII without encryption marker | Apply column-level encryption or masking |
| V18 | PII Governance | Employees | Address | Medium-risk PII without encryption marker | Apply column-level encryption or masking |
| V19 | Orphan Procedure | dbo.section | — | Stored procedure has no reads or writes — silent no-op | Investigate or drop |
| V20 | Cross-DB Ambiguity | EmployeeTerritories | EmployeeID | FK ambiguity between Employees and employee | Add explicit FK constraint |

---

### NOTE Validation Issues

| # | Check Type | Entity | Issue | Recommendation |
|---|---|---|---|---|
| V21 | Naming Convention | pubs tables vs Northwind tables | snake_case (pubs) vs PascalCase (Northwind) — inconsistent naming conventions across databases | Standardize naming on migration |
| V22 | View Lineage Gap | 17 views | Full DDL not extracted — view logic unverifiable | Extract and analyze view DDL |
| V23 | Write Path | All tables | No stored procedures perform INSERT/UPDATE/DELETE — write path is unobserved | Confirm write mechanism |
| V24 | Soft-Delete Coverage | Products only | Discontinued flag present in Products; no soft-delete pattern on other entities | Evaluate consistent soft-delete strategy |

---

### Validation Coverage Matrix

| Layer | Validation Present | Gap |
|---|---|---|
| SQL NOT NULL constraints | PARTIAL — several PKs nullable | Remediate nullable PKs |
| SQL FK constraints | PARTIAL — 8 confirmed FKs, 12 inferred | Add missing FK constraints |
| SQL CHECK constraints | NOT DETECTED | Add check constraints for range/enum fields |
| SQL DEFAULT values | NOT DETECTED | Add defaults for audit columns |
| Application validation | NOT PRESENT — no app layer | Future: add service-layer validation |
| API validation | NOT PRESENT — no API layer | Future: add DTO validation |

---
