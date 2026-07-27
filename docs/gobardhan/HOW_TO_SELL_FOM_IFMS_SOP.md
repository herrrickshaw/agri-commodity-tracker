# How to Sell Fermented Organic Manure (FOM) — IFMS / MDA Prescription

Step-by-step selling procedure for an **authorised FOM manufacturer**, derived
from the *FOM Module — IFMS* user manual (`FOM_DEMO.pdf`, 29 pp.). This is the
operational counterpart to the bulk-sale authorisation notifications in this
folder: the Gazette notifications say **who** may bulk-sell FOM directly to
farmers; this SOP is **how** each sale is recorded in the Government portal so it
qualifies for the **Market Development Assistance (MDA)** subsidy (₹1,500/MT).

**Portals**
- **New IFMS** — `https://dbtfert.nic.in/login.html` — all capacity, MRP,
  production, sales and verification steps.
- **Old IFMS** — same login URL → **Generate Bills** — final DBT subsidy claim.

Log in with registered credentials. Fields marked `*` in the portal are mandatory.

---

## Phase 0 — One-time / periodic setup (Manufacturer)

Do these before any dispatch. **You cannot dispatch until MRP is entered.**

1. **Installed Capacity & Batch Size** — menu *Installed Capacity, Batch Details*.
   Select Financial Year → Company → Plant → **GO**. Enter installed capacity and
   batch size for each product group. **Batch size must be < 2000 MT.**
2. **Enter MRP** — *Enter/Update Masters → Enter MRP*. Select Company, Plant,
   Product, State, Year, Month; set Dispatch Date, Product, State, MRP Rate →
   **Submit**. *Dispatch is possible only after MRP has been entered for that
   product.*
3. **Authorised Signature Upload** — *Enter/Update Masters → Authorised Signature
   Upload*. Enter From/To Date, Notification Date, Name, Designation, Product,
   "E-Signing for", Type; upload a scanned proof document → **Save**.
4. **Opening Balance** (one-time per plant/product) — *FOM → Opening Balance*.
   Select Plant, Date, Product, Quantity. "Opening Balance At" defaults to your
   company and is locked. This initial stock seeds Available Stock; the screen
   deactivates after this one-time declaration → **Save**.

---

## Phase 1 — Record production (Manufacturer, daily)

5. **FOM Production** — *FOM → FOM Production*. Select Plant, Production Date,
   Product; enter Production Quantity (MT). Use **Add** for multiple products /
   the previous date; **Clear / Delete Last Row** to correct → save.
   Production adds to Available Stock, from which all sales draw.

---

## Phase 2 — Sell (Manufacturer) — pick the channel

Every sale requires a **batch number that matches the uploaded NABL / State-Lab
Quality Certificate**. Sales cannot exceed Available Stock.

### 2A. Sale to a Marketer (via fertiliser marketing company)
*FOM → FOM Manufacturer Sale to Marketer.* Enter Manufacturer Plant, Unit,
Invoice No., Invoice Date, Available Stock, Marketer Company, Marketer Plant,
Product, Quantity (MT), Batch No., Selling Price/MT (₹), and **upload the
NABL/State-Lab QC certificate** → **Save**. (Invoice = the sale invoice to the
marketer; batch must match the QC certificate.)

### 2B. Bulk Sale direct to farmers / retailers
Two sub-forms, same evidence requirements:
- **Stock Transfer of FOM (Self)** — *FOM → Stock Transfer of FOM (Self)*.
- **Sale to Other Retailers** — *FOM → Sale to Other Retailers*.

For either: enter Manufacturer Plant, Invoice No./Date, Available Stock, State,
District, Retailer, Product, Quantity (MT), Batch No.; **upload three proofs** —
(i) NABL/State-Lab QC Certificate, (ii) Weighbridge Receipt, (iii) Photo of the
loaded truck's backside. Then enter transport details: Vehicle No., **Vehicle
Capacity (must be < 40 MT)**, Transport Company, Driver Name, Driver Mobile →
**Save**.

### 2C. Track your sales
*View Reports* → **OB Declared**, **FOM Production**, **View Sale to Marketer**,
**View Bulk Sale to Retailer** (select Company/Plant/Product/date range → GO;
each is **downloadable as Excel**).

---

## Phase 3 — State verification (done by State User, not the manufacturer)

Your sales must clear State verification before they earn subsidy. The State User:
6. **Verification of FOM (Bulk Sale — State Receipts)** — enters Certified
   Quantity and Substandard Quantity.
7. **Enter State Receipts for Marketer FOM Sale** — Certified vs Substandard qty.
8. **Enter Proforma B2-MDA** (separate forms for **Bulk Sale** and **Marketer
   Sale**) — records failed samples and Total Qty **not** eligible for subsidy;
   uploads the Detailed Analysis Report → **Save and Upload**.
9. **Generate Proforma B1-MDA** (Quantity Certificate) — Bulk and Marketer.
10. **Generate FOM Quality Certificate B2-MDA** — Bulk and Marketer.
11. **Upload Proforma B1-MDA & B2-MDA** — Bulk and Marketer.

*Practical implication for the seller:* keep batch numbers, QC certificates and
dispatch records clean — anything that fails the State's B2 sample check is
carved out as "not eligible for subsidy."

---

## Phase 4 — Claim the DBT subsidy (Manufacturer, Old IFMS)

12. Go to **Old IFMS** → **Generate Bills → FOM DBT Bills**.
13. As the **cFOM user**, select Company, Plant, Product Group, Signatory, and
    From/To dates → **GO**.
14. Verify the **State-wise DBT Sales details** per product → **Preview Bill** →
    **eSign and Save Bill** to generate the DBT claim.

---

## Quick rule checklist (the constraints that block a sale/claim)

| Rule | Where |
|---|---|
| Batch size **< 2000 MT** | Installed Capacity setup |
| **MRP must be entered before any dispatch** | Enter MRP |
| Opening Balance is a **one-time** declaration | Opening Balance |
| Sale **batch number must match the NABL QC certificate** | all sale forms |
| Bulk sale requires **QC cert + weighbridge receipt + truck-backside photo** | Bulk / Retailer sale |
| Transport **vehicle capacity < 40 MT** | Bulk / Retailer sale |
| Subsidy needs State **B1 (quantity)** + **B2 (quality)** proformas | State verification |
| Final claim is **eSigned** on the Old IFMS DBT bill | DBT Bill generation |

**Source:** `FOM_DEMO.pdf` — "Fermented Organic Manure (FOM) Module, IFMS"
(Department of Fertilizers / DBT). Sequence and field lists transcribed from the
manual; portal screens may vary slightly by release.
