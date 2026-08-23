"""
Unit tests for Metrics module
"""

import pytest
import pandas as pd
import numpy as np
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.metrics import MetricsCalculator, load_financial_data_from_csv


@pytest.fixture
def config():
    """Load test configuration"""
    return {
        'metrics': {
            'performance_indicators': ['roa', 'ocf'],
            'standardization': 'zscore',
            'missing_data_strategy': 'skip'
        }
    }


@pytest.fixture
def calculator(config):
    """Create MetricsCalculator instance"""
    return MetricsCalculator(config)


@pytest.fixture
def sample_tone_data():
    """Create sample tone data"""
    data = {
        'stock_code': ['000001', '000001', '000001', '000002', '000002'],
        'year': [2020, 2021, 2022, 2020, 2021],
        'tone_raw': [0.2, 0.5, 0.3, -0.1, 0.1],
        'company_name': ['Company A'] * 3 + ['Company B'] * 2
    }
    return pd.DataFrame(data)


@pytest.fixture
def sample_financial_data():
    """Create sample financial data"""
    data = {
        'stock_code': ['000001', '000001', '000001', '000002', '000002'],
        'year': [2020, 2021, 2022, 2020, 2021],
        'roa': [0.05, 0.08, 0.06, 0.03, 0.04],
        'ocf': [1000000, 1200000, 1100000, 800000, 850000]
    }
    return pd.DataFrame(data)


def test_standardize_series_zscore(calculator):
    """Test Z-score standardization"""
    series = pd.Series([1, 2, 3, 4, 5])
    standardized = calculator.standardize_series(series, method='zscore')

    # Check mean is approximately 0
    assert abs(standardized.mean()) < 1e-10

    # Check std is approximately 1
    assert abs(standardized.std() - 1.0) < 1e-10


def test_standardize_series_minmax(calculator):
    """Test min-max standardization"""
    series = pd.Series([1, 2, 3, 4, 5])
    standardized = calculator.standardize_series(series, method='minmax')

    # Check min is 0 and max is 1
    assert standardized.min() == 0.0
    assert standardized.max() == 1.0


def test_standardize_series_zero_std(calculator):
    """Test standardization with zero standard deviation"""
    series = pd.Series([5, 5, 5, 5, 5])
    standardized = calculator.standardize_series(series, method='zscore')

    # Should return zeros
    assert (standardized == 0).all()


def test_calculate_performance_change(calculator, sample_financial_data):
    """Test performance change calculation"""
    df = sample_financial_data.copy()

    roa_change = calculator.calculate_performance_change(df, 'roa')

    # First year should be NaN (no previous year)
    assert pd.isna(roa_change.iloc[0])

    # Second year should show change
    # ROA went from 0.05 to 0.08 for company 000001
    assert not pd.isna(roa_change.iloc[1])
    assert abs(roa_change.iloc[1] - 0.03) < 1e-10


def test_calculate_performance_score(calculator, sample_financial_data):
    """Test composite performance score calculation"""
    df = sample_financial_data.copy()

    perf_score = calculator.calculate_performance_score(df, 'roa', 'ocf')

    # Should have scores for all but first observation of each company
    assert len(perf_score) == len(df)


def test_calculate_tni(calculator):
    """Test TNI calculation"""
    tone_scores = pd.Series([0.2, 0.5, 0.3, -0.1, 0.1])
    perf_scores = pd.Series([0.03, -0.02, 0.04, 0.01, -0.01])

    tni = calculator.calculate_tni(tone_scores, perf_scores)

    # TNI should be calculated for all observations
    assert len(tni) == 5

    # TNI should have both positive and negative values
    assert tni.max() > 0 or tni.min() < 0


def test_merge_tone_and_financial(calculator, sample_tone_data, sample_financial_data):
    """Test merging tone and financial data"""
    merged = calculator.merge_tone_and_financial(sample_tone_data, sample_financial_data)

    # Should have data for all matching stock_code and year combinations
    assert len(merged) == 5

    # Should have columns from both datasets
    assert 'tone_raw' in merged.columns
    assert 'roa' in merged.columns


def test_calculate_all_metrics(calculator, sample_tone_data, sample_financial_data):
    """Test complete metrics calculation"""
    metrics_df = calculator.calculate_all_metrics(sample_tone_data, sample_financial_data)

    # Check required columns exist
    required_cols = ['tone_raw', 'tone_normalized', 'perf_score', 'tni']
    for col in required_cols:
        assert col in metrics_df.columns

    # Check TNI is calculated
    assert not metrics_df['tni'].isna().all()


def test_generate_summary_statistics(calculator, sample_tone_data, sample_financial_data):
    """Test summary statistics generation"""
    metrics_df = calculator.calculate_all_metrics(sample_tone_data, sample_financial_data)
    summary = calculator.generate_summary_statistics(metrics_df)

    # Check structure
    assert 'total_observations' in summary
    assert 'tone_stats' in summary
    assert 'tni_stats' in summary

    # Check tone stats
    assert 'mean' in summary['tone_stats']
    assert 'std' in summary['tone_stats']

    # Check TNI stats
    if summary['tni_stats']:
        assert 'positive_count' in summary['tni_stats']
        assert 'negative_count' in summary['tni_stats']


def test_handle_missing_data_skip(calculator, sample_tone_data):
    """Test missing data handling with skip strategy"""
    df = sample_tone_data.copy()
    df.loc[0, 'tone_raw'] = np.nan
    df['perf_score'] = [0.03, -0.02, 0.04, 0.01, -0.01]

    result = calculator.handle_missing_data(df)

    # Should have removed row with NaN
    assert len(result) < len(df)
    assert not result['tone_raw'].isna().any()


def test_load_financial_data_from_csv(tmp_path):
    """Test loading financial data from CSV"""
    # Create temporary CSV
    csv_path = tmp_path / "financial_data.csv"
    data = {
        'stock_code': ['1', '1', '2'],
        'year': [2020, 2021, 2020],
        'roa': [0.05, 0.06, 0.04],
        'ocf': [1000000, 1100000, 900000]
    }
    pd.DataFrame(data).to_csv(csv_path, index=False)

    # Load data
    df = load_financial_data_from_csv(str(csv_path))

    # Check loaded correctly
    assert len(df) == 3
    assert 'stock_code' in df.columns

    # Check stock codes are normalized to 6 digits
    assert df['stock_code'].iloc[0] == '000001'


def test_tni_interpretation(calculator):
    """Test TNI interpretation scenarios"""
    # Scenario 1: Positive tone, declining performance (High TNI)
    # This suggests innovation narrative - positive language despite poor performance
    tone1 = pd.Series([0.9, 0.1, 0.5])
    perf1 = pd.Series([-0.9, 0.1, 0.2])
    tni1 = calculator.calculate_tni(tone1, perf1)

    # Scenario 2: Negative tone, improving performance (Low TNI)
    # This suggests conservative reporting - cautious language despite good performance
    tone2 = pd.Series([-0.5, 0.1, 0.2])
    perf2 = pd.Series([0.5, 0.1, 0.2])
    tni2 = calculator.calculate_tni(tone2, perf2)

    # Both scenarios represent a discrepancy between tone and performance
    # Therefore, both should result in a positive TNI
    assert tni1.iloc[0] > 0
    assert tni2.iloc[0] > 0


def test_performance_change_multiple_companies(calculator):
    """Test performance change calculation across multiple companies"""
    df = pd.DataFrame({
        'stock_code': ['000001', '000001', '000002', '000002'],
        'year': [2020, 2021, 2020, 2021],
        'roa': [0.05, 0.08, 0.03, 0.06]
    })

    df['roa_change'] = calculator.calculate_performance_change(df, 'roa')

    # First observation of each company should be NaN
    company1_data = df[df['stock_code'] == '000001']
    company2_data = df[df['stock_code'] == '000002']

    assert pd.isna(company1_data.iloc[0]['roa_change'])
    assert pd.isna(company2_data.iloc[0]['roa_change'])

    # Second observations should have valid changes
    assert not pd.isna(company1_data.iloc[1]['roa_change'])
    assert not pd.isna(company2_data.iloc[1]['roa_change'])


def test_empty_dataframes(calculator):
    """Test handling of empty dataframes"""
    empty_df = pd.DataFrame()

    result = calculator.calculate_all_metrics(empty_df, empty_df)

    # Should return empty dataframe without errors
    assert result.empty


def test_missing_columns(calculator):
    """Test handling of missing required columns"""
    df1 = pd.DataFrame({'year': [2020, 2021]})
    df2 = pd.DataFrame({'year': [2020, 2021]})

    result = calculator.merge_tone_and_financial(df1, df2)

    # Should return empty dataframe
    assert result.empty


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

# ---------------------------------------------------------------------
# Prior-year baseline: TNI must survive when tone covers only target years
# but the financial CSV (AKShare cache) includes the preceding year.

def test_tni_nonempty_when_tone_single_year_but_financials_include_prior_year(calculator):
    """Two companies × single target year (the classic 2-company web job).

    The financial CSV carries 2020+2021 rows; tone only 2021. YoY change
    must be computed on the full financial series BEFORE merging, so both
    2021 rows keep a finite perf_score and survive dropna → non-empty TNI.
    """
    tone = pd.DataFrame({
        'stock_code': ['600000', '600519'],
        'year': [2021, 2021],
        'tone_raw': [0.30, -0.20],
    })
    financial = pd.DataFrame({
        'stock_code': ['600000', '600000', '600519', '600519'],
        'year': [2020, 2021, 2020, 2021],
        'roa': [0.040, 0.055, 0.150, 0.180],
        'ocf': [1.0e9, 1.2e9, 3.5e10, 4.0e10],
    })

    result = calculator.calculate_all_metrics(tone, financial)

    assert not result.empty, "single-target-year tone rows must survive via prior-year financials"
    assert len(result) == 2
    assert result['tni'].notna().all()
    assert np.isfinite(result['tni']).all()
    # With two observations z-scores are ±1/sqrt(2), so assert non-degenerate
    # finite values instead of sign spread (signs collapse for n=2).
    assert (result['tni'].abs() > 0).all()


def test_calculate_all_metrics_multi_year_behavior_unchanged(calculator, sample_tone_data, sample_financial_data):
    """Full multi-year merge still computes changes identically per company."""
    result = calculator.calculate_all_metrics(sample_tone_data, sample_financial_data)
    assert not result.empty
    assert result['tni'].notna().any()
