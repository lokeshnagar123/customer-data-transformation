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

    def _derived_hierarchy(self, member: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
        keys = cfg["entity_keys"]
        child = keys["entity_id_col"].upper()
        parent = keys["parent_id_col"].upper()
        root = keys.get("root_id_col", "ALL_CUSTOMERS_ID").upper()
        assoc = keys.get("assoc_type_col", "HIERARCHY").upper()
        required = [child, parent]
        missing = [column for column in required if column not in member.columns]
        if missing:
            raise ValueError(f"Cannot derive hierarchy; missing columns: {missing}")
        result = pd.DataFrame({
            "MEMBER_ASSOC_TYPE_CD": member[assoc] if assoc in member else pd.NA,
            "MEMBER_ID": member[child],
            "PARENT_MEMBER_ID": member[parent],
            "DEFAULT_MEMBER_ID": member[root] if root in member else pd.NA,
        })
        return result.drop_duplicates().reset_index(drop=True)

    def _build_dimension(self, cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
        source = cfg["source"]
        member_raw = self._normalize_dates(self._read(source.get("member_file"), required=True), cfg)
        hierarchy = self._normalize_dates(self._read(source.get("hierarchy_file")), cfg)
        attributes = self._normalize_dates(self._read(source.get("attribute_file")), cfg)

        member = self._rename(member_raw, cfg.get("master_column_mapping"))
        if member is None or "DIMENSION_ID" not in member.columns:
            raise ValueError("master_column_mapping must produce DIMENSION_ID")

        if hierarchy is None:
            hierarchy_output = self._derived_hierarchy(member_raw, cfg)
            hierarchy_keys = cfg["entity_keys"]
            parent_map = hierarchy_output[["MEMBER_ID", "PARENT_MEMBER_ID"]].rename(columns={
                "MEMBER_ID": "DIMENSION_ID",
                "PARENT_MEMBER_ID": "PARENT_DIMENSION_ID",
            })
        else:
            hierarchy_output = hierarchy.drop_duplicates().reset_index(drop=True)
            keys = cfg["entity_keys"]
            child = keys["entity_id_col"].upper()
            parent = keys["parent_id_col"].upper()
            parent_map = hierarchy[[child, parent]].rename(columns={
                child: "DIMENSION_ID",
                parent: "PARENT_DIMENSION_ID",
            }).drop_duplicates(subset=["DIMENSION_ID"])

        member = member.merge(parent_map, on="DIMENSION_ID", how="left", validate="one_to_one")
        attributes = self._rename(attributes, cfg.get("attribute_column_mapping"))
        if attributes is not None and "DIMENSION_ID" in attributes.columns:
            attributes = attributes.drop_duplicates(subset=["DIMENSION_ID"])
            member = member.merge(attributes, on="DIMENSION_ID", how="left", validate="one_to_one")

        return member.drop_duplicates(subset=cfg.get("dedup_key", ["DIMENSION_ID"])).reset_index(drop=True), hierarchy_output

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
