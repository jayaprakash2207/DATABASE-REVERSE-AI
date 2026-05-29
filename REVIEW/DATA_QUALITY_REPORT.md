# Data Quality Report

_Generated: 2026-05-29T09:26:53.456329+00:00_

**Quality Score:** 93/100

**Tables Analyzed:** 24
**Columns Analyzed:** 152
**Tables with Issues:** 28

## Issue Breakdown

| Rule Type | Count | Severity |
|-----------|-------|----------|
| missing_audit_columns | 24 | WARNING |
| pii_medium | 10 | WARNING |
| missing_primary_key | 8 | CRITICAL |
| missing_check_constraint | 7 | NOTE |
| nullable_foreign_key | 6 | NOTE |
| missing_fk_index | 6 | WARNING |
| gdpr_retention_gap | 5 | WARNING |
| nullable_primary_key | 5 | CRITICAL |
| denormalization_risk | 4 | NOTE |

## PII / PCI Summary

| Table | Column | Risk Level |
|-------|--------|------------|
| `Employees` | `Address` | pii_medium |
| `Customers` | `Address` | pii_medium |
| `Customers` | `Phone` | pii_medium |
| `Customers` | `Fax` | pii_medium |
| `Shippers` | `Phone` | pii_medium |
| `Suppliers` | `Address` | pii_medium |
| `Suppliers` | `Phone` | pii_medium |
| `Suppliers` | `Fax` | pii_medium |
| `authors` | `phone` | pii_medium |
| `authors` | `address` | pii_medium |
