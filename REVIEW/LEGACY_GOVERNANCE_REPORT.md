# Legacy Governance Report

_Generated: 2026-05-29T09:32:44.268490+00:00 by M3 Data Architecture Agent (Claude Code)_

### Summary Statistics

| Severity | Count | Source |
|---|---|---|
| CRITICAL | 13 | SQL_GOVERNANCE |
| WARNING | 45 | SQL_GOVERNANCE |
| NOTE | 17 | SQL_GOVERNANCE |
| **TOTAL** | **75** | |

---

### CRITICAL Findings — Full Inventory

**Group A: Missing Primary Keys (8 tables)**

These tables have no PRIMARY KEY constraint — rows cannot be uniquely identified.

| Table | Database | Impact | Remediation Priority |
|---|---|---|---|
| CustomerCustomerDemo | Northwind | Junction table — duplicate rows possible | IMMEDIATE |
| CustomerDemographics | Northwind | Reference data — corrupt classification | IMMEDIATE |
| Region | Northwind | Lookup table — geography integrity | IMMEDIATE |
| Territories | Northwind | Lookup table — territory integrity | IMMEDIATE |
| EmployeeTerritories | Northwind | Junction table — duplicate assignments | IMMEDIATE |
| titleauthor | pubs | Junction table — duplicate authorship | IMMEDIATE |
| roysched | pubs | Royalty schedule — financial integrity | IMMEDIATE |
| discounts | pubs | Discount rules — financial integrity | IMMEDIATE |

**Remediation:** Add composite PKs to junction tables; add simple PKs to lookup tables. No data migration required if no duplicate rows exist — verify with `SELECT ... HAVING COUNT(*) > 1` before adding constraints.

---

**Group B: Nullable Primary Keys (5 tables)**

These tables declare a PRIMARY KEY but the column is nullable — SQL Server permits this in some legacy DDL patterns (particularly in pubs, which is an older Microsoft sample schema).

| Table | Column | Database | Impact |
|---|---|---|---|
| authors | au_id | pubs | NULL rows have no identity — joins produce unexpected results |
| titles | title_id | pubs | Central entity in pubs — NULL title breaks sales and royalty chains |
| sales | title_id | pubs | Composite key component nullable — orphan sales records possible |
| jobs | job_id | pubs | Job classification has no stable identity |
| employee | emp_id | pubs | Employee record has no guaranteed identity |

**Remediation:** `ALTER COLUMN ... NOT NULL` after verifying no NULL values exist. If NULL rows exist, they must be assigned surrogate keys or deleted before constraint can be applied.

---

### WARNING Findings — Grouped

**Group C: PII Columns Without Encryption Markers (13 findings confirmed + additional inferred)**

| Table | Columns | PII Category | Encryption Status |
|---|---|---|---|
| Employees | Address, HomePhone, BirthDate, Photo, Notes | HIGH — personal data | NOT DETECTED |
| Customers | Address, Phone, Fax, ContactName | MEDIUM | NOT DETECTED |
| Suppliers | Address, Phone, Fax, ContactName | MEDIUM | NOT DETECTED |
| Shippers | Phone | MEDIUM | NOT DETECTED |
| authors | address, phone, au_fname, au_lname | MEDIUM | NOT DETECTED |
| employee | fname, lname, hire_date | LOW-MEDIUM | NOT DETECTED |

**Remediation Priority:** HIGH for Employees (DOB, photo — biometric-adjacent), MEDIUM for Customers/Suppliers/authors.

Apply in order:
1. SQL Server Transparent Data Encryption (TDE) — database-level baseline
2. Column-Level Encryption for high-sensitivity fields (DOB, photo)
3. Dynamic Data Masking for application-layer masking of Phone/Address
4. Always Encrypted for fields requiring application-layer key control

---

**Group D: Missing Audit Trail Columns (confirmed across Employees, Categories, Customers, Shippers, Suppliers; pattern applies to all 24 tables)**

No table in either database contains `created_at`, `updated_at`, `created_by`, `updated_by`, or equivalent audit columns.

**Impact:**
- No forensic trail for data changes
- No ability to answer "who changed this record and when"
- GDPR accountability requirement (Article 5(2)) cannot be met
- No optimistic concurrency foundation (rowversion/timestamp absent)

**Remediation:** Add audit columns universally. Consider a CDC (Change Data Capture) trigger pattern or SQL Server CDC feature for retroactive audit coverage on high-risk tables.

---

**Group E: GDPR Retention Gaps (confirmed for Employees, Customers, Suppliers, Shippers)**

No retention/expiry date column exists on any PII-bearing table.

**Impact:**
- GDPR Right to Erasure (Article 17) cannot be systematically honored
- No automated purge process possible without a retention anchor
- Right to Access (Article 15) fulfillment is manual

**Remediation:** Add `RetentionExpiresAt DATETIME2 NULL` to all PII tables. Establish a purge job that soft-deletes or anonymizes records past their retention window.

---

### NOTE Findings

**Group F: Naming Convention Inconsistency**

| Convention | Database | Example |
|---|---|---|
| PascalCase, multi-word | Northwind | `CustomerCustomerDemo`, `OrderDetails`, `EmployeeTerritories` |
| snake_case, abbreviated | pubs | `au_id`, `pub_id`, `stor_address`, `ytd_sales` |

pubs uses the original 1990s Sybase/SQL Server sample schema naming — this is a historical artifact. If both databases are ever unified or accessed by a common application layer, naming normalization will be required.

**Group G: Stub Procedure**

`dbo.section` — confirmed procedure with no reads, no writes, no inputs. Origin and purpose unknown. Low risk if unused; should be investigated and dropped if orphaned.

**Group H: Binary Column Governance**

`Employees.Photo` (IMAGE type) and `pub_info.logo` (BYTES) store binary data with no access control or encryption marker in evidence. IMAGE data type is deprecated in SQL Server — migration to VARBINARY(MAX) or external blob storage is recommended.

---

### Remediation Priority Matrix

| Priority | Finding Group | Effort | Risk Reduction |
|---|---|---|---|
| P1 | Missing PKs on 8 tables | LOW — DDL only | CRITICAL — data integrity |
| P1 | Nullable PKs on 5 tables | LOW — DDL + data check | CRITICAL — identity integrity |
| P2 | GDPR retention columns | MEDIUM — schema + process | HIGH — regulatory |
| P2 | Audit trail columns | MEDIUM — schema + CDC | HIGH — forensic/regulatory |
| P3 | PII encryption/masking | HIGH — security infra | HIGH — data protection |
| P3 | Naming convention normalization | MEDIUM — migration required | MEDIUM — maintainability |
| P4 | Stub procedure cleanup | LOW | LOW — hygiene |
| P4 | IMAGE type migration | MEDIUM | LOW — deprecation |
