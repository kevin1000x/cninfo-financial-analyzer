"""
Complete Example: Financial Report Analysis Pipeline

This script demonstrates the full workflow from download to TNI calculation.

Usage:
    python examples/full_analysis_example.py
"""

import os
import sys
import pandas as pd
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.pipeline import FinancialAnalysisPipeline
from src.utils import load_config, create_timestamp
from loguru import logger


def analyze_parsed_report_like_pipeline(pipeline, parse_result):
    """
    Manually analyze one parsed report with the same text-selection guardrails
    used by the official batch and streaming pipeline paths.
    """
    analysis_text, text_source = pipeline.select_analysis_text(parse_result)
    analysis = pipeline.analyzer.analyze_text(
        analysis_text,
        parse_result.get('file_path', '')
    )
    analysis['analysis_text_source'] = text_source
    return analysis


def main():
    """
    Run complete financial report analysis
    """

    print("=" * 70)
    print("CNINFO Financial Report Analysis - Complete Example")
    print("=" * 70)
    print()

    # Configuration
    config_path = 'config.yaml'
    sentiment_dict_path = 'data/dictionaries/cn_financial_sentiment.txt'
    company_list_path = 'examples/company_list.csv'
    financial_data_path = 'examples/financial_data.csv'

    # Check if sentiment dictionary exists
    if not os.path.exists(sentiment_dict_path):
        print(f"⚠️  Sentiment dictionary not found at: {sentiment_dict_path}")
        print("Creating a minimal example dictionary...")

        os.makedirs(os.path.dirname(sentiment_dict_path), exist_ok=True)
        with open(sentiment_dict_path, 'w', encoding='utf-8') as f:
            f.write("# Minimal Sentiment Dictionary\n")
            f.write("POS:增长\nPOS:盈利\nPOS:提升\nPOS:优化\n")
            f.write("NEG:下降\nNEG:亏损\nNEG:风险\nNEG:减少\n")

        print("✓ Created minimal dictionary")
        print()

    # Step 1: Initialize Pipeline
    print("Step 1: Initializing pipeline...")
    try:
        pipeline = FinancialAnalysisPipeline(
            config_path=config_path,
            sentiment_dict_path=sentiment_dict_path
        )
        print("✓ Pipeline initialized successfully")
    except Exception as e:
        print(f"✗ Failed to initialize pipeline: {e}")
        return

    print()

    # Step 2: Load Company List
    print("Step 2: Loading company list...")
    try:
        companies = pd.read_csv(company_list_path)
        print(f"✓ Loaded {len(companies)} companies")
        print(f"  Companies: {', '.join(companies['company_name'].head(3).tolist())}...")
    except Exception as e:
        print(f"✗ Failed to load companies: {e}")
        return

    print()

    # Step 3: Define Analysis Parameters
    print("Step 3: Setting analysis parameters...")
    years = [2020, 2021, 2022]
    report_types = ['annual']  # Can add 'semi_annual', 'quarterly'

    print(f"  Years: {years}")
    print(f"  Report types: {report_types}")
    print()

    # Step 4: Run Pipeline
    print("Step 4: Running analysis pipeline...")
    print("  This may take a while depending on network speed...")
    print()

    try:
        results = pipeline.run(
            company_csv=company_list_path,
            years=years,
            report_types=report_types,
            financial_data_csv=financial_data_path,
            skip_download=False,  # Set to True if files already downloaded
            skip_parse=False  # Set to True if PDFs already parsed
        )

        print()
        print("✓ Analysis completed successfully!")
        print()

    except Exception as e:
        print(f"✗ Pipeline failed: {e}")
        logger.exception("Pipeline error")
        return

    # Step 5: Display Results Summary
    print("=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    print()

    if results is not None and len(results) > 0:
        # Basic statistics
        print(f"Total observations: {len(results)}")
        print()

        # Analysis text source distribution
        if 'analysis_text_source' in results.columns:
            print("Analysis Text Source:")
            print(results['analysis_text_source'].value_counts().to_string())
            print()

        # Tone statistics
        if 'tone_raw' in results.columns:
            tone_stats = results['tone_raw'].describe()
            print("Tone Statistics:")
            print(f"  Mean:   {tone_stats['mean']:.4f}")
            print(f"  Std:    {tone_stats['std']:.4f}")
            print(f"  Min:    {tone_stats['min']:.4f}")
            print(f"  Max:    {tone_stats['max']:.4f}")
            print(f"  Median: {tone_stats['50%']:.4f}")
            print()

        # Readability statistics
        if 'fog_index' in results.columns:
            fog_stats = results['fog_index'].describe()
            print("Fog Index (Readability) Statistics:")
            print(f"  Mean:   {fog_stats['mean']:.2f}")
            print(f"  Std:    {fog_stats['std']:.2f}")
            print(f"  Min:    {fog_stats['min']:.2f}")
            print(f"  Max:    {fog_stats['max']:.2f}")
            print()

        # TNI statistics
        if 'tni' in results.columns:
            tni_stats = results['tni'].dropna().describe()
            print("TNI Statistics:")
            print(f"  Mean:   {tni_stats['mean']:.4f}")
            print(f"  Std:    {tni_stats['std']:.4f}")
            print(f"  Min:    {tni_stats['min']:.4f}")
            print(f"  Max:    {tni_stats['max']:.4f}")
            print()

            # TNI interpretation
            positive_tni = (results['tni'] > 0).sum()
            negative_tni = (results['tni'] < 0).sum()
            print(f"  Positive TNI: {positive_tni} ({positive_tni / len(results) * 100:.1f}%)")
            print(f"  Negative TNI: {negative_tni} ({negative_tni / len(results) * 100:.1f}%)")
            print()

        # Top 5 by Tone
        if 'tone_raw' in results.columns:
            print("Top 5 Most Positive Reports (by Tone):")
            top_positive = results.nlargest(5, 'tone_raw')[
                ['stock_code', 'company_name', 'year', 'tone_raw', 'analysis_text_source']
            ]
            print(top_positive.to_string(index=False))
            print()

            print("Top 5 Most Negative Reports (by Tone):")
            top_negative = results.nsmallest(5, 'tone_raw')[
                ['stock_code', 'company_name', 'year', 'tone_raw', 'analysis_text_source']
            ]
            print(top_negative.to_string(index=False))
            print()

        # High TNI observations (innovation narrative)
        if 'tni' in results.columns:
            high_tni = results[results['tni'] > 1.0]
            if len(high_tni) > 0:
                print(f"High TNI Observations (>1.0): {len(high_tni)}")
                print("  (Positive tone despite declining performance)")
                print(high_tni[['stock_code', 'year', 'tone_raw', 'perf_score', 'tni']].head())
                print()

    else:
        print("⚠️  No results generated")

    # Step 6: Output Files
    print("=" * 70)
    print("OUTPUT FILES")
    print("=" * 70)
    print()

    results_dir = 'data/results'
    if os.path.exists(results_dir):
        result_files = list(Path(results_dir).glob('master_summary_*.xlsx'))
        if result_files:
            latest_file = max(result_files, key=os.path.getctime)
            print(f"📊 Master Summary: {latest_file}")
            print(f"   File size: {os.path.getsize(latest_file) / 1024:.1f} KB")
            print()

    print("📁 Other outputs:")
    print(f"   Raw PDFs: data/raw/")
    print(f"   Parsed text: data/parsed/")
    print(f"   Logs: logs/analyzer.log")
    print()

    # Step 7: Next Steps
    print("=" * 70)
    print("NEXT STEPS")
    print("=" * 70)
    print()
    print("1. Review the master summary Excel file")
    print("2. Import results into statistical software (Stata, R, Python)")
    print("3. Perform regression analysis with TNI as dependent variable")
    print("4. Check logs/analyzer.log for any warnings or errors")
    print("5. Validate results by spot-checking a few reports manually")
    print()

    # Example: Export for R/Stata
    if results is not None and len(results) > 0:
        print("Example exports:")

        # CSV for R
        csv_path = f'data/results/results_for_r_{create_timestamp()}.csv'
        results.to_csv(csv_path, index=False)
        print(f"  ✓ CSV for R: {csv_path}")

        # Stata format
        try:
            stata_path = f'data/results/results_for_stata_{create_timestamp()}.dta'
            results.to_stata(stata_path, write_index=False)
            print(f"  ✓ Stata format: {stata_path}")
        except Exception:
            print("  ℹ Install pandas with stata support: pip install pandas[stata]")

        print()

    print("=" * 70)
    print("Analysis complete! 🎉")
    print("=" * 70)


if __name__ == '__main__':
    main()
