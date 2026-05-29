# SQL Lineage Report

_Generated: 2026-05-29T09:26:53.459447+00:00_

**Edges:** 49 | **Chains:** 74 | **Tables:** 24 | **Views:** 17 | **Procedures:** 12

## Tables with Multiple Consumers (Potential Redundant Transformations)

- **Products**: consumed by 9 components: PROC:CustOrderHist, PROC:CustOrdersDetail, PROC:Ten Most Expensive Products, VIEW:Alphabetical list of products, VIEW:Current Product List
- **Order Subtotals**: consumed by 5 components: PROC:Employee Sales by Country, PROC:Sales by Year, VIEW:Sales Totals by Amount, VIEW:Summary of Sales by Quarter, VIEW:Summary of Sales by Year
- **Orders**: consumed by 7 components: PROC:CustOrdersOrders, PROC:Sales by Year, VIEW:Invoices, VIEW:Orders Qry, VIEW:Quarterly Orders
- **Order Details**: consumed by 5 components: PROC:SalesByCategory, VIEW:Invoices, VIEW:Order Details Extended, VIEW:Order Subtotals, VIEW:Product Sales for 1997
- **titles**: consumed by 3 components: PROC:reptq1, PROC:reptq2, PROC:reptq3
- **Customers**: consumed by 4 components: VIEW:Customer and Suppliers by City, VIEW:Orders Qry, VIEW:Quarterly Orders, VIEW:Sales Totals by Amount
- **Categories**: consumed by 3 components: VIEW:Alphabetical list of products, VIEW:Products by Category, VIEW:Sales by Category

## Table Impact Summary

| Table | Read By | Written By | FK Parents | FK Children |
|-------|---------|------------|------------|-------------|
| **Products** | 9 | 0 | 2 | 1 |
| **Orders** | 7 | 0 | 3 | 1 |
| **Order Details** | 5 | 0 | 2 | 0 |
| **Customers** | 4 | 0 | 0 | 1 |
| **Categories** | 3 | 0 | 0 | 1 |
| **titles** | 3 | 0 | 0 | 0 |
| **Employees** | 1 | 0 | 1 | 2 |
| **Suppliers** | 1 | 0 | 0 | 1 |
| **Shippers** | 1 | 0 | 0 | 1 |
| **authors** | 1 | 0 | 0 | 0 |
| **titleauthor** | 1 | 0 | 0 | 0 |
| **sales** | 0 | 0 | 0 | 0 |
| **pub_info** | 0 | 0 | 0 | 0 |
| **roysched** | 0 | 0 | 0 | 0 |
| **discounts** | 0 | 0 | 0 | 0 |
| **EmployeeTerritories** | 0 | 0 | 0 | 0 |
| **Territories** | 0 | 0 | 0 | 0 |
| **CustomerCustomerDemo** | 0 | 0 | 0 | 0 |
| **Region** | 0 | 0 | 0 | 0 |
| **stores** | 0 | 0 | 0 | 0 |
| **jobs** | 0 | 0 | 0 | 0 |
| **employee** | 0 | 0 | 0 | 0 |
| **CustomerDemographics** | 0 | 0 | 0 | 0 |
| **publishers** | 0 | 0 | 0 | 0 |

## View → Source Table Dependencies

- `VIEW:VIEW:Customer and Suppliers by City` reads from `Customers`
- `VIEW:VIEW:Customer and Suppliers by City` reads from `Suppliers`
- `VIEW:VIEW:Alphabetical list of products` reads from `Categories`
- `VIEW:VIEW:Alphabetical list of products` reads from `Products`
- `VIEW:VIEW:Current Product List` reads from `Products`
- `VIEW:VIEW:Orders Qry` reads from `Customers`
- `VIEW:VIEW:Orders Qry` reads from `Orders`
- `VIEW:VIEW:Products Above Average Price` reads from `Products`
- `VIEW:VIEW:Products by Category` reads from `Categories`
- `VIEW:VIEW:Products by Category` reads from `Products`
- `VIEW:VIEW:Quarterly Orders` reads from `Customers`
- `VIEW:VIEW:Quarterly Orders` reads from `Orders`
- `VIEW:VIEW:Invoices` reads from `Order Details`
- `VIEW:VIEW:Invoices` reads from `Orders`
- `VIEW:VIEW:Invoices` reads from `Shippers`
- `VIEW:VIEW:Order Details Extended` reads from `Order Details`
- `VIEW:VIEW:Order Details Extended` reads from `Products`
- `VIEW:VIEW:Order Subtotals` reads from `Order Details`
- `VIEW:VIEW:Product Sales for 1997` reads from `Order Details`
- `VIEW:VIEW:Product Sales for 1997` reads from `Products`
- `VIEW:VIEW:Sales by Category` reads from `Categories`
- `VIEW:VIEW:Sales Totals by Amount` reads from `Customers`
- `VIEW:VIEW:Summary of Sales by Quarter` reads from `Orders`
- `VIEW:VIEW:Summary of Sales by Year` reads from `Orders`
- `VIEW:VIEW:titleview` reads from `authors`

