"""
Metrics Module
Calculates TNI (Tone-Normalized Innovation) and performance metrics

Reference: Financial research methodology for tone vs. performance analysis
"""

import pandas as pd
from typing import Dict
from loguru import logger


class MetricsCalculator:
    """
    Calculator for financial performance and TNI metrics
    """

    def __init__(self, config: Dict):
        """
        Initialize metrics calculator

        Args:
            config: Configuration dictionary
        """
        self.config = config['metrics']
        self.performance_indicators = self.config.get('performance_indicators', ['roa', 'ocf'])
        self.standardization = self.config.get('standardization', 'zscore')
        self.missing_strategy = self.config.get('missing_data_strategy', 'skip')

        logger.info(f"Metrics Calculator initialized: {self.performance_indicators}")

    def standardize_series(self, series: pd.Series, method: str = 'zscore') -> pd.Series:
        """
        Standardize a series using specified method

        Args:
            series: Input series
            method: 'zscore' or 'minmax'

        Returns:
            Standardized series
        """
        if method == 'zscore':
            # Z-score normalization: (x - mean) / std
            mean = series.mean()
            std = series.std()

            if std == 0:
                logger.warning("Standard deviation is 0, returning zeros")
                return pd.Series(0, index=series.index)

            standardized = (series - mean) / std

        elif method == 'minmax':
            # Min-max normalization: (x - min) / (max - min)
            min_val = series.min()
            max_val = series.max()

            if max_val == min_val:
                logger.warning("Min equals max, returning zeros")
                return pd.Series(0, index=series.index)

            standardized = (series - min_val) / (max_val - min_val)

        else:
            raise ValueError(f"Unknown standardization method: {method}")

        return standardized

    def calculate_performance_change(self,
                                     financial_data: pd.DataFrame,
                                     metric: str = 'roa') -> pd.Series:
        """
        Calculate year-over-year change in performance metric

        Args:
            financial_data: DataFrame with columns [stock_code, year, metric]
            metric: Performance metric to calculate change for

        Returns:
            Series of metric changes
        """
        if metric not in financial_data.columns:
            logger.error(f"Metric '{metric}' not found in financial data")
            return pd.Series()

        # Sort by stock_code and year
        df = financial_data.sort_values(['stock_code', 'year']).copy()

        # Calculate change within each company
        df[f'{metric}_change'] = df.groupby('stock_code')[metric].diff()

        logger.debug(f"Calculated {metric} changes")

        return df[f'{metric}_change']

    def calculate_performance_score(self,
                                    financial_data: pd.DataFrame,
                                    primary_metric: str = 'roa',
                                    secondary_metric: str = 'ocf') -> pd.Series:
        """
        Calculate composite performance score

        Uses primary metric (ROA change) if available,
        falls back to secondary (OCF change) if missing

        Args:
            financial_data: DataFrame with financial metrics
            primary_metric: Primary performance metric
            secondary_metric: Fallback metric

        Returns:
            Series of performance scores
        """
        df = financial_data.copy()

        # Calculate changes for both metrics
        primary_change_col = f'{primary_metric}_change'
        secondary_change_col = f'{secondary_metric}_change'

        # Calculate changes
        if primary_metric in df.columns:
            df[primary_change_col] = self.calculate_performance_change(df, primary_metric)

        if secondary_metric in df.columns:
            df[secondary_change_col] = self.calculate_performance_change(df, secondary_metric)

        # Use primary, fallback to secondary
        if primary_change_col in df.columns:
            perf_score = df[primary_change_col].fillna(df.get(secondary_change_col, 0))
        else:
            perf_score = df.get(secondary_change_col, pd.Series(0, index=df.index))

        logger.info(f"Calculated performance scores using {primary_metric} and {secondary_metric}")

        return perf_score

    def calculate_tni(self,
                      tone_scores: pd.Series,
                      performance_scores: pd.Series) -> pd.Series:
        """
        Calculate Tone-Normalized Innovation (TNI)

        Formula:
            TNI = Standardized_Tone × (-1) × Standardized_Performance

        Interpretation:
        - High TNI: Positive tone but declining performance (potential innovation narrative)
        - Low TNI: Negative tone but improving performance (conservative reporting)

        Args:
            tone_scores: Series of raw tone scores
            performance_scores: Series of performance change scores

        Returns:
            Series of TNI scores
        """
        # Ensure series are aligned
        common_index = tone_scores.index.intersection(performance_scores.index)
        tone_aligned = tone_scores.loc[common_index]
        perf_aligned = performance_scores.loc[common_index]

        # Standardize both series
        tone_standardized = self.standardize_series(tone_aligned, self.standardization)
        perf_standardized = self.standardize_series(perf_aligned, self.standardization)

        # Calculate TNI
        # TNI = Tone_Z × (-1) × Perf_Z
        tni = tone_standardized * (-1) * perf_standardized

        logger.info(f"Calculated TNI for {len(tni)} observations")
        logger.debug(f"TNI range: [{tni.min():.3f}, {tni.max():.3f}]")

        return tni

    def merge_tone_and_financial(self,
                                 tone_results: pd.DataFrame,
                                 financial_data: pd.DataFrame) -> pd.DataFrame:
        """
        Merge tone analysis results with financial data

        Args:
            tone_results: DataFrame with tone analysis results
            financial_data: DataFrame with financial metrics

        Returns:
            Merged DataFrame
        """
        # Ensure key columns exist
        tone_key = ['stock_code', 'year']
        financial_key = ['stock_code', 'year']

        if not all(col in tone_results.columns for col in tone_key):
            logger.error("Tone results missing required columns")
            return pd.DataFrame()

        if not all(col in financial_data.columns for col in financial_key):
            logger.error("Financial data missing required columns")
            return pd.DataFrame()

        # Merge on stock_code and year
        merged = pd.merge(
            tone_results,
            financial_data,
            on=['stock_code', 'year'],
            how='left',
            suffixes=('', '_financial')
        )

        logger.info(f"Merged data: {len(merged)} rows")

        return merged

    def calculate_all_metrics(self,
                              tone_results: pd.DataFrame,
                              financial_data: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate all metrics including TNI

        Args:
            tone_results: DataFrame with tone analysis results
                Required columns: stock_code, year, tone_raw
            financial_data: DataFrame with financial metrics
                Required columns: stock_code, year, roa, ocf (optional)

        Returns:
            DataFrame with all calculated metrics
        """
        # Performance changes must be computed on the FULL financial series
        # (all years available for each company) BEFORE merging with tone
        # results. Tone results typically cover only the target years; if the
        # merge happened first, every company's first row would lose its
        # prior-year baseline and its YoY change would be NaN.
        financial = financial_data.copy()
        for metric in self.performance_indicators:
            if metric in financial.columns:
                change_col = f'{metric}_change'
                financial[change_col] = self.calculate_performance_change(financial, metric)

        financial['perf_score'] = self.calculate_performance_score(
            financial,
            primary_metric='roa',
            secondary_metric='ocf'
        )

        # Merge datasets
        df = self.merge_tone_and_financial(tone_results, financial)

        if df.empty:
            logger.error("Failed to merge data")
            return pd.DataFrame()

        # Handle missing data according to configured strategy
        df = self.handle_missing_data(df)

        # Standardize tone scores
        df['tone_normalized'] = self.standardize_series(
            df['tone_raw'],
            self.standardization
        )

        # Standardize performance scores
        df['perf_score_normalized'] = self.standardize_series(
            df['perf_score'],
            self.standardization
        )

        # Calculate TNI
        df['tni'] = self.calculate_tni(
            df['tone_raw'],
            df['perf_score']
        )

        logger.info(f"Calculated all metrics for {len(df)} observations")

        return df

    def generate_summary_statistics(self, metrics_df: pd.DataFrame) -> Dict:
        """
        Generate summary statistics for calculated metrics

        Args:
            metrics_df: DataFrame with calculated metrics

        Returns:
            Dictionary of summary statistics
        """
        summary = {
            'total_observations': len(metrics_df),
            'tone_stats': {},
            'performance_stats': {},
            'tni_stats': {}
        }

        # Tone statistics
        if 'tone_raw' in metrics_df.columns:
            tone = metrics_df['tone_raw'].dropna()
            summary['tone_stats'] = {
                'mean': tone.mean(),
                'std': tone.std(),
                'min': tone.min(),
                'max': tone.max(),
                'median': tone.median()
            }

        # Performance statistics
        if 'perf_score' in metrics_df.columns:
            perf = metrics_df['perf_score'].dropna()
            summary['performance_stats'] = {
                'mean': perf.mean(),
                'std': perf.std(),
                'min': perf.min(),
                'max': perf.max(),
                'median': perf.median()
            }

        # TNI statistics
        if 'tni' in metrics_df.columns:
            tni = metrics_df['tni'].dropna()
            summary['tni_stats'] = {
                'mean': tni.mean(),
                'std': tni.std(),
                'min': tni.min(),
                'max': tni.max(),
                'median': tni.median(),
                'positive_count': (tni > 0).sum(),
                'negative_count': (tni < 0).sum()
            }

        return summary

    def handle_missing_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Handle missing data according to configured strategy

        Args:
            df: DataFrame with potential missing values

        Returns:
            DataFrame with handled missing values
        """
        if self.missing_strategy == 'skip':
            # Remove rows with missing key values
            df = df.dropna(subset=['tone_raw', 'perf_score'])
            logger.info(f"Dropped rows with missing data: {len(df)} remaining")

        elif self.missing_strategy == 'interpolate':
            # Interpolate within each company
            df = df.groupby('stock_code').apply(
                lambda x: x.interpolate(method='linear')
            ).reset_index(drop=True)
            logger.info("Interpolated missing data")

        elif self.missing_strategy == 'forward_fill':
            # Forward fill within each company
            df = df.groupby('stock_code').fillna(method='ffill')
            logger.info("Forward filled missing data")

        return df


def load_financial_data_from_csv(csv_path: str) -> pd.DataFrame:
    """
    Load financial data from CSV file

    Expected columns: stock_code, year, roa, ocf, etc.

    Args:
        csv_path: Path to CSV file

    Returns:
        DataFrame with financial data
    """
    try:
        df = pd.read_csv(csv_path, encoding='utf-8-sig')

        # Normalize column names
        df.columns = df.columns.str.lower().str.strip()

        # Ensure required columns exist
        required = ['stock_code', 'year']
        if not all(col in df.columns for col in required):
            logger.error(f"CSV missing required columns: {required}")
            return pd.DataFrame()

        # Normalize stock codes
        df['stock_code'] = df['stock_code'].apply(lambda x: str(x).zfill(6))

        logger.info(f"Loaded financial data: {len(df)} rows")

        return df

    except Exception as e:
        logger.error(f"Failed to load financial data: {e}")
        return pd.DataFrame()
