# CNINFO 金融文本分析器

一个用于从 CNINFO（巨潮资讯网）下载、解析与分析中文公司年报的完整 Python 工具包。

## 概述

本项目提供端到端流程，用于：

* 批量下载来自 CNINFO.com 的年报和公告
* 从 PDF 中提取结构化文本和财务表格
* 计算语言学指标（语调、可读性 / Fog 指数）
* 为研究计算 Tone-Normalized Innovation（TNI）得分

## 功能

* **并发下载**：支持异步下载，含限速与重试逻辑
* **断点续传**：跳过已下载的文件
* **PDF 解析**：提取段落、管理层讨论与分析（MD&A）部分以及财务表格
* **中文 NLP**：使用 jieba 分词并支持自定义金融词典
* **语调分析**：基于中文金融情感词典（Loughran & McDonald 词典的中文适配）
* **可读性指标**：中文适配的 Gunning-Fog 指数
* **TNI 计算**：语调与绩效指标的标准化结合
* **全面测试**：使用 pytest 的单元测试

## 安装

```bash
# 克隆仓库
git clone https://github.com/kevin1000x/cninfo-financial-analyzer
cd cninfo-financial-analyzer

# 创建虚拟环境（推荐）
python -m venv venv
source venv/bin/activate  # 在 Windows 上：venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 以开发模式安装该包
pip install -e .

# 如需 Selenium / Playwright 登录辅助
pip install -e ".[automation]"
```

## 快速开始

### 1. 基本用法

```python
from src.pipeline import FinancialAnalysisPipeline

# 初始化管道
pipeline = FinancialAnalysisPipeline(
    config_path='config.yaml',
    sentiment_dict_path='data/dictionaries/cn_financial_sentiment.txt'
)

# 运行分析
results = pipeline.run(
    company_codes=['000001', '600000'],
    years=[2020, 2021, 2022],
    report_types=['annual', 'semi-annual']
)

# 结果保存到 data/results/master_summary.xlsx
```

### 2. 命令行界面

```bash
# 仅下载
python -m src.pipeline download --companies examples/company_list.csv --years 2020-2022

# 完整分析流水线
python -m src.pipeline analyze --companies examples/company_list.csv --years 2020-2022 --financial-data examples/financial_data.csv

# 复用已保存的中间分析结果
python -m src.pipeline analyze --companies examples/company_list.csv --years 2020-2022 --skip-analyze

# 从最新 parse_results 恢复并重新分析
python -m src.pipeline analyze --companies examples/company_list.csv --years 2020-2022 --restore-parse-results

# 从指定 analysis_results pickle 恢复并直接输出/计算指标
python -m src.pipeline analyze --companies examples/company_list.csv --years 2020-2022 --restore-analysis-results data/results/intermediate/analysis_results_20240101_120000.pkl

# 确认当前工作结束并清理缓存，仅保留 streaming audit 留档
python -m src.pipeline finish-work --confirm-work-complete

# 解析已有的 PDFs
python -m src.pipeline parse --input data/raw/ --output data/parsed/
```

## 配置

编辑 `config.yaml` 来自定义：

```yaml
downloader:
  base_url: "http://www.cninfo.com.cn"
  concurrent_downloads: 5
  rate_limit: 2.0  # 请求之间的秒数
  retry_attempts: 3
  timeout: 30

parser:
  pdf_engine: "pdfplumber"  # 或 "pymupdf"
  max_saved_reports: 10
  use_ocr: false
  ocr_language: "chi_sim"

analyzer:
  segmentation_tool: "jieba"  # 或 "hanlp"
  min_word_length: 2
  complex_word_threshold: 2  # 字符数阈值
  common_vocab_path: "data/dictionaries/common_vocab.txt"

analysis:
  min_text_chars: 500
  min_mda_ratio: 0.02

output:
  format: "excel"  # 或 "csv", "parquet"
  save_raw_text: true
  save_structured_tables: true
  save_intermediate: true
  max_saved_intermediates: 5
```

其中：

* `save_raw_text` 控制是否落盘 `full_text.txt` / `mda_text.txt`
* `save_structured_tables` 控制是否落盘表格与财务报表 CSV
* `save_intermediate` 会保存 `parse_results` / `analysis_results` 到 `data/results/intermediate`
* `analysis.min_text_chars` / `analysis.min_mda_ratio` 控制分析阶段是否接受 `mda_text`，否则回退到 `full_text`
* `--restore-parse-results [PICKLE]` 可从指定或 latest `parse_results` 恢复并重新分析
* `--restore-analysis-results [PICKLE]` 可从指定或 latest `analysis_results` 恢复，跳过下载/解析/分析
* `--skip-analyze` 保持兼容：按原流程完成解析后读取 latest `analysis_results`
* `finish-work --confirm-work-complete` 会清理 PDF、临时中间结果和非审计解析缓存，保留 `streaming_audit` 与最终结果文件

手动解析后自行分析时，也应复用正式流水线的文本选择逻辑：

```python
analysis_text, text_source = pipeline.select_analysis_text(parse_result)
analysis = pipeline.analyzer.analyze_text(analysis_text, parse_result.get('file_path', ''))
analysis['analysis_text_source'] = text_source
```

## 数据要求

### 1. 公司列表（CSV）

`examples/company_list.csv`：

```csv
stock_code,company_name
000001,平安银行
600000,浦发银行
```

### 2. 财务数据（可选，用于 TNI 计算）

`examples/financial_data.csv`：

```csv
stock_code,year,ROA,operating_cash_flow
000001,2020,0.85,12500000000
000001,2021,0.92,13200000000
```

### 3. 情感词典

`data/dictionaries/cn_financial_sentiment.txt`：

```
# 正面词（每行一个）
增长
盈利
优化
# 负面词（以前缀标示）
NEG:下降
NEG:亏损
NEG:风险
```

## 方法论

### 语调指标（Tone）

基于 **Loughran & McDonald (2011)** 的金融情感分析，并对中文进行了适配：

```
Tone = (Positive_Words - Negative_Words) / (Positive_Words + Negative_Words)
```

**参考文献**：

* Loughran, T., & McDonald, B. (2011). "When is a liability not a liability? Textual analysis, dictionaries, and 10‐Ks." *Journal of Finance*, 66(1), 35-65.
* 中文金融情感词典：姜富伟团队扩展（2016）

### Fog 指数（可读性）

中文适配的 Gunning-Fog 公式：

```
Fog = 0.4 × (Average_Sentence_Length + Percentage_Complex_Words)
```

**复杂词定义**：字符数大于 2 且不在常用词表中的词

**替代指标**：文件大小（字节）作为文本复杂性的稳健代理

**参考文献**：

* Gunning, R. (1952). "The Technique of Clear Writing"
* Li, F. (2008). "Annual report readability, current earnings, and earnings persistence"
* 中文适配：多篇 CSDN / 学术博客讨论中文文本复杂性的做法

### TNI（语调-标准化创新）

```
TNI = Standardized_Tone × (-1) × Standardized_Performance
```

其中：

* `Standardized_Tone = (Tone - Mean_Tone) / StdDev_Tone`
* `Standardized_Performance = (ΔPerformance - Mean_ΔPerformance) / StdDev_ΔPerformance`
* `ΔPerformance = (ROA_t - ROA_{t-1})` 或 `(OCF_t - OCF_{t-1}) / Total_Assets`

## 实现参考

### 网页爬取

* **CNINFO Spider**: [https://github.com/jingmian/cninfo_spider（MIT](https://github.com/jingmian/cninfo_spider（MIT) 许可）

  * 提供公告查询的 POST 接口
  * 下载 URL 模式提取

### PDF 解析

* **Herrkun 的 pdfplumber 示例**：用于中文财务报告解析
* 相关库：pdfplumber（首选）、PyMuPDF（备用）、tabula-py/camelot（表格提取）

### 中文 NLP

* **Jieba**: [https://github.com/fxsjy/jieba（MIT](https://github.com/fxsjy/jieba（MIT) 许可）
* **cntext**: [https://github.com/hidadeng/cntext（用于学术验证的工具）](https://github.com/hidadeng/cntext（用于学术验证的工具）)

## 法律与合规

**重要免责声明**：

1. **CNINFO 服务条款**：请始终检查并遵守 [http://www.cninfo.com.cn/new/robots.txt](http://www.cninfo.com.cn/new/robots.txt)
2. **限速**：默认配置尊重服务器负载（请求间隔 2 秒）
3. **学术用途**：该工具仅用于学术研究与个人分析
4. **无担保**：使用风险自担；作者对误用不承担责任
5. **数据权利**：下载的报告版权仍归其发布方所有

**推荐最佳实践**：

* 在非高峰时段使用
* 实现礼貌爬取（设置 User-Agent、延时）
* 缓存已下载文件，避免重复下载
* 不要重新分发有版权的内容

## 登录与验证码处理

CNINFO 可能需要登录或验证码验证。可选方案：

### 方案 1：手动 Cookies（推荐）

```python
# 浏览器登录后，提取 Cookies
cookies = {
    'JSESSIONID': 'your_session_id',
    # 添加其他 Cookies
}

pipeline = FinancialAnalysisPipeline(cookies=cookies)
```

### 方案 2：Selenium（自动化）

```python
from src.downloader import SeleniumDownloader

# 先安装：pip install -e ".[automation]"

downloader = SeleniumDownloader(
    headless=False,  # 测试后可设为 True
    wait_for_manual_captcha=True  # 等待手动处理验证码
)

cookies = downloader.download_with_login(
    username='your_username',
    password='your_password',
    wait_seconds=60
)
```

### 方案 3：Playwright（较新的替代）

```bash
pip install -e ".[automation]"
playwright install chromium
```

```python
from src.downloader import PlaywrightDownloader

downloader = PlaywrightDownloader()
cookies = downloader.download_with_login(wait_seconds=60)
```

## 输出格式

主汇总表：`data/results/master_summary_{timestamp}.xlsx`

| 列名                  | 描述               |
| ------------------- | ---------------- |
| stock_code          | 股票代码             |
| company_name        | 公司名称             |
| year                | 报告年份             |
| report_type         | 报告类型（年报/半年度/季度）  |
| file_path           | PDF 路径；流式删 PDF 时为空 |
| text_path           | 提取后的文本路径         |
| analysis_text_source| 指标来自 `mda_text` 或 `full_text` |
| download_date       | 标准化后的披露日期（YYYY-MM-DD） |
| pos_word_count      | 正面词计数            |
| neg_word_count      | 负面词计数            |
| tone_raw            | 原始语调得分           |
| tone_normalized     | Z 分数标准化语调        |
| fog_index           | 可读性得分            |
| file_size_bytes     | 作为可读性替代的文件大小（字节） |
| avg_sentence_length | 平均句长             |
| complex_word_pct    | 复杂词比例            |
| roa                 | 资产回报率（ROA）       |
| roa_change          | ROA 同比变化         |
| ocf                 | 经营现金流            |
| ocf_change          | OCF 同比变化         |
| perf_score          | 标准化绩效得分          |
| tni                 | 语调标准化创新得分（TNI）   |

## 测试

```bash
# 运行所有测试
pytest tests/

# 运行特定测试模块
pytest tests/test_text_analyzer.py -v

# 运行并生成覆盖率报告
pytest --cov=src tests/
```

## 故障排查

### PDF 解析错误

* **问题**：部分 PDF 为扫描图像
* **解决**：在配置中启用 OCR：`use_ocr: true`（需要 Tesseract）

### MD&A 被误识别为目录

* **问题**：`mda_text` 很短、像目录行，导致语调指标失真
* **解决**：流水线和 `pipeline.select_analysis_text(...)` / `select_analysis_text(...)` 公共 helper 会自动回退到 `full_text` 分析，并在导出中写入 `analysis_text_source`

### 编码问题

* **问题**：中文字符乱码
* **解决**：确保全程使用 UTF-8 编码；对于 CSV 使用 `encoding='utf-8-sig'`

### 内存问题

* **问题**：大批量 PDF 处理造成内存不足
* **解决**：分批处理、增加系统内存或使用流式处理；默认仅保留最近 10 份解析文本目录，流式审查副本也只保留最近 10 份

### 验证码拦截

* **问题**：频繁出现验证码
* **解决**：使用手动 Cookie 方法、降低并发请求、增加更长的延时

## 贡献

欢迎贡献！请按照以下步骤：

1. Fork 本仓库
2. 创建功能分支
3. 为新增功能添加测试
4. 提交 Pull Request

## 许可证

本项目采用 **MIT 许可证** 发布。详见 LICENSE 文件。

**第三方许可证**：

* Jieba：MIT 许可证
* pdfplumber：MIT 许可证
* PyMuPDF：AGPL v3（可选商业许可）
* pandas、numpy、aiohttp：BSD 许可证
* 中文金融情感词典：学术使用（引用原作者）

## 引用

如果在学术研究中使用本工具，请引用：

```bibtex
@software{cninfo_financial_analyzer,
  title={CNINFO Financial Text Analyzer},
  author={{CNINFO Financial Analyzer Maintainers}},
  year={2024},
  url={https://github.com/kevin1000x/cninfo-financial-analyzer}
}
```

并参考以下方法论来源：

* Loughran & McDonald (2011)（情感分析方法）
* Li (2008)（可读性指标）
* 姜富伟等（中文金融情感词典）

## 联系与支持

* 问题反馈：GitHub Issues
* 邮件：请优先通过 GitHub Issues 联系维护者
* 文档：README.md、USAGE_GUIDE.md、INSTALLATION.md

---

**免责声明**：这是一个研究工具。请在使用时核实结果并遵守数据使用法规。
