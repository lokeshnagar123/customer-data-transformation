#!/usr/bin/env python3
"""Configuration-driven transformation for two-file or hierarchical dimensions."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

LOGGER = logging.getLogger("dimension_transformer")


class DimensionTransformer:
    """Transform a dimension without requiring hierarchy or metadata files."""

    def __init__(self, config_path: str | Path, input_dir: str | Path, output_dir: str | Path):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with Path(config_path).open(encoding="utf-8") as stream:
            self.config: dict[str, Any] = yaml.safe_load(stream) or {}

    @staticmethod
    def _clean(frame: pd.DataFrame | None) -> pd.DataFrame | None:
        if frame is None:
            return None
        frame = frame.copy()
        frame.columns = [str(column).strip().upper() for column in frame.columns]
        for column in frame.columns:
            frame[column] = frame[column].replace(r"^\s*$", np.nan, regex=True)
        return frame

    def _read(self, filename: str | None, required: bool = False) -> pd.DataFrame | None:
        if not filename:
            return None
        path = self.input_dir / filename
        if not path.exists():
            if required:
                raise FileNotFoundError(f"Required source file not found: {path}")
            LOGGER.warning("Optional source file not found: %s", path)
            return None
        return self._clean(pd.read_csv(path, dtype="string"))

    @staticmethod
    def _parse_date(value: Any, sentinels: dict[str, str], output_format: str) -> Any:
        if pd.isna(value) or not str(value).strip():
            return pd.NA
        raw = str(value).strip().upper()
        for sentinel, replacement in sentinels.items():
            token = str(sentinel).upper()
            if raw == token or raw.startswith(f"{token}:"):
                return replacement
        try:
            return pd.to_datetime(raw.split(":", 1)[0], format="%d%b%Y").strftime(output_format)
        except (TypeError, ValueError):
            try:
                return pd.to_datetime(raw).strftime(output_format)
            except (TypeError, ValueError):
                LOGGER.warning("Unable to parse date value %r", value)
                return value

    def _normalize_dates(self, frame: pd.DataFrame | None, cfg: dict[str, Any]) -> pd.DataFrame | None:
        if frame is None:
            return None
        sentinels = cfg.get("sentinel_dates", {})
        output_format = cfg.get("date_format_output", "%Y-%m-%d %H:%M:%S")
        for column in cfg.get("date_columns", []):
            column = column.upper()
            if column in frame.columns:
                frame[column] = frame[column].map(lambda value: self._parse_date(value, sentinels, output_format))
        return frame

    @staticmethod
    def _rename(frame: pd.DataFrame | None, mapping: dict[str, str] | None) -> pd.DataFrame | None:
        if frame is None:
            return None
        mapping = {str(k).upper(): str(v).upper() for k, v in (mapping or {}).items()}
        return frame.rename(columns={key: value for key, value in mapping.items() if key in frame.columns})

    def _build_hierarchy_from_staging(self, member: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
        """Build hierarchy_dim by extracting root, types, and customers from staging data."""
        keys = cfg["entity_keys"]
        child_col = keys["entity_id_col"].upper()
        parent_col = keys["parent_id_col"].upper()
        root_col = keys.get("root_id_col", "ALL_CUSTOMERS_ID").upper()
        assoc_col = keys.get("assoc_type_col", "HIERARCHY").upper()

        rows = []

        # 1. Root node (ALL_CUSTOMERS)
        if root_col in member.columns:
            root_id = member[root_col].iloc[0]
            root_assoc = member[assoc_col].iloc[0] if assoc_col in member.columns else "CUSTOMER"
            rows.append({
                "MEMBER_ASSOC_TYPE_CD": root_assoc,
                "MEMBER_ID": root_id,
                "PARENT_MEMBER_ID": root_id,
                "DEFAULT_MEMBER_ID": root_id,
            })

        # 2. Customer types (unique parent IDs from member data)
        if parent_col in member.columns and child_col in member.columns:
            types = member[[parent_col, "CUSTOMER_TYPE_NM" if "CUSTOMER_TYPE_NM" in member.columns else parent_col]].copy()
            types = types[types[parent_col] != root_col].drop_duplicates(subset=[parent_col])
            for _, row in types.iterrows():
                type_id = row[parent_col]
                type_assoc = member[assoc_col].iloc[0] if assoc_col in member.columns else "CUSTOMER"
                rows.append({
                    "MEMBER_ASSOC_TYPE_CD": type_assoc,
                    "MEMBER_ID": type_id,
                    "PARENT_MEMBER_ID": root_col if root_col in member.columns else member[root_col].iloc[0],
                    "DEFAULT_MEMBER_ID": root_col if root_col in member.columns else member[root_col].iloc[0],
                })

        # 3. Individual customers (child nodes)
        for _, row in member.iterrows():
            child_id = row[child_col]
            parent_id = row[parent_col]
            root_id = row[root_col] if root_col in member.columns else None
            assoc = row[assoc_col] if assoc_col in member.columns else "CUSTOMER"
            rows.append({
                "MEMBER_ASSOC_TYPE_CD": assoc,
                "MEMBER_ID": child_id,
                "PARENT_MEMBER_ID": parent_id,
                "DEFAULT_MEMBER_ID": root_id,
            })

        return pd.DataFrame(rows).drop_duplicates().reset_index(drop=True)

    def _build_dimension_from_staging(self, member: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
        """Build dimension by extracting all hierarchy levels from staging data."""
        keys = cfg["entity_keys"]
        child_col = keys["entity_id_col"].upper()
        parent_col = keys["parent_id_col"].upper()
        root_col = keys.get("root_id_col", "ALL_CUSTOMERS_ID").upper()

        rows = []

        # 1. Root node
        if root_col in member.columns:
            root_id = member[root_col].iloc[0]
            root_nm = member["ALL_CUSTOMERS_NM"].iloc[0] if "ALL_CUSTOMERS_NM" in member.columns else "All Customer"
            root_desc = member["ALL_CUSTOMERS_DESC"].iloc[0] if "ALL_CUSTOMERS_DESC" in member.columns else "All Customer"
            rows.append({
                "DIMENSION_ID": root_id,
                "DIMENSION_NM": root_nm,
                "DIMENSION_DESC": root_desc,
                "HIERARCHY": "ROOT",
                "LEVELNAME": "ALL_CUSTOMERS",
            })

        # 2. Customer types (unique by CUSTOMER_TYPE_ID)
        if "CUSTOMER_TYPE_ID" in member.columns:
            types = member[["CUSTOMER_TYPE_ID", "CUSTOMER_TYPE_NM", "CUSTOMER_TYPE_DESC"]].copy()
            types = types.drop_duplicates(subset=["CUSTOMER_TYPE_ID"])
            for _, row in types.iterrows():
                rows.append({
                    "DIMENSION_ID": row["CUSTOMER_TYPE_ID"],
                    "DIMENSION_NM": row["CUSTOMER_TYPE_NM"],
                    "DIMENSION_DESC": row["CUSTOMER_TYPE_DESC"],
                    "HIERARCHY": "CUSTOMER",
                    "LEVELNAME": "CUSTOMER_TYPE",
                })

        # 3. Individual customers
        for _, row in member.iterrows():
            rows.append({
                "DIMENSION_ID": row[child_col],
                "DIMENSION_NM": row["CUSTOMER_NM"] if "CUSTOMER_NM" in member.columns else row[child_col],
                "DIMENSION_DESC": row["CUSTOMER_DESC"] if "CUSTOMER_DESC" in member.columns else row[child_col],
                "HIERARCHY": row[keys.get("assoc_type_col", "HIERARCHY")] if keys.get("assoc_type_col", "HIERARCHY") in member.columns else "CUSTOMER",
                "LEVELNAME": "CUSTOMER",
            })

        return pd.DataFrame(rows).drop_duplicates(subset=["DIMENSION_ID"]).reset_index(drop=True)

    def _build_dimension(self, cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
        source = cfg["source"]
        member_raw = self._normalize_dates(self._read(source.get("member_file"), required=True), cfg)
        hierarchy = self._normalize_dates(self._read(source.get("hierarchy_file")), cfg)
        attributes = self._normalize_dates(self._read(source.get("attribute_file")), cfg)

        # If no hierarchy file, synthesize from staging data
        if hierarchy is None:
            dimension = self._build_dimension_from_staging(member_raw, cfg)
            hierarchy_output = self._build_hierarchy_from_staging(member_raw, cfg)
        else:
            member = self._rename(member_raw, cfg.get("master_column_mapping"))
            if member is None or "DIMENSION_ID" not in member.columns:
                raise ValueError("master_column_mapping must produce DIMENSION_ID")
            dimension = member
            hierarchy_output = self._rename(hierarchy, cfg.get("hierarchy_column_mapping"))

        # Merge attributes
        attributes = self._rename(attributes, cfg.get("attribute_column_mapping"))
        if attributes is not None and "DIMENSION_ID" in attributes.columns:
            attributes = attributes.drop_duplicates(subset=["DIMENSION_ID"])
            dimension = dimension.merge(attributes, on="DIMENSION_ID", how="left", validate="one_to_one")

        return dimension.drop_duplicates(subset=cfg.get("dedup_key", ["DIMENSION_ID"])).reset_index(drop=True), hierarchy_output

    def transform_dimension(self, name: str, cfg: dict[str, Any]) -> dict[str, int]:
        LOGGER.info("Transforming %s", name)
        dimension, hierarchy = self._build_dimension(cfg)
        metadata = self._read(cfg["source"].get("metadata_file"))
        metadata = metadata.drop_duplicates().reset_index(drop=True) if metadata is not None else pd.DataFrame()
        output = cfg["output_files"]
        dimension.to_csv(self.output_dir / output["dimension_dim"], index=False)
        hierarchy.to_csv(self.output_dir / output["hierarchy_dim"], index=False)
        metadata.to_csv(self.output_dir / output["metadata_dim"], index=False)
        dimension.to_csv(self.output_dir / output["master_target"], index=False)
        LOGGER.info("  dimension_dim: %d rows", len(dimension))
        LOGGER.info("  hierarchy_dim: %d rows", len(hierarchy))
        LOGGER.info("  metadata_dim: %d rows", len(metadata))
        return {"dimension_rows": len(dimension), "hierarchy_rows": len(hierarchy), "metadata_rows": len(metadata)}

    def transform_all(self) -> dict[str, dict[str, int]]:
        results = {}
        for name, cfg in self.config.get("dimensions", {}).items():
            if cfg.get("enabled", True):
                results[name] = self.transform_dimension(name, cfg)
        return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/dimension_config.yaml")
    parser.add_argument("--input", default="input")
    parser.add_argument("--output", default="output")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for name, stats in DimensionTransformer(args.config, args.input, args.output).transform_all().items():
        LOGGER.info("%s: %s", name, stats)


if __name__ == "__main__":
    main()
