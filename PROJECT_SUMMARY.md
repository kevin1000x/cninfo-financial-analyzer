# CNINFO 金融分析器 — 项目概述

## 📋 项目概览

这是一个面向生产环境的完整 Python 项目，用于分析来自 CNINFO（巨潮资讯网）的中文公司年报。系统提供从下载报告到计算高级语言学与财务指标的端到端功能。

### 主要功能

✅ **批量下载**：并发下载，带限速、重试逻辑和断点续传能力
✅ **PDF 解析**：从中文财务报告中提取文本与表格
✅ **情感分析**：使用中文金融情感词典计算语调（Tone）指标
✅ **可读性分析**：中文适配的 Gunning-Fog 指数
✅ **绩效指标**：计算 TNI（语调-标准化创新）
✅ **全面测试**：使用 pytest 的单元测试
✅ **文档完善**：包含 README、使用指南与内联文档

---

## 📁 项目结构

```
cninfo-financial-analyzer/
├── README.md                    # 主文档
├── USAGE_GUIDE.md               # 详细使用示例
├── LICENSE                      # MIT 许可证
├── requirements.txt             # Python 依赖
├── setup.py                     # 包安装脚本
├── config.yaml                  # 配置文件
├── Makefile                     # 构建自动化
├── .gitignore                   # Git 忽略规则
│
├── src/                         # 源代码
│   ├── __init__.py              # 包初始化
│   ├── downloader.py            # CNINFO 爬虫（约 520 行）
│   ├── pdf_parser.py            # PDF 解析（约 370 行）
│   ├── text_analyzer.py         # 语调与 Fog 分析（约 380 行）
│   ├── metrics.py               # TNI 计算（约 320 行）
│   ├── utils.py                 # 辅助函数（约 260 行）
│   └── pipeline.py              # 主流程编排（约 440 行）
│
├── tests/                       # 测试套件
│   ├── __init__.py
│   ├── test_downloader.py       # 下载器测试
│   ├── test_pdf_parser.py       # 解析器测试
│   ├── test_text_analyzer.py    # 分析器测试（约 240 行）
│   └── test_metrics.py          # 指标测试（约 260 行）
│
├── examples/                    # 示例文件
│   ├── company_list.csv         # 示例公司列表
│   ├── financial_data.csv       # 示例财务数据
│   ├── cn_financial_sentiment.txt  # 情感词典
│   └── full_analysis_example.py    # 完整示例脚本
│
└── data/                        # 数据目录
    ├── raw/                     # 下载的 PDF
    ├── parsed/                  # 提取的文本/表格
    ├── dictionaries/            # 情感词典等
    └── results/                 # 分析输出
```

**合计**：约 2,800 行 Python 代码 + 完备文档

---

## 🔧 核心组件

### 1. 下载器（`src/downloader.py`）

**目的**：从 CNINFO 下载年报，具备健壮的错误处理能力

**主要特点**：

* 使用 `aiohttp` 实现异步并发下载
* 自动限速（可配置）
* 指数退避重试机制
* 断点续传（跳过已存在文件）
* 支持基于 Cookie 的认证
* 可选：Selenium/Playwright 处理验证码

**示例用法**：

```python
from src.downloader import CNINFODownloader

downloader = CNINFODownloader(config, cookies=None)
metadata = downloader.download_reports(
    company_codes=['000001', '600000'],
    years=[2020, 2021, 2022],
    report_types=['annual', 'semi_annual']
)
```

**实现说明**：

* 使用 CNINFO 的 POST 接口进行公告查询
* 下载 URL 模式：`http://static.cninfo.com.cn/{adjunctUrl}`
* 验证下载文件（最小文件大小、PDF 格式）
* 记录统计信息：成功、失败、跳过

---

### 2. PDF 解析器（`src/pdf_parser.py`）

**目的**：从 PDF 报告中提取文本与结构化表格

**主要特点**：

* 多解析引擎：以 pdfplumber 为主、PyMuPDF 作备用
* 使用关键词匹配提取管理层讨论与分析（MD&A）章节
* 当 MD&A 过短、像目录或占全文比例异常时，分析阶段会自动回退到全文
* 识别并提取财务报表（资产负债表、利润表、现金流量表）
* 表格解析支持多个引擎（pdfplumber、tabula、camelot）
* 对扫描件支持 OCR（Tesseract）
* 解析留档默认仅保留最近 10 份，并排除 `streaming_audit/`

**示例用法**：

```python
from src.pdf_parser import PDFParser

parser = PDFParser(config)
result = parser.parse_pdf('report.pdf', save_output=True)

# 返回结构示例：
# {
#   'text': str,                    # 全文文本
#   'mda_text': str,                # 仅 MD&A 文本
#   'tables': [DataFrame],          # 所有表格
#   'financial_statements': {       # 分类后的报表
#     'balance_sheet': DataFrame,
#     'income_statement': DataFrame,
#     'cash_flow': DataFrame
#   }
# }
```

**实现说明**：

* 使用正则匹配定位章节
* 针对中文文本的提取优化
* 支持跨页表格识别
* 按组织良好的目录结构保存解析结果

---

### 3. 文本分析器（`src/text_analyzer.py`）

**目的**：计算语言学指标（语调 Tone 与 Fog 指数）

**主要特点**：

* 使用 Jieba 做中文分词
* 基于自定义金融情感词典的情感分析
* 中文适配的 Gunning-Fog 指数计算
* 提供替代可读性指标（文件大小、字符计数等）

**语调指标**（基于 Loughran & McDonald, 2011）：

```
Tone = (Positive_Words - Negative_Words) / (Positive_Words + Negative_Words)
```

**Fog 指数**（中文适配）：

```
Fog = 0.4 × (Average_Sentence_Length + Percentage_Complex_Words)
```

**复杂词定义**：

* 中文字符数超过 2 的词
* 且不在常用词表中（若提供常用表）

**示例用法**：

```python
from src.text_analyzer import TextAnalyzer

analyzer = TextAnalyzer(config, sentiment_dict_path)
result = analyzer.analyze_text(text, file_path=None)

# 返回内容示例包括：
# - tone_raw、pos_word_count、neg_word_count
# - fog_index、avg_sentence_length、complex_word_pct
# - char_count、file_size_bytes
```

---

### 4. 指标计算器（`src/metrics.py`）

**目的**：计算 TNI 与其它绩效类指标

**主要特点**：

* 计算绩效变化（如同比变化）
* 支持 Z-score 与 min-max 标准化
* 可用多种绩效指标来计算 TNI
* 缺失数据处理策略灵活

**TNI 公式**：

```
TNI = Standardized_Tone × (-1) × Standardized_Performance
```

解释：

* 高正 TNI：语调积极但绩效下降（可能是“创新叙事”）
* 高负 TNI：语调消极但绩效改善（偏保守的披露）

**示例用法**：

```python
from src.metrics import MetricsCalculator

calculator = MetricsCalculator(config)
metrics_df = calculator.calculate_all_metrics(
    tone_results,      # 包含语调分析结果的 DataFrame
    financial_data     # 包含财务指标的 DataFrame
)

# 输出包含：tone_normalized、perf_score、tni 等
```

---

### 5. 流程编排器（`src/pipeline.py`）

**目的**：端到端工作流编排

**主要特点**：

* 五阶段流水线：下载 → 解析 → 分析 → 指标计算 → 保存
* 提供命令行接口
* 可配置的跳过选项（如测试时跳过下载/解析）
* 支持 `--skip-analyze` 复用最新中间分析结果
* 支持 `--restore-parse-results [PICKLE]` / `--restore-analysis-results [PICKLE]` 从 latest 或指定中间结果恢复
* 流式模式的文本审计目录默认仅保留最近 10 份
* 支持多种结果保存格式

**示例用法**：

```python
from src.pipeline import FinancialAnalysisPipeline

pipeline = FinancialAnalysisPipeline(
    config_path='config.yaml',
    sentiment_dict_path='data/dictionaries/cn_financial_sentiment.txt'
)

results = pipeline.run(
    company_csv='companies.csv',
    years=[2020, 2021, 2022],
    report_types=['annual'],
    financial_data_csv='financial.csv'
)
```

---

## 🧪 测试

### 测试覆盖范围

* **test_downloader.py**：下载逻辑、URL 构造、错误处理
* **test_pdf_parser.py**：文本提取、MD&A 识别、表格解析
* **test_text_analyzer.py**：分词、语调计算、Fog 指数
* **test_metrics.py**：标准化、绩效变化、TNI 计算

### 运行测试

```bash
# 运行全部测试
pytest tests/ -v

# 带覆盖率
pytest tests/ --cov=src --cov-report=html

# 运行某个模块的测试
pytest tests/test_text_analyzer.py -v
```

---

## 📊 输出格式

### 主汇总表

保存路径示例：`data/results/master_summary_{timestamp}.xlsx`

| 列名                  | 含义         | 示例                       |
| ------------------- | ---------- | ------------------------ |
| stock_code          | 6 位股票代码    | 000001                   |
| company_name        | 公司名称       | 平安银行                     |
| year                | 报告年份       | 2021                     |
| report_type         | 报告类型       | annual                   |
| file_path           | PDF 文件路径（流式删 PDF 时可能为空） | data/raw/000001/2021/... |
| text_path           | 提取文本路径    | data/parsed/.../full_text.txt |
| analysis_text_source| 指标来源文本    | mda_text / full_text     |
| download_date       | 标准化披露日期   | 2024-03-15               |
| pos_word_count      | 正面词计数      | 45                       |
| neg_word_count      | 负面词计数      | 12                       |
| tone_raw            | 原始语调得分     | 0.5758                   |
| tone_normalized     | Z 分数标准化    | 0.8234                   |
| fog_index           | 可读性得分      | 24.5                     |
| file_size_bytes     | 文件大小（字节）   | 2458903                  |
| avg_sentence_length | 平均句长       | 18.3                     |
| complex_word_pct    | 复杂词比例（%）   | 15.2                     |
| roa                 | 资产回报率（ROA） | 0.85                     |
| roa_change          | ROA 同比变化   | 0.07                     |
| ocf                 | 经营现金流      | 12500000000              |
| ocf_change          | OCF 同比变化   | 700000000                |
| perf_score          | 绩效得分       | 0.07                     |
| tni                 | TNI 得分     | -0.3421                  |

---

## 🔬 研究方法论

### 学术基础

**语调分析**：

* 基于 Loughran & McDonald (2011) 的金融情感词典
* 使用姜富伟团队扩展（2016）对中文进行适配
* 已通过对中文财报的人工编码进行验证

**可读性**：

* 中文化的 Gunning-Fog 指数
* 复杂词的定义参照中文语言学特征
* 文件大小作为稳健替代指标（参考 Li, 2008）

**TNI 指标**：

* 捕捉语调与绩效之间的不一致
* 有助于识别“创新叙事”或保守式披露
* 标准化保证不同公司与时间的可比性

### 引用要求

若用于学术研究，请引用：

1. **Loughran, T., & McDonald, B. (2011)**. “When is a liability not a liability? Textual analysis, dictionaries, and 10‐Ks.” *Journal of Finance*, 66(1), 35-65.

2. **Li, F. (2008)**. “Annual report readability, current earnings, and earnings persistence.” *Journal of Accounting and Economics*, 45(2-3), 221-247.

3. **姜富伟等（2016）**。中文金融情感词典（扩展版）。

4. **本软件**：请按你的引用格式引用本工具。

---

## 🚀 快速启动命令

```bash
# 1. 初始化
make init

# 2. 运行示例分析
make run-example

# 或逐步执行：
make download-example  # 下载报告
make parse-example     # 解析 PDFs
make analyze-example   # 完整分析

# 3. 运行测试
make test

# 4. 代码质量检查
make check
```

---

## ⚙️ 配置选项

### `config.yaml` 中的关键设置

**Downloader（下载器）**：

* `concurrent_downloads`：并行下载数量（默认：5）
* `rate_limit`：请求间隔秒数（默认：2.0）
* `retry_attempts`：重试次数（默认：3）

**Parser（解析器）**：

* `pdf_engine`：`"pdfplumber"` 或 `"pymupdf"`
* `use_ocr`：对扫描件启用 OCR（默认：false）
* `extract_tables`：是否提取表格（默认：true）

**Analyzer（分析器）**：

* `segmentation_tool`：`"jieba"` 或 `"hanlp"`
* `complex_word_threshold`：复杂词的字符阈值（默认：2）
* `fog_formula`：使用 `"chinese_adapted"`

**Metrics（指标）**：

* `standardization`：`"zscore"` 或 `"minmax"`
* `missing_data_strategy`：`"skip"`、`"interpolate"` 或 `"forward_fill"`

---

## 📚 资源

### 文档

* **README.md**：概览与安装说明
* **USAGE_GUIDE.md**：详细示例与工作流
* **API 文档**：所有模块均有内联 docstring

### 示例文件

* `examples/company_list.csv`：示例公司列表
* `examples/financial_data.csv`：示例财务数据
* `examples/cn_financial_sentiment.txt`：情感词典
* `examples/full_analysis_example.py`：完整工作流示例

### 外部参考

* CNINFO Spider: [https://github.com/jingmian/cninfo_spider](https://github.com/jingmian/cninfo_spider)
* pdfplumber 文档: [https://github.com/jazzband/pdfplumber](https://github.com/jazzband/pdfplumber)
* Jieba 文档: [https://github.com/fxsjy/jieba](https://github.com/fxsjy/jieba)

---

## 🔒 法律与合规

### 重要免责声明

1. **CNINFO 条款**：始终遵守 [http://www.cninfo.com.cn/robots.txt](http://www.cninfo.com.cn/robots.txt)
2. **限速**：默认 2 秒延时以尊重服务器负载
3. **版权**：下载的报告仍归发布方所有
4. **学术用途**：仅供研究与个人分析使用
5. **无担保**：使用风险自担

### 最佳实践

* 在非高峰时段下载
* 缓存文件以避免重复下载
* 不要重新分发有版权的内容
* 在发表中引用数据来源

---

## 🐛 已知问题与局限

1. **验证码**：CNINFO 可能要求验证码（可用 Selenium 作为替代方案）
2. **OCR 准确性**：扫描件需 Tesseract，识别准确性不稳定
3. **内存**：大批量处理建议使用流式处理
4. **词典覆盖**：情感词典可能无法覆盖全部领域术语
5. **语言支持**：目前仅支持中文；不支持多语言

---

## 🛠️ 故障排查

### 常见问题

**问题**：下载失败
**解决**：检查网络、尝试手动 Cookie、降低并发量

**问题**：提取到的文本为空
**解决**：启用 OCR 或更换 PDF 引擎

**问题**：内存不足
**解决**：分批处理或增加系统内存

**问题**：编码错误
**解决**：始终使用 UTF-8 编码（`encoding='utf-8-sig'`）

详细排查流程请参见 USAGE_GUIDE.md

---

## 📈 未来改进方向

潜在改进项：

* [ ] Web 界面（Flask / Streamlit）
* [ ] 支持更多交易所（如上交所）
* [ ] 基于机器学习的情感模型
* [ ] 实时监控仪表盘
* [ ] 多语言支持（英文报告）
* [ ] 数据库集成（PostgreSQL / MongoDB）
* [ ] 云端部署（AWS / Azure）

---

## 🤝 贡献说明

欢迎贡献！请按以下流程：

1. Fork 本仓库
2. 创建功能分支（`git checkout -b feature/amazing-feature`）
3. 提交变更（`git commit -m 'Add amazing feature'`）
4. 推送分支（`git push origin feature/amazing-feature`）
5. 发起 Pull Request

---

## 📞 支持

* **问题反馈**：GitHub Issues
* **文档**：README.md、USAGE_GUIDE.md
* **电子邮件**：请通过 GitHub Issues 联系维护者

---

## 📄 许可证

MIT 许可证 — 详见 LICENSE 文件。

第三方许可举例：

* Jieba：MIT
* pdfplumber：MIT
* PyMuPDF：AGPL v3（可选商业许可）
* pandas：BSD 3-Clause
* 其它依赖：详见 requirements.txt

---

**版本**：1.0.0
**最后更新**：2024
**作者**：CNINFO Financial Analyzer Maintainers
**状态**：已准备好投入生产 ✅

---

如果你需要我把示例脚本中的英文注释也改成中文注释（并保证代码仍可运行），或者把这个文档生成 README.md、PDF 或简短的 PPT，我可以马上为你处理并输出对应文件。想要哪个就说一声，lcz。
