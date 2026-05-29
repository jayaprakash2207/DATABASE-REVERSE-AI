# Modernization Recommendations

_Generated: 2026-05-29T09:32:44.268490+00:00 by M3 Data Architecture Agent (Claude Code)_

### P1 — CRITICAL: Remediate Missing and Nullable Primary Keys

**Current State:** 8 tables lack primary keys entirely. 5 tables have nullable primary key columns. These are Northwind (CustomerCustomerDemo, CustomerDemographics, Region, Territories, EmployeeTerritories) and pubs (titleauthor, roysched, discounts, authors.au_id, titles.title_id, sales.title_id, jobs.job_id, employee.emp_id).

**Risk:** Tables without PKs cannot guarantee row uniqueness. Nullable PKs allow NULL-identity rows to exist, breaking all referential integrity that depends on them. Any future ORM adoption, API layer, or application modernization will fail or produce unpredictable behavior against these tables.

**Specific Remediation:**

```sql
-- Northwind: Add composite PKs to junction/lookup tables
ALTER TABLE CustomerCustomerDemo ADD CONSTRAINT PK_CustomerCustomerDemo 
    PRIMARY KEY (CustomerID, CustomerTypeID);
ALTER TABLE CustomerDemographics ADD CONSTRAINT PK_CustomerDemographics 
    PRIMARY KEY (CustomerTypeID);
ALTER TABLE Region ADD CONSTRAINT PK_Region PRIMARY KEY (RegionID);
ALTER TABLE Territories ADD CONSTRAINT PK_Territories PRIMARY KEY (TerritoryID);
ALTER TABLE EmployeeTerritories ADD CONSTRAINT PK_EmployeeTerritories 
    PRIMARY KEY (EmployeeID, TerritoryID);

-- pubs: Add composite PKs and enforce NOT NULL on PK columns
ALTER TABLE titleauthor ADD CONSTRAINT PK_titleauthor 
    PRIMARY KEY (au_id, title_id);
ALTER TABLE roysched ADD CONSTRAINT PK_roysched 
    PRIMARY KEY (title_id, lorange);
ALTER TABLE authors ALTER COLUMN au_id id NOT NULL;
ALTER TABLE titles ALTER COLUMN title_id tid NOT NULL;
ALTER TABLE jobs ALTER COLUMN job_id int NOT NULL;
ALTER TABLE employee ALTER COLUMN emp_id empid NOT NULL;
```

**Priority:** P1 — implement before any application or modernization work begins.

---

### P2 — CRITICAL: Implement GDPR-Compliant Data Governance

**Current State:** Multiple tables store PII (Employees, Customers, Suppliers, Shippers, authors, employee) with no audit trail columns, no retention/purge date columns, and no confirmed encryption at rest.

**Risk:** Non-compliance with GDPR Article 5 (storage limitation) and Article 25 (data protection by design). In the event of a breach, no forensic trail exists (no audit columns). Right-to-erasure requests cannot be systematically honored without a retention timestamp.

**Specific Remediation:**

```sql
-- Add audit columns to all PII-bearing tables (example for Customers)
ALTER TABLE Customers ADD 
    CreatedAt DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    UpdatedAt DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
    CreatedBy NVARCHAR(100) NOT NULL DEFAULT SYSTEM_USER,
    RetentionExpiresAt DATETIME2 NULL;

-- Apply column-level encryption or masking for PII fields
-- (SQL Server Dynamic Data Masking as interim measure)
ALTER TABLE Customers 
    ALTER COLUMN Phone ADD MASKED WITH (FUNCTION = 'partial(0,"XXX-XXX-",4)');
ALTER TABLE Customers 
    ALTER COLUMN Address ADD MASKED WITH (FUNCTION = 'default()');

-- Repeat pattern for Employees, Suppliers, Shippers, authors, employee tables
```

**Priority:** P2 — legal and regulatory exposure.

---

### P3 — HIGH: Resolve Employee Entity Naming Collision

**Current State:** `Northwind.Employees` and `pubs.employee` both exist and both receive the aggregate label `EmployeeAggregate`. EmployeeTerritories has LOW-confidence inferred joins to both simultaneously.

**Risk:** Any application layer built over these databases will face ambiguous query routing. If EmployeeTerritories is ever queried with a JOIN by employee concept, the wrong entity may be joined.

**Specific Remediation:**

1. Rename pubs.employee to `pubs.publishing_employee` (or alias via view: `CREATE VIEW pubs.PublishingEmployee AS SELECT * FROM pubs.employee`)
2. Add explicit FK constraint from `Northwind.EmployeeTerritories.EmployeeID` → `Northwind.Employees.EmployeeID` (eliminate the LOW-confidence ambiguity)
3. Discard the inferred `Orders.EmployeeID → pubs.employee` relationship as a false positive

---

### P4 — MEDIUM: Add Application Tier and API Layer

**Current State:** Zero application code. Business logic lives entirely in 12 stored procedures and 17 views. All are read-only. No write procedures detected.

**Risk:** The system has no observable write path. Either writes occur via raw table INSERT/UPDATE/DELETE (no governance, no audit), or there is an application layer not captured in the analysis. In either case, the architecture is fragile: no validation layer, no domain events, no integration contracts.

**Modernization Path:**

1. Introduce a read model API (REST or GraphQL) that wraps stored procedures as query endpoints — this formalizes the existing implicit read interface
2. Introduce a command/write model that routes mutations through validated service objects
3. Gradually migrate stored procedure logic into service-layer business logic with testable units
4. Introduce domain events for order placement, product updates, and customer changes to enable future event-driven integration

**Priority:** P4 — architectural modernization. No immediate data integrity impact but required for any scale or integration work.

---

### P5 — LOW: Normalize Contact Address into Value Object

**Current State:** Address block (Address, City, Region, PostalCode, Country) repeated across Customers, Employees, Suppliers, and denormalized into Orders (Ship* fields).

**Risk:** PII governance policies must be applied to 4+ locations independently. Any policy change (encryption, masking, GDPR purge) must be replicated manually.

**Specific Remediation (if migrating to ORM/application layer):**

Define a canonical `PostalAddress` value object:
```
PostalAddress {
  street: string
  city: string
  region: string
  postalCode: string
  country: string
}
```

Apply a single governance ruleset to `PostalAddress` rather than individual columns.

In SQL, this can be partially addressed using column-level security policies grouped by a sensitivity label.

---
