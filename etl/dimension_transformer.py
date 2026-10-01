#!/usr/bin/env python3
"""Configuration-driven transformation for hierarchical dimensions."""
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
    """Transform any configured member/hierarchy/attribute dimension."""

    def __init__(self, config_path: str | Path, input_dir: str | Path, output_dir: str | Path):
        self.config_path = Path(config_path)
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.config_path.open(encoding="utf-8") as stream:
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
        if pd.isna(value) or str(value).strip() == "":
            return pd.NA
        raw = str(value).strip().upper()
        for sentinel, replacement in sentinels.items():
            if raw == str(sentinel).upper() or raw.startswith(f"{str(sentinel).upper()}:"):
                return replacement
        try:
            date_part = raw.split(":", 1)[0]
            return pd.to_datetime(date_part, format="%d%b%Y").strftime(output_format)
        except (TypeError, ValueError):
            try:
                return pd.to_datetime(raw).strftime(output_format)
            except (TypeError, ValueError):
                LOGGER.warning("Unable to parse date value %r", value)
                return value

    def _normalize_dates(self, frame: pd.DataFrame | None, config: dict[str, Any]) -> pd.DataFrame | None:
        if frame is None:
            return None
        sentinels = config.get("sentinel_dates", {})
        output_format = config.get("date_format_output", "%Y-%m-%d %H:%M:%S")
        for column in config.get("date_columns", []):
            column = column.upper()
            if column in frame.columns:
                frame[column] = frame[column].map(
                    lambda value: self._parse_date(value, sentinels, output_format)
                )
        return frame

    @staticmethod
    def _rename(frame: pd.DataFrame | None, mapping: dict[str, str] | None) -> pd.DataFrame | None:
        if frame is None:
            return None
        mapping = {str(k).upper(): str(v).upper() for k, v in (mapping or {}).items()}
        return frame.rename(columns={key: value for key, value in mapping.items() if key in frame.columns})

    @staticmethod
    def _first_existing(frame: pd.DataFrame | None, candidates: list[str]) -> str | None:
        if frame is None:
            return None
        return next((candidate.upper() for candidate in candidates if candidate.upper() in frame.columns), None)

    def _build_dimension(self, cfg: dict[str, Any]) -> pd.DataFrame:
        source = cfg["source"]
        member = self._read(source.get("member_file"), required=True)
        hierarchy = self._read(source.get("hierarchy_file"))
        attributes = self._read(source.get("attribute_file"))
        extra_master = self._read(source.get("master_file"))

        member = self._normalize_dates(member, cfg)
        hierarchy = self._normalize_dates(hierarchy, cfg)
        attributes = self._normalize_dates(attributes, cfg)
        extra_master = self._normalize_dates(extra_master, cfg)
        member = self._rename(member, cfg.get("master_column_mapping"))

        if member is None or "DIMENSION_ID" not in member.columns:
            raise ValueError("master_column_mapping must produce DIMENSION_ID")

        keys = cfg["entity_keys"]
        entity_source = keys["entity_id_col"].upper()
        parent_source = keys.get("parent_id_col", "PARENT_MEMBER_ID").upper()
        if hierarchy is not None and entity_source in hierarchy.columns and parent_source in hierarchy.columns:
            parent_map = hierarchy[[entity_source, parent_source]].rename(columns={
                entity_source: "DIMENSION_ID",
                parent_source: "PARENT_DIMENSION_ID",
            }).drop_duplicates(subset=["DIMENSION_ID"])
            member = member.merge(parent_map, on="DIMENSION_ID", how="left", validate="one_to_one")

        attributes = self._rename(attributes, cfg.get("attribute_column_mapping"))
        if attributes is not None and "DIMENSION_ID" in attributes.columns:
            attributes = attributes.drop_duplicates(subset=["DIMENSION_ID"])
            member = member.merge(attributes, on="DIMENSION_ID", how="left", validate="one_to_one")

        # Optional staging/master data is joined only when its configured key can be identified.
        if extra_master is not None:
            extra_master = self._rename(extra_master, cfg.get("master_file_column_mapping", {}))
            master_key = self._first_existing(extra_master, ["DIMENSION_ID", "CUSTOMER_ID", "PRODUCT_ID", "LOCATION_ID"])
            if master_key:
                extra_master = extra_master.rename(columns={master_key: "DIMENSION_ID"})
                extra_master = extra_master.drop_duplicates(subset=["DIMENSION_ID"])
                new_columns = [column for column in extra_master.columns if column != "DIMENSION_ID" and column not in member.columns]
                member = member.merge(extra_master[["DIMENSION_ID", *new_columns]], on="DIMENSION_ID", how="left", validate="one_to_one")

        return member.drop_duplicates(subset=cfg.get("dedup_key", ["DIMENSION_ID"])).reset_index(drop=True)

    def _build_hierarchy(self, cfg: dict[str, Any]) -> pd.DataFrame:
        source = cfg["source"]
        hierarchy = self._read(source.get("hierarchy_file"))
        if hierarchy is None:
            return pd.DataFrame()
        return self._rename(hierarchy, cfg.get("hierarchy_column_mapping")).drop_duplicates().reset_index(drop=True)

    def _build_metadata(self, cfg: dict[str, Any]) -> pd.DataFrame:
        metadata = self._read(cfg["source"].get("metadata_file"))
        return metadata.drop_duplicates().reset_index(drop=True) if metadata is not None else pd.DataFrame()

    def transform_dimension(self, name: str, cfg: dict[str, Any]) -> dict[str, int]:
        LOGGER.info("Transforming %s", name)
        dimension = self._build_dimension(cfg)
        hierarchy = self._build_hierarchy(cfg)
        metadata = self._build_metadata(cfg)
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
    results = DimensionTransformer(args.config, args.input, args.output).transform_all()
    for name, stats in results.items():
        LOGGER.info("%s: %s", name, stats)


if __name__ == "__main__":
    main()
