# Redundancy Analysis

_Generated: 2026-05-29T09:32:44.268490+00:00 by M3 Data Architecture Agent (Claude Code)_

### Finding R1 — Employee Entity Duplication Across Databases

**CONFIRMED** — Source: entity extraction + relationship evidence

Two separate `employee`-concept entities exist:

| Attribute | Northwind.Employees | pubs.employee |
|---|---|---|
| Primary Key | EmployeeID (int) | emp_id (empid, nullable) |
| Name fields | FirstName, LastName | fname, minit, lname |
| Hire Date | HireDate | hire_date |
| Hierarchy | ReportsTo (self-FK) | None |
| Job/Role | Title, TitleOfCourtesy | job_id → jobs |
| Publisher FK | None | pub_id → publishers |
| PII Scope | Full (address, photo, DOB) | Minimal |
| Domain | Commerce/ERP | Publishing |

**Assessment:** These are distinct business entities in separate domains — they should NOT be merged. However, they both carry the label `EmployeeAggregate` in extraction output, creating a semantic collision risk. CONFIRMED naming collision.

**Risk:** EmployeeTerritories (Northwind) has LOW-confidence inferred joins to BOTH entities simultaneously — this suggests an FK ambiguity that could lead to incorrect cross-database joins at the application layer when one is introduced.

**Recommendation:** Rename pubs.employee to `pubs.PublishingEmployee` or scope aggregate labels by database prefix.

---

### Finding R2 — Contact Address Pattern Repeated Across 4 Entities

**CONFIRMED** — Source: schema catalog

The following address block appears redundantly across Northwind entities:

| Field | Customers | Employees | Suppliers | Orders (Ship*) |
|---|---|---|---|---|
| Address | YES | YES | YES | ShipAddress |
| City | YES | YES | YES | ShipCity |
| Region | YES | YES | YES | ShipRegion |
| PostalCode | YES | YES | YES | ShipPostalCode |
| Country | YES | YES | YES | ShipCountry |
| Phone | YES | YES (HomePhone) | YES | — |
| Fax | YES | — | YES | — |

**Overlap: ~5 fields across 4 entities (20+ column repetitions)**

**Risk:** Inconsistent PII governance applied to the same logical concept. Any encryption or masking policy must be replicated across all occurrences.

**Recommendation:** Define a canonical `AddressValue` value object. Apply uniform governance rules once. In current SQL-only architecture, enforce via column-level security policies applied uniformly.

---

### Finding R3 — Orders.Ship* Fields Are Denormalized Customer Address Snapshot

**INFERRED** — Source: schema analysis

Orders contains ShipName, ShipAddress, ShipCity, ShipRegion, ShipPostalCode, ShipCountry — these mirror the Customer address fields but represent the shipping destination at time of order.

**Assessment:** This is intentional historical denormalization — the shipping address at order time may differ from the current customer address. This is a common commerce pattern.

**Risk:** LOW — pattern is architecturally valid. However, governance policies (PII masking, GDPR retention) must cover Orders.Ship* fields in addition to Customers.*. These are currently ungoverned.

---

### Finding R4 — ContactName / ContactTitle Pattern in Customers and Suppliers

**CONFIRMED** — Source: schema catalog

| Field | Customers | Suppliers |
|---|---|---|
| ContactName | YES | YES |
| ContactTitle | YES | YES |
| CompanyName | YES | YES |

**Overlap: 3 fields, 2 entities**

**Assessment:** Both Customers and Suppliers represent external business parties with contact persons. The shared contact schema suggests a common `BusinessParty` or `ExternalContact` abstraction could unify governance rules.

**Risk:** MEDIUM — PII governance must be applied identically to both. If encryption or masking is added to one but not the other, a governance gap opens.

---

### Finding R5 — Royalty Percentage Stored in Two Locations (pubs)

**CONFIRMED** — Source: schema catalog

| Table | Field | Meaning |
|---|---|---|
| titleauthor | royaltyper | Per-author royalty share percentage |
| roysched | royalty | Royalty rate per sales range |

**Assessment:** These serve different purposes (author allocation vs. rate schedule) but both store royalty percentage values. Not a true duplicate — they are related concepts in a royalty calculation chain. INFERRED dependency.

---

### Redundancy Summary

| ID | Finding | Type | Risk | Recommendation |
|---|---|---|---|---|
| R1 | Employees vs employee — dual entity | Cross-database naming collision | HIGH | Prefix aggregate labels by database scope |
| R2 | Address block repeated 4x | Structural field duplication | MEDIUM | Canonical AddressValue value object |
| R3 | Orders.Ship* mirrors Customer address | Historical denormalization | LOW | Add PII governance to Ship* fields |
| R4 | ContactName/ContactTitle in Customers+Suppliers | Structural duplication | MEDIUM | Canonical BusinessParty abstraction |
| R5 | Royalty percentage in titleauthor + roysched | Related concepts, not duplicate | LOW | Document relationship explicitly |

---
