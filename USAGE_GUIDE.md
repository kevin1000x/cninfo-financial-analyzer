# CNINFO 金融分析器 — 使用指南

本指南为使用 CNINFO Financial Analyzer 工具包提供详细示例。

## 目录

1. [安装](#安装)
2. [快速开始](#快速开始)
3. [组件逐项使用](#组件逐项使用)
4. [高级用法](#高级用法)
5. [常见工作流](#常见工作流)
6. [故障排查](#故障排查)

---

## 安装

### 基本安装

```bash
# Clone repository
git clone https://github.com/kevin1000x/cninfo-financial-analyzer
cd cninfo-financial-analyzer

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install package
pip install -e .
```

### 完整安装（包含所有可选依赖）

```bash
pip install -e ".[full,dev]"

# 如需 Selenium / Playwright 登录辅助
pip install -e ".[automation]"
```

### OCR 的 Tesseract（可选）

```bash
# Ubuntu/Debian
sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim

# macOS
brew install tesseract tesseract-lang

# Windows: Download from https://github.com/UB-Mannheim/tesseract/wiki
```

---

## 快速开始

### 1. 基本分析流水线

```python
from src.pipeline import FinancialAnalysisPipeline

# 初始化管道
pipeline = FinancialAnalysisPipeline(
    config_path='config.yaml',
    sentiment_dict_path='data/dictionaries/cn_financial_sentiment.txt'
)

# 运行完整分析
results = pipeline.run(
    company_csv='examples/company_list.csv',
    years=[2020, 2021, 2022],
    report_types=['annual'],
    financial_data_csv='examples/financial_data.csv'
)

# 结果会自动保存到 data/results/
print(f"Analyzed {len(results)} reports")
```

### 2. 命令行界面

```bash
# Full analysis
python -m src.pipeline analyze \
    --companies examples/company_list.csv \
    --years 2020-2022 \
    --financial-data examples/financial_data.csv

# Download only
python -m src.pipeline download \
    --companies examples/company_list.csv \
    --years 2020,2021,2022 \
    --types annual,semi_annual

# Parse existing PDFs
python -m src.pipeline parse \
    --input data/raw \
    --output data/parsed

# Reuse the latest saved analysis intermediate
python -m src.pipeline analyze \
    --companies examples/company_list.csv \
    --years 2020-2022 \
    --skip-analyze

# Restore latest parse_results and rerun analysis
python -m src.pipeline analyze \
    --companies examples/company_list.csv \
    --years 2020-2022 \
    --restore-parse-results

# Restore a specific analysis_results pickle
python -m src.pipeline analyze \
    --companies examples/company_list.csv \
    --years 2020-2022 \
    --restore-analysis-results data/results/intermediate/analysis_results_20240101_120000.pkl

# Confirm work completion and clean transient cache while keeping audit artifacts
python -m src.pipeline finish-work --confirm-work-complete
```

---

## 组件逐项使用

### A. 下载器（Downloader）

#### 基本下载

```python
from src.downloader import CNINFODownloader
from src.utils import load_config

config = load_config('config.yaml')
downloader = CNINFODownloader(config)

# 下载报告
metadata = downloader.download_reports(
    company_codes=['000001', '600000'],
    years=[2020, 2021],
    report_types=['annual', 'semi_annual']
)

print(f"Downloaded {downloader.stats['success']} files")
```

#### 使用 Cookies（用于需要认证的情况）

```python
cookies = {
    'JSESSIONID': 'your_session_id_from_browser',
    # 根据需要添加其他 Cookies
}

downloader = CNINFODownloader(config, cookies=cookies)
```

#### 使用 Selenium（处理验证码）

```python
from src.downloader import SeleniumDownloader

# 先安装：pip install -e ".[automation]"

downloader = SeleniumDownloader(
    headless=False,  # 显示浏览器以便手动处理验证码
    wait_for_manual_captcha=True
)

cookies = downloader.download_with_login(
    username='your_username',
    password='your_password',
    wait_seconds=60
)
```

### B. PDF 解析器（PDF Parser）

#### 解析单个 PDF

```python
from src.pdf_parser import PDFParser

config = load_config('config.yaml')
parser = PDFParser(config)

# 解析 PDF
result = parser.parse_pdf('data/raw/000001/2020/annual_report.pdf')

print(f"Extracted {len(result['text'])} characters")
print(f"Found {len(result['tables'])} tables")
print(f"Financial statements: {list(result['financial_statements'].keys())}")
```

#### 批量解析

```python
import glob

pdf_files = glob.glob('data/raw/**/*.pdf', recursive=True)
results = parser.batch_parse(pdf_files)

print(f"Successfully parsed {len(results)} PDFs")
```

#### 对扫描版 PDF 启用 OCR

```python
# 更新配置
config['parser']['use_ocr'] = True
config['parser']['ocr_language'] = 'chi_sim'

parser = PDFParser(config)
result = parser.parse_pdf('scanned_report.pdf')
```

### C. 文本分析器（Text Analyzer）

#### 计算语调（Tone）

```python
from src.text_analyzer import TextAnalyzer

analyzer = TextAnalyzer(
    config,
    sentiment_dict_path='data/dictionaries/cn_financial_sentiment.txt'
)

text = """
公司本年度实现营业收入大幅增长，净利润持续提升。
管理层认为，核心竞争力不断增强，市场份额稳步扩大。
公司将继续优化业务结构，提高运营效率。
"""

tone_result = analyzer.calculate_tone(text)

print(f"Positive words: {tone_result['pos_word_count']}")
print(f"Negative words: {tone_result['neg_word_count']}")
print(f"Tone score: {tone_result['tone_raw']:.4f}")
```

（上面示例中的自然语言文本用于展示语调计算；代码字符串保持不变。）

#### 计算 Fog 指数（可读性）

```python
fog_result = analyzer.calculate_fog_index(text)

print(f"Fog Index: {fog_result['fog_index']:.2f}")
print(f"Average sentence length: {fog_result['avg_sentence_length']:.1f}")
print(f"Complex word %: {fog_result['complex_word_pct']:.1f}%")
```

#### 综合分析

```python
# 同时分析语调与可读性
full_result = analyzer.analyze_text(text, file_path='report.pdf')

print(full_result)
# 输出包括：tone_raw、fog_index、char_count、file_size_bytes 等字段
```

#### 自定义情感词典

```python
# 创建你自己的词典
with open('custom_sentiment.txt', 'w', encoding='utf-8') as f:
    f.write('POS:自定义正面词\n')
    f.write('NEG:自定义负面词\n')

analyzer = TextAnalyzer(config, 'custom_sentiment.txt')
```

### D. 指标计算器（Metrics Calculator）

#### 计算 TNI

```python
from src.metrics import MetricsCalculator, load_financial_data_from_csv
import pandas as pd

calculator = MetricsCalculator(config)

# 准备数据
tone_results = pd.DataFrame({
    'stock_code': ['000001', '000001', '000001'],
    'year': [2020, 2021, 2022],
    'tone_raw': [0.2, 0.5, 0.3]
})

financial_data = load_financial_data_from_csv('examples/financial_data.csv')

# 计算所有指标
metrics_df = calculator.calculate_all_metrics(tone_results, financial_data)

print(metrics_df[['stock_code', 'year', 'tone_raw', 'tni']])
```

#### 解释 TNI

```python
# 高正 TNI：在业绩不佳时使用积极语调（可能是创新叙事或过度乐观表述）
# 高负 TNI：在业绩良好时使用消极语调（偏保守、谨慎的表述）

summary = calculator.generate_summary_statistics(metrics_df)
print(f"Mean TNI: {summary['tni_stats']['mean']:.4f}")
print(f"Positive TNI count: {summary['tni_stats']['positive_count']}")
```

---

## 高级用法

### 1. 并行处理

```python
from concurrent.futures import ThreadPoolExecutor
import os

def process_single_pdf(pdf_path):
    parser = PDFParser(config)
    analyzer = TextAnalyzer(config, sentiment_dict_path)
    
    # 解析
    result = parser.parse_pdf(pdf_path, save_output=False)
    
    # 分析
    analysis = analyzer.analyze_text(result['text'])
    
    return {
        'path': pdf_path,
        'analysis': analysis
    }

# 并行处理
pdf_files = glob.glob('data/raw/**/*.pdf', recursive=True)

with ThreadPoolExecutor(max_workers=4) as executor:
    results = list(executor.map(process_single_pdf, pdf_files))

print(f"Processed {len(results)} files in parallel")
```

### 2. 自定义绩效指标

```python
# 添加自定义财务指标
financial_data['custom_metric'] = financial_data['net_profit'] / financial_data['total_assets']

# 配置计算器使用该指标
config['metrics']['performance_indicators'].append('custom_metric')
calculator = MetricsCalculator(config)

metrics_df = calculator.calculate_all_metrics(tone_results, financial_data)
```

### 3. 面向大规模数据的流式处理

```python
def stream_process_pdfs(pdf_directory, batch_size=10):
    pdf_files = glob.glob(f'{pdf_directory}/**/*.pdf', recursive=True)
    
    for i in range(0, len(pdf_files), batch_size):
        batch = pdf_files[i:i+batch_size]
        
        # 处理批次
        results = parser.batch_parse(batch)
        
        # 分析批次
        for result in results:
            analysis = analyzer.analyze_text(result['text'])
            yield analysis
        
        # 释放内存
        import gc
        gc.collect()

# 使用生成器逐批处理
for analysis in stream_process_pdfs('data/raw', batch_size=5):
    print(f"Processed: {analysis['file_path']}")
```

### 4. 与数据库集成

```python
import sqlalchemy as sa

# 创建数据库连接
engine = sa.create_engine('postgresql://user:pass@localhost/findb')

# 将结果保存到数据库
results.to_sql('financial_analysis', engine, if_exists='append', index=False)

# 查询结果
query = """
SELECT stock_code, year, tone_raw, tni
FROM financial_analysis
WHERE tni > 1.0
ORDER BY tni DESC
"""

high_tni = pd.read_sql(query, engine)
```

---

## 常见工作流

### 工作流 1：为论文做年报分析

```python
# 1. 下载特定时期的数据
pipeline = FinancialAnalysisPipeline()

results = pipeline.run(
    company_csv='research_sample.csv',  # 你的研究样本
    years=range(2015, 2023),  # 8 年数据
    report_types=['annual'],
    financial_data_csv='financial_metrics.csv'
)

# 2. 导出用于统计分析
results.to_stata('results_for_stata.dta')
results.to_csv('results_for_r.csv')

# 3. 生成描述性统计
from src.metrics import MetricsCalculator

calculator = MetricsCalculator(config)
summary = calculator.generate_summary_statistics(results)

print(f"Sample size: {summary['total_observations']}")
print(f"Mean Tone: {summary['tone_stats']['mean']:.4f}")
print(f"Mean TNI: {summary['tni_stats']['mean']:.4f}")
```

### 工作流 2：监控特定公司

```python
from src.pipeline import FinancialAnalysisPipeline

# 监控特定公司的季度报告
companies = ['000001', '600000', '000002']  # 你的监控名单

pipeline = FinancialAnalysisPipeline(config_path='config.yaml')

# 下载最新季度报告
downloader = pipeline.downloader
metadata = downloader.download_reports(
    company_codes=companies,
    years=[2024],
    report_types=['quarterly']
)

# 快速分析；手动 parse/analyze 也复用正式流水线的文本选择规则
for (code, year, type_), metas in metadata.items():
    for meta in metas:
        result = pipeline.parser.parse_pdf(meta['file_path'])
        analysis_text, text_source = pipeline.select_analysis_text(result)
        analysis = pipeline.analyzer.analyze_text(analysis_text, meta['file_path'])
        analysis['analysis_text_source'] = text_source
    
        # 如果语调非常消极则触发预警
        if analysis['tone_raw'] < -0.3:
            print(f"Alert: {code} shows negative tone ({analysis['tone_raw']:.2f})")
```

### 工作流 3：构建机器学习训练集

```python
# 提取用于机器学习的特征
ml_features = results[[
    'stock_code', 'year',
    'tone_raw', 'fog_index', 'file_size_bytes',
    'pos_word_count', 'neg_word_count',
    'avg_sentence_length', 'complex_word_pct',
    'roa', 'roa_change', 'ocf'
]]

# 创建目标变量（例如未来股票收益）
# 该目标需来自外部数据源
ml_features['future_return'] = ...  # 你的目标

# 保存供 ML 流水线使用
ml_features.to_parquet('ml_dataset.parquet')
```

---

## 故障排查

### 问题：下载时遇到验证码

**解决方法：**

```python
# 使用 Selenium 并手动处理验证码
from src.downloader import SeleniumDownloader

downloader = SeleniumDownloader(
    headless=False,
    wait_for_manual_captcha=True
)

cookies = downloader.download_with_login(wait_seconds=60)
```

### 问题：PDF 解析返回空文本

**解决方法：**

```python
# 对扫描版 PDF 启用 OCR
config['parser']['use_ocr'] = True
parser = PDFParser(config)

# 或尝试替代解析器
config['parser']['pdf_engine'] = 'pdfplumber'
parser = PDFParser(config)
```

### 问题：大批量处理时内存不足

**解决方法：**

```python
# 分小块处理
def process_in_chunks(files, chunk_size=10):
    for i in range(0, len(files), chunk_size):
        chunk = files[i:i+chunk_size]
        results = parser.batch_parse(chunk)
        # 立即保存结果
        pd.DataFrame(results).to_csv(f'results_chunk_{i}.csv')
        
        # 释放内存
        import gc
        gc.collect()
```

### 问题：编码错误

**解决方法：**

```python
# 总是指定 UTF-8 编码
df = pd.read_csv('file.csv', encoding='utf-8-sig')
df.to_csv('output.csv', encoding='utf-8-sig', index=False)

# 对于文本文件
with open('file.txt', 'r', encoding='utf-8') as f:
    text = f.read()
```

### 问题：下载速度慢

**解决方法：**

```python
# 增加并发（但请尊重服务器）
config['downloader']['concurrent_downloads'] = 10
config['downloader']['rate_limit'] = 1.0  # 秒

# 或在低峰时段下载
import time
if time.localtime().tm_hour in range(2, 6):  # 凌晨 2-6 点
    downloader.download_reports(...)
```

---

## 最佳实践

1. **始终缓存已下载文件** — 避免重复下载
2. **记录词典版本** — 情感词典会随时间演化
3. **保存中间结果** — 包括 PDF、解析文本、分析结果
   `save_intermediate: true` 时会额外保存 `parse_results` / `analysis_results`
   可用 `--restore-parse-results [PICKLE]` 或 `--restore-analysis-results [PICKLE]` 从指定文件恢复；省略 `PICKLE` 时读取 latest
4. **工作结束后清理缓存** — 使用 `finish-work --confirm-work-complete` 清理 PDF、临时中间结果和非审计解析缓存，保留 `streaming_audit` 留档
5. **使用版本控制** — 跟踪配置和代码变更
6. **抽样手工校验结果** — 对部分报告进行人工复核
7. **尊重 CNINFO 服务器** — 使用合理的限速
8. **保持日志记录** — 开启详细日志便于排查
9. **先在小样本上测试** — 在处理海量数据前先验证流程

---

## 附加资源

* **学术论文**：请参见 README 中的参考文献
* **API 文档**：查阅巨潮（CNINFO）的官方文档
* **社区支持**：在 GitHub Issues 讨论与提问
* **示例**：参见 `examples/` 目录下的脚本

---

## 获取帮助

如果遇到问题：

1. 检查本指南与 README
2. 搜索已有的 GitHub Issues
3. 启用 DEBUG 日志：`config['logging']['level'] = 'DEBUG'`
4. 新建 Issue 时请包含：

   * 错误信息
   * 代码片段
   * 日志输出
   * 系统信息（操作系统、Python 版本）
