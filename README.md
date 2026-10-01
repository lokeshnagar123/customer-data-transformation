# Generic Dimension Transformation Framework

This repository transforms dimensions from configuration rather than hardcoded Python logic.

## Customer input mode

Customer currently has exactly two source files in `input/`:

- `stg_customer.csv`: customer master, type, root, and hierarchy columns
- `stg_customer_attr.csv`: customer attributes keyed by `CUSTOMER_ID`

No `customer_member.csv`, `customer_member_hier.csv`, or metadata source file is required.
The transformer derives the customer hierarchy from `CUSTOMER_ID`, `CUSTOMER_TYPE_ID`,
`ALL_CUSTOMERS_ID`, and `HIERARCHY` in `stg_customer.csv`.

## Run

```bash
python -m pip install -r requirements.txt
python transform_dimensions.py
```

Customer outputs are written to `output/`:

- `customer_dim.csv`
- `customer_hierarchy_dim.csv`
- `customer_property_metadata_dim.csv` (empty because no metadata source was supplied)
- `customer_master_target.csv`

## Adding another dimension

Add a dimension block to `config/dimension_config.yaml`. Set `hierarchy_file` and
`metadata_file` to `null` when they are not available. For a two-file dimension,
provide `member_file`, `attribute_file`, `entity_keys`, and the column mappings.
Keep new dimensions disabled until their input files and mappings are available.
