# Research data ingestion and validation (v2.2)

## Two separate paths

| Path | For | Behaviour |
|---|---|---|
| **Research dataset** — `python -m research.ingest <file>` or `POST /api/research/validate` | The MPhil 30-SKU dataset (or any file with the research schema) | Strict. No value is imputed, clipped, rounded or deleted. Unparseable cells are errors. Units come only from the mapping file. |
| **Inventory upload** — Data Upload page, `POST /api/upload` | Operational stock lists for the dashboard | Lenient but transparent: every value not read from the file is listed in `derivations` (field, rule, row count) and stored with the batch. Research-schema files are refused here. |

## Research dataset: how to run

```bash
cd backend
python -m research.ingest "C:\path\to\research_dataset.xlsx"             # first sheet
python -m research.ingest data.xlsx --sheet "Data" --mapping my_mapping.json
```

Exit code 0 = valid, 1 = validation errors, 2 = file/mapping problem.
Output folder: `backend/research/data/processed/<provenance>_<sha256[:12]>/`

| File | Content |
|---|---|
| `source.<ext>` | byte-identical copy of the input |
| `processed.csv` | canonical column names, values exactly as parsed |
| `validation_report.json` / `.md` | every issue with rule, column and 1-based row numbers |
| `metadata.json` | SHA-256 of source and processed data, provenance, units, rules applied, software versions, timestamp |

`backend/research/data/` is git-ignored so the dataset is never committed.

## Mapping file and units

`backend/research/config/research_30sku_mapping.json` maps canonical fields to the documented
column names. **All units are `null` (unconfirmed)** until the researcher sets them:

| Key | Allowed values |
|---|---|
| `currency` | free text, e.g. `PKR` |
| `annual_demand` | `units_per_year` |
| `demand_std_period` | `year`, `month`, `week`, `day` — the period that `Demand_StdDev_Sigma` refers to |
| `holding_cost_basis` | `per_unit_per_year`, `fraction_of_unit_cost_per_year` |
| `shortage_cost_basis` | `per_unit_short`, `per_unit_short_per_year`, `per_stockout_occasion` |
| `lead_time` | `days`, `weeks`, `months`, `years` |
| `unit_volume` | free text, e.g. `m3` |

While any unit is `null`, `units_confirmed=false` and unit-dependent calculations must not be run.

## Validation rules

| Rule | Level | Condition |
|---|---|---|
| `missing_column` | error | a mapped column is absent |
| `missing_value` | error (warning for `Segment`) | empty cell |
| `not_a_number` | error | numeric field cannot be parsed |
| `non_positive` | error | demand, unit cost, ordering cost, holding cost, lead time or volume ≤ 0 |
| `negative` | error | σ or shortage cost < 0 |
| `duplicate_sku` | error | repeated `SKU_ID` |
| `zero_std` | warning | σ = 0 (deterministic demand) |
| `high_cv` | warning | σ / (D̄ per stated period) > 1 (normal approximation questionable) |
| `holding_ratio` | warning | h outside 1–100 % of c per year (when h is per unit per year) |
| `holding_rate_gt_1` | warning | h > 1 when h is a fraction of c |
| `long_lead_time` | warning | L > 365 days |
| `zero_shortage_cost` | warning | π = 0 |
| `row_count` | warning | rows ≠ `expected_rows` (30) |
| `surrounding_whitespace` | warning | spaces around IDs/text (kept as-is) |
| `units_unconfirmed` | warning | any unit is `null` |
| `unmapped_columns` | info | extra columns present |

## Provenance labels

`research`, `synthetic`, `demonstration`, `user_uploaded`. Every research artifact and every
inventory upload batch (`upload_batches.provenance`) carries one. Batches uploaded before
v2.2 are shown as `unlabelled`; they are not relabelled automatically.
