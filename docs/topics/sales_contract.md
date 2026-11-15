# Knowledge base: `dbo.sales_contract` (draft for the Topic MCP server)

Status: **DRAFT written by the coding agent from the live source data on 2026-11-15, read-only.**
Every statement marked **[CONFIRM]** is an inference that the business owner must confirm before the
agent is allowed to rely on it. No customer names, employee names or money totals are stored here.

Source: SQL Server `dbo.sales_contract`, 35,440 rows, 66 columns, every column nullable.
Rewritten by a delete+insert job about every 10 minutes (about 2 minutes per run, one commit).

## 1. What one row is

One row is **one item line of one contract/tender document of one company**.
A document (`doc_no`) has on average 5 lines (up to 478). About 8,000 `biz_doc_id`, 6,800 `doc_no`.

- **Natural key is `(entity, row_id)`, not `row_id` alone** (verified unique on all 35,440 rows). 57 `row_id` values appear in two
  companies with different content. Never join or de-duplicate on `row_id` only.
- Companies (`entity`, 4 values): `vcp` (72% of rows), `salud` (23%), `nufa` (3%), `durabe` (2%).
  Document code follows the company: `C2` = vcp and salud, `SO` = nufa, `C3` = durabe.
- Date range: documents from 2004, tender end dates up to 2028. Data last refreshed per row on
  `update_time` (latest value 2026-11-04 at the time of reading).

## 2. Column groups

| Group | Columns | Meaning |
| --- | --- | --- |
| Company / document | `entity`, `doc_code`, `doc_no`, `biz_doc_id`, `doc_date`, `decision_no`, `description`, `remark`, `created_at`, `update_time` | Which company, which document, document date, tender decision number (null in 764 rows). |
| Item | `item_code`, `code_old`, `item_name`, `unit` | `code_old` is the **goods code** users know; `item_name` is the goods name. 878 distinct `code_old`. |
| Customer | `customer_code`, `customer_name`, `customer_address`, `address` | About 1,950 customers. **29 customer codes have more than one spelling of the name**, so group by `customer_code`, show one name. |
| Place | `city_name` (current province), `city_name_2` (name before the merger of provinces), `city_code` | 445 rows have no place. **Use `city_name` for "now", `city_name_2` for "before the merger"**. The two differ in how rows are grouped (e.g. HCM 14,302 rows vs 12,761). |
| Classification | `cat_1`, `cat_2`, `is_hang_gui_thau`, `is_tong_thau` | See section 4. `is_tong_thau` is always `2. No` (useless today). |
| Contract values | `quantity_contract`, `unit_price_before_tax_contract`, `unit_price_after_tax_contract`, `amount_before_tax_contract`, `amount_tax_contract`, `amount_after_tax_contract`, `tax_code` | Quantity, **winning/contract unit price**, and **line total**. After-tax total = before-tax total + tax. Checked: quantity x after-tax unit price = after-tax total on every row. |
| Delivered / returned / finished | `quantity_delivered`, `price_before_tax_delivered`, `tax_amount_delivered`, `price_after_tax_delivered`; same four for `_returned` and `_finished` | **Only totals exist here, no unit price.** `price_*_finished` is a money total, not a unit price. Verified: `delivered + returned = finished` on every row (returned is negative). |
| Start date | `confirmed_date` (start of the contract), `confirmed_year`, `confirmed_month_no`, `confirmed_month_name_1` (`YYYY-MM`), `confirmed_month_name_2` (`Mar`), `confirmed_quarter_no`, `confirmed_quarter_text`, `confirmed_leadtime` | Null in 102 rows. |
| End date | `finished_date` (tender end), `finished_year`, `finished_month_*`, `finished_quarter_*`, `finished_leadtime` | Never null. **This is when the tender/contract ends, not when goods were delivered.** |
| Duration | `contract_leadtime` | Days between start and end **[CONFIRM unit]**. Null when `confirmed_date` is null. |
| Salesperson | `employee_code_bdd`, `employee_name_bdd` | Name is null in 54% of rows, code null in 599 rows (9,082 more are empty strings). Treat as incomplete. |

`quantity_*`, prices and totals are floating point numbers in Vietnamese dong (VND), including
fractional unit prices such as 7839.9993. Round on display; never compare totals for equality.

## 3. Definitions the agent must use

These are the proposed defaults. Items marked [CONFIRM] must be signed off by the business owner.

- **Contract value** ("giá trị hợp đồng / giá trị trúng thầu") = `SUM(amount_after_tax_contract)`.
  Before tax = `amount_before_tax_contract`. Say which one is shown. VND, shown in billions (tỷ)
  when above one billion.
- **Completed value** ("giá trị hoàn thành / thực hiện") = `SUM(price_after_tax_finished)`;
  completed quantity = `SUM(quantity_finished)`. It is **cumulative to date** for the line, not per
  month. [CONFIRM]
- **Completion rate** = completed value / contract value (guard against a zero denominator: 339 rows
  have a zero contract amount). 120 rows have completed quantity above contract quantity: do not
  assume the rate is at most 100%.
- **Tender is still in force** (`is_thau_con_hieu_luc`) = `finished_date >= today`. The column named
  `is_thau_con_hieu_luc` **does not exist in the table**; the agent must compute it. Whether a
  contract that has not started (`confirmed_date > today`) counts as in force is [CONFIRM].
  Today in the server's time zone. About 6,400 rows have `finished_date` today or later.
- **Time grouping**: by default group a contract by the month/quarter/year of `confirmed_date`
  (when it was won/started). Use `finished_*` only when the user asks "ending in / expiring in".
  **The table cannot say when goods were delivered**: "revenue delivered in March" is not
  answerable from this table. The agent must say so instead of grouping `price_*_finished` by
  `finished_date`. [CONFIRM that no delivery-date table exists]
- **"Doanh số"** is ambiguous: ask which one (contract value, or completed value to date). Do not
  silently pick one. If the user insists, default to contract value by `confirmed_date`, and say so.
- **Customer**: group by `customer_code`; display `customer_name`. Province: ask current or before merger.
- **Company**: the 4 `entity` values. "All companies" means no filter; never mix two companies'
  rows by `row_id`.

## 4. Classifications (values observed, meaning to confirm)

`cat_1` (3 values): `2. Thầu` (tender, 61%), `3. HĐMB` (sales contract, 37%), `4. Khác` (other, 2%).

`cat_2` (8 values, finer split of `cat_1`):

| `cat_2` | Rows | Reading [CONFIRM all] |
| --- | --- | --- |
| `2. Thầu` | 11,697 | Tender won |
| `3. Áp Thầu` | 9,347 | Orders applied against a tender |
| `4. Gửi Thầu` | 534 | Goods consigned to a tender (no sales until used) |
| `5. HĐMB` | 12,954 | Sales contract |
| `0. Khác` | 816 | Other (`cat_1` = `4. Khác`) |
| `6. Thầu CKG`, `7. Áp Thầu CKG`, `9. HĐMB CKG` | 66, 15, 11 | Same three kinds, "CKG" variant: unknown meaning |

`is_hang_gui_thau`: `1. Yes` for 1,548 rows (mostly `Thầu` and `Áp Thầu`), `2. No` otherwise.
Guess: consignment goods flag. [CONFIRM]

Tax code `tax_code`: `R05` (98%), `R08`, `R10`, 129 empty. VAT 5%, 8%, 10% (verified: tax amount divided by before-tax amount is exactly 0.05, 0.08, 0.10); empty code = 0 tax.
Units (`unit`): mostly `Viên` (65%), `Lọ`, `Ống`, `Chai`, `Gói`, `Túi`. A few dirty spellings
(`Hộp/Lọ` twice with different spaces, `LọSultam`, `Amebismo`). Never add quantities of different units
without saying so.

## 5. Traps for query writing

1. Text filters on Vietnamese values need Unicode literals. A filter like `cat_1 = '2. Thầu'`
   sent as a plain literal silently matches **zero rows**. Use parameters or `N'...'`.
2. Values contain a number prefix (`2. Thầu`, `1. Yes`). Offer short aliases to users, filter on full values.
3. All columns are nullable; `employee_*`, `city_*`, `confirmed_*`, `decision_no` have real nulls.
4. Dates are `date`, but `created_at` and `update_time` are `datetime` (no time zone).
5. The table is rebuilt every ~10 minutes; answers can differ between two asks. Always state the
   "data as of" time (`MAX(update_time)` or the sync time).
6. Row counts shown to users must come from the data, not from this document (it will go stale).

## 6. Sample questions the topic should support

Answerable now (with the definitions above):

1. Tổng giá trị hợp đồng năm 2025 theo từng công ty.
2. Top 10 khách hàng theo giá trị hợp đồng trong năm nay.
3. Số hợp đồng/thầu còn hiệu lực theo từng công ty.
4. Các thầu sắp hết hạn trong 90 ngày tới, theo khách hàng và tổng giá trị còn lại.
5. Giá trị hợp đồng theo tỉnh thành (hiện tại) trong quý này.
6. Tỷ lệ hoàn thành của các thầu đang hiệu lực, thấp nhất trước.
7. Top 20 mặt hàng (`code_old`) theo giá trị hợp đồng của năm trước.
8. Đơn giá trúng thầu của một mã hàng ở các tỉnh khác nhau.
9. Thầu bắt đầu mỗi tháng trong năm nay (số tài liệu, tổng giá trị).
10. Giá trị hàng gửi thầu đang còn hiệu lực.
11. So sánh giá trị hợp đồng giữa hai năm theo nhóm `cat_1` / `cat_2`.
12. Nhân viên kinh doanh nào có giá trị hợp đồng cao nhất (cảnh báo: dữ liệu nhân viên thiếu hơn một nửa).

Must ask or refuse with an explanation:

13. "Doanh số tháng này" (which definition?). 14. "Hàng đã giao trong tháng 3" (no delivery date).
15. "Lợi nhuận / biên lợi nhuận" (no cost column). 16. "Tồn kho" (not in this table).

## 7. Open questions for the business owner

1. Confirm the proposed "doanh số" default and the "completed value is cumulative" reading.
2. Meaning of `Áp Thầu`, `Gửi Thầu`, `CKG`, `is_hang_gui_thau`, tax codes.
3. Should a not-yet-started contract count as "in force"? Does `finished_date` get extended when a tender is extended?
4. Is there a table with delivery dates? If yes, add it as a second topic.
5. Which columns are not allowed for which roles (salesperson, customer address, unit prices)?
6. Which rows are test or obsolete (e.g. documents from 2004-2018, the 816 `Khác` rows)?
