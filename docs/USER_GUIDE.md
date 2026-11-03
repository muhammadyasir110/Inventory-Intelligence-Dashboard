# User guide (2.7.x)

For planners, buyers, warehouse staff and managers of an FMCG distributor. Your administrator gives you a
username and role.

| Role | Can |
|---|---|
| Administrator | everything in the organisation, including users, settings and plan visibility |
| Inventory manager | import data, edit SKUs, run optimisation, create draft purchase orders, receive goods |
| Procurement manager | create, approve (not their own), order and cancel purchase orders, manage suppliers |
| Warehouse manager | create warehouses, receive goods |
| Executive | read-only |

## 1. Getting data in (Data Upload)

1. **Inventory snapshot** - a CSV or Excel file, one row per SKU (per warehouse). Useful columns: `sku`,
   `product_name`, `category`, `warehouse`, `supplier`, `quantity_on_hand`, `unit_cost`, `unit_price`,
   `daily_avg_demand`, `lead_time_days`, `reorder_point`, `safety_stock`, `max_stock_level`, `expiry_date`.
   Units: quantities in units, demand in **units per day**, lead time in **days**, money in your
   organisation currency (PKR by default).
2. Choose the file. The system **checks it first and saves nothing**: you see the number of rows, SKUs,
   warehouses, the stock value, any errors with spreadsheet row numbers, warnings, and which fields would be
   *derived by rule* because they are missing. Press **Import** to save, or **Cancel**.
3. **Demand history** - a file with `sku`, `date`, `quantity` (optional `channel`), one row per SKU per
   day. Import the inventory first. Future dates, unknown SKUs, negative quantities and duplicate rows are
   rejected with row numbers; if any row is wrong nothing is imported. Rows that already exist are skipped
   unless you tick *Replace*.

At least **14 days** of demand per SKU enable forecasts and XYZ classes; 28+ days give more reliable results.

## 2. Reading the dashboard

* **Status**: critical (at or below safety stock), low (at or below reorder point), overstock (above max
  level), expiring (within 30 days), normal.
* **ABC**: share of annual consumption value (A = items making up the first ~80 % of value).
* **XYZ**: demand variability from your recorded history (X steady, Y variable, Z erratic); "—" means not
  enough history - it is never guessed.
* **FSN**: turnover (fast/slow/non-moving). **VED** is a rule-of-thumb proxy, not a managerial criticality
  assessment.
* Open any SKU to see EOQ, safety stock and reorder point **with the numbers substituted**, using your
  organisation's settings, plus data-quality warnings.
* **Stock adjustments** (administrator, inventory manager, warehouse manager): on the SKU page enter a counted quantity and a
  reason (cycle count, damage, expiry write-off, ...). Every change is kept in the item's ledger shown
  below the form; stock can never go negative. Uploading a new inventory file starts a new snapshot.
* **Transfers** (Warehouses page): the system proposes moving surplus of a SKU to a warehouse that is short
  of it, within the destination's free capacity. When the goods have actually moved, click **Execute** and
  confirm the quantity: both stock records change and the move appears in both ledgers.

## 3. Forecasting

Forecasts are made only from recorded demand history, turned into one value per day (several rows for the
same day, e.g. channels, are added; days without a record count as zero and are reported - many filled days
lower the reliability label). Products that sell only on some days (intermittent demand) are forecast with
the SBA method, which predicts an average daily rate; others use Holt's method. The page tells you which SKUs lack history, the data
period used, and accuracy on the last 7 days compared with a naive "same as yesterday" forecast (MASE < 1
means better than naive). MAPE is not shown when some days have zero demand; WAPE is shown instead. The
*illustration* switch draws synthetic curves for demos only - never use them for decisions.

## 4. Optimisation and explainable policy

The Optimisation Center proposes order quantities and safety stocks under your budget, warehouse capacity
and service-level target. The explainable (EIDSF) policy shows, per SKU, the cost drivers, the rules that
fired and why, which resources (budget, warehouse pools) are actually limiting, and a certified bound on
how far the plan can be from the optimum. These are **model calculations**: they depend on your data and
settings and are not guarantees of business results.

## 5. Purchasing

1. Procurement & POs shows the **replenishment plan** (items at or below their reorder point) with a
   recommended quantity (respecting minimum order quantity and pack size) and the EOQ for reference.
2. **Create draft POs from plan** makes one draft per supplier. Nothing is sent to suppliers by the system.
3. A procurement manager or administrator **approves** (a procurement manager cannot approve their own
   PO), then marks it **ordered** after sending it to the supplier yourself.
4. When goods arrive, **Receive** posts the quantity to stock and the transaction ledger (partial receipts
   are allowed; over-receipt is refused). Drafts, approved and ordered POs can be cancelled.

## 6. Reports

CSV, Excel and PDF exports contain only your organisation's data and state the organisation, time stamp,
time zone, currency and source upload. Text cells that look like spreadsheet formulas are neutralised.

## 7. Your account

* **Joining**: open the invitation link from your e-mail (or from your administrator), choose a username and
  password. Links work once and expire after 7 days.
* **Forgot password**: on the sign-in page choose *Forgot password?* and enter your account's e-mail
  address. If e-mail is configured you receive a link valid for 60 minutes; all your sessions are signed out
  after the reset. Otherwise ask your administrator.
* **Read-only banner**: if your organisation's subscription has ended, payment is overdue beyond the grace
  period, or deletion was requested, you can still view and export data but not change it.
* **Two-factor login**: Settings -> My account -> turn on two-factor login, scan the key with an
  authenticator app, enter the code, and keep the recovery codes somewhere safe.
* **Owner**: Settings -> Organisation data & ownership: export everything, transfer ownership, request or
  cancel deletion.


Settings -> My account: change your password (your other sessions are signed out) or sign out on all
devices. Repeated wrong passwords lock the account for 15 minutes.
