#!/usr/bin/env python3
"""
Generic Dimension Transformation Framework
Supports any dimension (customer, product, location, supplier, etc.)
No code changes needed - just update dimension_config.yaml for new dimensions.
"""

import pandas as pd
import numpy as np
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DimensionTransformer:
    """
    Generic dimension transformation engine.
    Transforms master data from source to target dimensional model.
    Works for any dimension with zero code changes.
    """

    def __init__(self, config_path: str, source_dir: str, output_dir: str):
        """
        Initialize the transformer.
        
        Args:
            config_path: Path to dimension_config.yaml
            source_dir: Directory containing source CSV files
            output_dir: Directory for output files
        """
        self.config_path = Path(config_path)
        self.source_dir = Path(source_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        
        # Load configuration
        self.config = self._load_config()
        self.stats = {}
        
        logger.info(f"Initialized DimensionTransformer")
        logger.info(f"Source dir: {self.source_dir}")
        logger.info(f"Output dir: {self.output_dir}")

    def _load_config(self) -> Dict:
        """
        Load dimension configuration from YAML.
        
        Returns:
            dict: Configuration dictionary
        """
        with open(self.config_path, 'r') as f:
            config = yaml.safe_load(f)
        logger.info(f"Loaded configuration from {self.config_path}")
        return config

    def _clean_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Standardize column names to uppercase.
        
        Args:
            df: Input DataFrame
            
        Returns:
            DataFrame with cleaned column names
        """
        df.columns = [str(col).strip().upper() for col in df.columns]
        return df

    def _clean_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Clean dataframe by replacing blank strings with NaN.
        
        Args:
            df: Input DataFrame
            
        Returns:
            Cleaned DataFrame
        """
        for col in df.columns:
            if df[col].dtype == 'object':
                df[col] = df[col].replace(r'^\s*$', np.nan, regex=True)
        return df

    def _parse_date_string(self, value: any, sentinel_dates: Dict[str, str]) -> str:
        """
        Parse date string with sentinel date handling.
        
        Args:
            value: Input date value
            sentinel_dates: Dictionary of sentinel dates to replace
            
        Returns:
            Formatted date string or NaN
        """
        if pd.isna(value):
            return np.nan
        
        s = str(value).strip()
        if s == "":
            return np.nan
        
        s_upper = s.upper()
        
        # Check sentinel dates
        for sentinel, replacement in sentinel_dates.items():
            if s_upper == sentinel or s_upper.startswith(sentinel):
                return replacement
        
        # Try to parse standard date formats
        try:
            # Handle timestamp format: 28FEB2021:00:00:00
            if ":" in s:
                s = s.split(":")[0]
            
            dt = pd.to_datetime(s, format="%d%b%Y")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                dt = pd.to_datetime(s, errors="raise")
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                logger.warning(f"Could not parse date: {value}")
                return s

    def _normalize_datetime_columns(
        self,
        df: pd.DataFrame,
        columns: List[str],
        sentinel_dates: Dict[str, str]
    ) -> pd.DataFrame:
        """
        Normalize date columns in dataframe.
        
        Args:
            df: Input DataFrame
            columns: List of date column names
            sentinel_dates: Dictionary of sentinel dates
            
        Returns:
            DataFrame with normalized dates
        """
        for col in columns:
            if col in df.columns:
                df[col] = df[col].apply(
                    lambda x: self._parse_date_string(x, sentinel_dates)
                )
        return df

    def _read_source_file(
        self,
        filename: str,
        normalize_cols: bool = True,
        clean: bool = True
    ) -> Optional[pd.DataFrame]:
        """
        Read a source CSV file.
        
        Args:
            filename: Name of the file
            normalize_cols: Whether to standardize column names
            clean: Whether to clean the dataframe
            
        Returns:
            DataFrame or None if file not found
        """
        file_path = self.source_dir / filename
        
        if not file_path.exists():
            logger.warning(f"File not found: {file_path}")
            return None
        
        try:
            df = pd.read_csv(file_path, dtype=str)
            if normalize_cols:
                df = self._clean_columns(df)
            if clean:
                df = self._clean_dataframe(df)
            logger.info(f"Read {len(df)} rows from {filename}")
            return df
        except Exception as e:
            logger.error(f"Error reading {filename}: {e}")
            return None

    def _rename_columns(
        self,
        df: pd.DataFrame,
        mapping: Dict[str, str]
    ) -> pd.DataFrame:
        """
        Rename columns based on mapping.
        
        Args:
            df: Input DataFrame
            mapping: Dictionary of old_name -> new_name
            
        Returns:
            DataFrame with renamed columns
        """
        # Only rename columns that exist
        valid_mapping = {k: v for k, v in mapping.items() if k in df.columns}
        return df.rename(columns=valid_mapping)

    def transform_dimension(self, dimension_name: str) -> Tuple[bool, str]:
        """
        Transform a single dimension.
        
        Args:
            dimension_name: Name of dimension (e.g., 'customer', 'product')
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        logger.info(f"\n" + "="*80)
        logger.info(f"Starting transformation for dimension: {dimension_name}")
        logger.info("="*80)
        
        if dimension_name not in self.config['dimensions']:
            msg = f"Dimension '{dimension_name}' not found in config"
            logger.error(msg)
            return False, msg
        
        dim_config = self.config['dimensions'][dimension_name]
        
        if not dim_config.get('enabled', True):
            msg = f"Dimension '{dimension_name}' is disabled in config"
            logger.info(msg)
            return False, msg
        
        try:
            # Read source files
            master_df = self._read_source_file(dim_config['source']['master_file'])
            hierarchy_df = self._read_source_file(dim_config['source']['hierarchy_file'])
            member_df = self._read_source_file(dim_config['source']['member_file'])
            attribute_df = self._read_source_file(dim_config['source']['attribute_file'])
            metadata_df = self._read_source_file(dim_config['source']['metadata_file'])
            hier_metadata_df = self._read_source_file(dim_config['source']['hier_metadata_file'])
            
            if not member_df:
                return False, f"Required file not found: {dim_config['source']['member_file']}"
            
            # Normalize dates
            date_cols = dim_config.get('date_columns', [])
            sentinel_dates = dim_config.get('sentinel_dates', {})
            
            if member_df is not None:
                member_df = self._normalize_datetime_columns(member_df, date_cols, sentinel_dates)
            if hierarchy_df is not None:
                hierarchy_df = self._normalize_datetime_columns(hierarchy_df, date_cols, sentinel_dates)
            if attribute_df is not None:
                attribute_df = self._normalize_datetime_columns(attribute_df, date_cols, sentinel_dates)
            if metadata_df is not None:
                metadata_df = self._normalize_datetime_columns(metadata_df, date_cols, sentinel_dates)
            if hier_metadata_df is not None:
                hier_metadata_df = self._normalize_datetime_columns(hier_metadata_df, date_cols, sentinel_dates)
            
            # Build dimension_dim
            logger.info(f"Building {dimension_name}_dim...")
            dimension_dim = self._build_dimension_dim(
                member_df, hierarchy_df, attribute_df, master_df, dim_config
            )
            
            # Build hierarchy_dim
            logger.info(f"Building {dimension_name}_hierarchy_dim...")
            hierarchy_dim = self._build_hierarchy_dim(
                hierarchy_df, hier_metadata_df, dim_config
            )
            
            # Build metadata_dim
            logger.info(f"Building {dimension_name}_property_metadata_dim...")
            metadata_dim = self._build_metadata_dim(metadata_df, dim_config)
            
            # Build master_target
            logger.info(f"Building {dimension_name}_master_target...")
            master_target = dimension_dim.copy() if dimension_dim is not None else None
            
            # Write outputs
            output_files = dim_config['output_files']
            
            if dimension_dim is not None:
                output_path = self.output_dir / output_files['dimension_dim']
                dimension_dim.to_csv(output_path, index=False)
                logger.info(f"Wrote {len(dimension_dim)} rows to {output_path.name}")
                self.stats[f"{dimension_name}_dim"] = len(dimension_dim)
            
            if hierarchy_dim is not None:
                output_path = self.output_dir / output_files['hierarchy_dim']
                hierarchy_dim.to_csv(output_path, index=False)
                logger.info(f"Wrote {len(hierarchy_dim)} rows to {output_path.name}")
                self.stats[f"{dimension_name}_hierarchy_dim"] = len(hierarchy_dim)
            
            if metadata_dim is not None:
                output_path = self.output_dir / output_files['metadata_dim']
                metadata_dim.to_csv(output_path, index=False)
                logger.info(f"Wrote {len(metadata_dim)} rows to {output_path.name}")
                self.stats[f"{dimension_name}_metadata_dim"] = len(metadata_dim)
            
            if master_target is not None:
                output_path = self.output_dir / output_files['master_target']
                master_target.to_csv(output_path, index=False)
                logger.info(f"Wrote {len(master_target)} rows to {output_path.name}")
                self.stats[f"{dimension_name}_master_target"] = len(master_target)
            
            msg = f"Successfully transformed dimension: {dimension_name}"
            logger.info(msg)
            return True, msg
        
        except Exception as e:
            msg = f"Error transforming {dimension_name}: {str(e)}"
            logger.error(msg)
            import traceback
            logger.error(traceback.format_exc())
            return False, msg

    def _build_dimension_dim(
        self,
        member_df: pd.DataFrame,
        hierarchy_df: pd.DataFrame,
        attribute_df: pd.DataFrame,
        master_df: pd.DataFrame,
        config: Dict
    ) -> Optional[pd.DataFrame]:
        """
        Build the main dimension table.
        
        Args:
            member_df: Member/master data
            hierarchy_df: Hierarchy/parent-child data
            attribute_df: Attribute/property data
            master_df: Optional extra master data
            config: Dimension configuration
            
        Returns:
            DataFrame or None
        """
        if member_df is None:
            return None
        
        # Rename member columns
        member_renamed = self._rename_columns(
            member_df.copy(),
            config['master_column_mapping']
        )
        
        # Add hierarchy parent info
        if hierarchy_df is not None:
            entity_id = config['entity_keys']['entity_id_col']
            parent_id = config['entity_keys']['parent_id_col']
            
            # Build parent mapping
            hierarchy_renamed = hierarchy_df.rename(columns={
                entity_id: "DIMENSION_ID",
                parent_id: "PARENT_DIMENSION_ID"
            })
            
            parent_mapping = hierarchy_renamed[[
                "DIMENSION_ID", "PARENT_DIMENSION_ID"
            ]].drop_duplicates()
            
            member_renamed = member_renamed.merge(
                parent_mapping,
                on="DIMENSION_ID",
                how="left"
            )
        
        # Add attributes
        if attribute_df is not None:
            attr_mapping = config.get('attribute_column_mapping', {})
            attr_renamed = self._rename_columns(attribute_df.copy(), attr_mapping)
            
            # Find the dimension ID column name in attributes
            dim_id_col = None
            for orig, new in attr_mapping.items():
                if new == "DIMENSION_ID" and orig in attr_renamed.columns:
                    dim_id_col = "DIMENSION_ID"
                    break
            
            if dim_id_col:
                member_renamed = member_renamed.merge(
                    attr_renamed,
                    on="DIMENSION_ID",
                    how="left"
                )
        
        # Add master data (if present)
        if master_df is not None:
            master_cols = master_df.columns.tolist()
            if 'CUSTOMER_ID' in master_cols:
                master_renamed = master_df.rename(columns={'CUSTOMER_ID': 'DIMENSION_ID'})
                member_renamed = member_renamed.merge(
                    master_renamed,
                    on="DIMENSION_ID",
                    how="left"
                )
        
        # Dedup based on config
        dedup_key = config.get('dedup_key', ['DIMENSION_ID'])
        member_renamed = member_renamed.drop_duplicates(subset=dedup_key).reset_index(drop=True)
        
        return member_renamed

    def _build_hierarchy_dim(
        self,
        hierarchy_df: pd.DataFrame,
        hier_metadata_df: pd.DataFrame,
        config: Dict
    ) -> Optional[pd.DataFrame]:
        """
        Build the hierarchy dimension table.
        
        Args:
            hierarchy_df: Hierarchy data
            hier_metadata_df: Hierarchy metadata
            config: Dimension configuration
            
        Returns:
            DataFrame or None
        """
        if hierarchy_df is None:
            return None
        
        hierarchy_renamed = self._rename_columns(
            hierarchy_df.copy(),
            config.get('hierarchy_column_mapping', {})
        )
        
        # Add metadata if available
        if hier_metadata_df is not None:
            metadata_cols = [
                col for col in hier_metadata_df.columns
                if col not in hierarchy_renamed.columns
            ]
            
            join_key = config['entity_keys']['assoc_type_col']
            if join_key in hier_metadata_df.columns and join_key in hierarchy_renamed.columns:
                hierarchy_renamed = hierarchy_renamed.merge(
                    hier_metadata_df,
                    on=join_key,
                    how="left"
                )
        
        hierarchy_renamed = hierarchy_renamed.drop_duplicates().reset_index(drop=True)
        return hierarchy_renamed

    def _build_metadata_dim(
        self,
        metadata_df: pd.DataFrame,
        config: Dict
    ) -> Optional[pd.DataFrame]:
        """
        Build the property metadata table.
        
        Args:
            metadata_df: Metadata data
            config: Dimension configuration
            
        Returns:
            DataFrame or None
        """
        if metadata_df is None:
            return None
        
        metadata = metadata_df.drop_duplicates().reset_index(drop=True)
        return metadata

    def transform_all(self) -> Dict[str, Tuple[bool, str]]:
        """
        Transform all enabled dimensions.
        
        Returns:
            Dictionary of results by dimension name
        """
        results = {}
        
        for dimension_name, dim_config in self.config['dimensions'].items():
            if dim_config.get('enabled', True):
                success, message = self.transform_dimension(dimension_name)
                results[dimension_name] = (success, message)
        
        return results

    def print_summary(self):
        """
        Print transformation summary.
        """
        logger.info(f"\n" + "="*80)
        logger.info("TRANSFORMATION SUMMARY")
        logger.info("="*80)
        for key, count in self.stats.items():
            logger.info(f"{key}: {count} rows")
        logger.info("="*80)


def main():
    """
    Main entry point for the transformation.
    """
    config_path = "config/dimension_config.yaml"
    source_dir = "input"
    output_dir = "output"
    
    transformer = DimensionTransformer(
        config_path=config_path,
        source_dir=source_dir,
        output_dir=output_dir
    )
    
    results = transformer.transform_all()
    
    logger.info(f"\n" + "="*80)
    logger.info("TRANSFORMATION RESULTS")
    logger.info("="*80)
    for dimension, (success, message) in results.items():
        status = "✓ SUCCESS" if success else "✗ FAILED"
        logger.info(f"{status}: {dimension} - {message}")
    
    transformer.print_summary()
    
    logger.info(f"\nOutput files generated in: {output_dir}")
    output_path = Path(output_dir)
    for file in sorted(output_path.glob("*.csv")):
        logger.info(f"  - {file.name}")


if __name__ == "__main__":
    main()
