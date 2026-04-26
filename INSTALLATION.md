# 安装指南 — CNINFO 金融分析器

本指南涵盖不同使用场景下的所有安装方法。

## 目录

1. [快速安装](#快速安装)
2. [开发环境搭建](#开发环境搭建)
3. [Docker 安装](#docker-安装)
4. [系统需求](#系统需求)
5. [平台特定说明](#平台特定说明)
6. [验证](#验证)
7. [故障排查](#故障排查)

---

## 快速安装

适用于只想运行分析的用户：

```bash
# 1. 克隆仓库
git clone https://github.com/kevin1000x/cninfo-financial-analyzer
cd cninfo-financial-analyzer

# 2. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows：venv\Scripts\activate

# 3. 安装包
pip install -e .

# 4. 初始化目录与词典
make init

# 5. 运行示例
python examples/full_analysis_example.py
```

---

## 开发环境搭建

适用于贡献者与开发者：

### 1. 克隆并准备环境

```bash
git clone https://github.com/kevin1000x/cninfo-financial-analyzer
cd cninfo-financial-analyzer

# 创建虚拟环境
python3.10 -m venv venv
source venv/bin/activate
```

### 2. 安装所有依赖

```bash
# 安装包含所有可选功能的依赖
pip install -e ".[full,dev]"

# 如需浏览器登录 / Cookie 引导能力
pip install -e ".[automation]"

# 或分步安装
pip install -e .                    # 核心依赖
pip install -e ".[full]"            # 含 OCR 与高级功能
pip install -e ".[automation]"      # 含 Selenium / Playwright
pip install -e ".[dev]"             # 含测试与代码风格工具
```

### 3. 设置 pre-commit 钩子

```bash
pre-commit install
```

### 4. 验证安装

```bash
make test
make lint
make typecheck
```

---

## Docker 安装

### 方案一：使用 Docker Compose（推荐）

```bash
# 构建并运行
docker-compose up --build

# 运行特定命令
docker-compose run cninfo-analyzer python -m src.pipeline download \
    --companies examples/company_list.csv \
    --years 2020-2021

# 交互式 shell
docker-compose run cninfo-analyzer /bin/bash
```

### 方案二：仅使用 Dockerfile

```bash
# 构建镜像
docker build -t cninfo-analyzer .

# 挂载卷并运行
docker run -v $(pwd)/data:/app/data \
           -v $(pwd)/logs:/app/logs \
           cninfo-analyzer

# 交互模式
docker run -it -v $(pwd)/data:/app/data \
           cninfo-analyzer /bin/bash
```

### Docker 优势

* 无需在本机配置 Python 环境
* 跨系统环境一致
* 易于部署到云端
* 包含系统依赖（如 Tesseract 等）

---

## 系统需求

### 最低配置

* **操作系统**：Linux、macOS、或 Windows 10+
* **Python**：3.8 及以上（推荐 3.10）
* **内存**：最低 4 GB，建议 8 GB
* **磁盘**：至少 10 GB 可用（用于 PDF 与结果）
* **网络**：稳定的互联网连接

### 推荐配置

* **Python**：3.10 或 3.11
* **内存**：16 GB（用于大批量处理）
* **磁盘**：50 GB 以上（大规模数据采集）
* **CPU**：多核，便于并行处理

---

## 平台特定说明

### Linux（Ubuntu / Debian）

```bash
# 安装系统依赖
sudo apt-get update
sudo apt-get install -y \
    python3.10 \
    python3.10-venv \
    python3-pip \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    ghostscript \
    poppler-utils

# 安装 Python 包
python3.10 -m venv venv
source venv/bin/activate
pip install -e ".[full]"
```

### macOS

```bash
# 安装 Homebrew（若尚未安装）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 安装依赖
brew install python@3.10 tesseract tesseract-lang poppler

# 设置虚拟环境
python3.10 -m venv venv
source venv/bin/activate
pip install -e ".[full]"
```

### Windows

```powershell
# 1. 从 python.org 安装 Python 3.10

# 2. 安装 Tesseract（用于 OCR）
# 从：https://github.com/UB-Mannheim/tesseract/wiki 下载
# 将安装目录加入 PATH，例如：C:\Program Files\Tesseract-OCR

# 3. 安装 Visual C++ Build Tools（部分依赖需要）
# 从：https://visualstudio.microsoft.com/visual-cpp-build-tools/ 下载

# 4. 创建虚拟环境
python -m venv venv
venv\Scripts\activate

# 5. 安装完整依赖
pip install -e .[full]
```

**Windows 说明**：

* 建议以管理员权限运行 PowerShell 或命令提示符
* 部分依赖（如 camelot）可能需要额外配置
* 可考虑使用 WSL2 以获得类似 Linux 的体验

---

## 验证

### 1. 检查安装

```bash
# 激活环境
source venv/bin/activate  # Windows：venv\Scripts\activate

# 检查 Python 版本
python --version  # 应为 3.8 及以上

# 检查包是否安装
python -c "import src; print(src.__version__)"

# 列出已安装包并查找 cninfo（可选）
pip list | grep cninfo
```

### 2. 运行测试

```bash
# 基本测试
pytest tests/ -v

# 带覆盖率
pytest tests/ --cov=src --cov-report=term

# 运行某个模块的测试
pytest tests/test_text_analyzer.py -v
```

### 3. 运行示例分析

```bash
# 使用示例数据做快速测试
python examples/full_analysis_example.py

# 或使用命令行
python -m src.pipeline analyze \
    --companies examples/company_list.csv \
    --years 2020 \
    --skip-download  # 若无网络则使用此参数
```

### 4. 检查输出

```bash
# 验证目录是否已创建
ls -la data/raw
ls -la data/parsed
ls -la data/results

# 查看日志
tail -f logs/analyzer.log
```

---

## 依赖详情

### 核心依赖（必需）

```
aiohttp>=3.9.0          # 异步 HTTP 客户端
requests>=2.31.0        # HTTP 请求库
pandas>=2.1.0           # 数据处理
numpy>=1.24.0           # 数值计算
pdfplumber>=0.10.0      # PDF 解析
PyMuPDF>=1.23.0         # 可替代的 PDF 解析器
jieba>=0.42.1           # 中文分词
PyYAML>=6.0             # 配置文件解析
loguru>=0.7.0           # 日志库
tqdm>=4.66.0            # 进度条
openpyxl>=3.1.0         # Excel 支持
```

### 可选依赖

```
# OCR 支持
pytesseract>=0.3.10
pdf2image>=1.16.0

# 高级表格解析
tabula-py>=2.8.0
camelot-py[cv]>=0.11.0

# 浏览器自动化（处理验证码）
selenium>=4.15.0
playwright>=1.40.0

# 开发工具
pytest>=7.4.0
black>=23.10.0
flake8>=6.1.0
mypy>=1.6.0
```

---

## 安装问题及解决方案

### 问题：`pip install` 失败

**表现**：`ERROR: Could not build wheels for ...`

**解决办法**：

```bash
# 更新 pip、setuptools、wheel
pip install --upgrade pip setuptools wheel

# 在 Linux 上安装构建依赖
sudo apt-get install build-essential python3-dev

# 在 Windows 上安装 Visual C++ Build Tools
# 从微软官网下载并安装
```

### 问题：找不到 Tesseract

**表现**：`TesseractNotFoundError`

**解决办法**：

```bash
# Linux
sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim

# macOS
brew install tesseract tesseract-lang

# Windows
# 从：https://github.com/UB-Mannheim/tesseract/wiki 下载并安装
# 并将安装目录加入 PATH
```

### 问题：PDF 解析时内存不足

**表现**：`MemoryError` 或 系统卡死

**解决办法**：

```python
# 分小批次处理
# 在 config.yaml 中设置：
parser:
  batch_size: 5  # 每次处理 5 个文件

# 或在代码中使用流式处理：
for pdf_file in pdf_files[:10]:  # 每次处理 10 个
    result = parser.parse_pdf(pdf_file)
```

### 问题：编码错误

**表现**：`UnicodeDecodeError` 或 中文乱码

**解决办法**：

```python
# 总是指定 UTF-8 编码
df = pd.read_csv('file.csv', encoding='utf-8-sig')
df.to_csv('output.csv', encoding='utf-8-sig')

# 对于文本文件
with open('file.txt', 'r', encoding='utf-8') as f:
    content = f.read()
```

### 问题：CNINFO 下载失败

**表现**：403 错误、频繁出现验证码

**解决办法**：

```python
# 1. 使用手动 Cookies
cookies = {
    'JSESSIONID': 'your_session_id'
}
downloader = CNINFODownloader(config, cookies=cookies)

# 2. 降低请求频率
config['downloader']['rate_limit'] = 5.0  # 5 秒

# 3. 使用 Selenium 处理验证码
from src.downloader import SeleniumDownloader
downloader = SeleniumDownloader(headless=False)
```

---

## 安装后设置

### 1. 配置设置

```bash
# 编辑 config.yaml
nano config.yaml  # 或使用 vim、code 等编辑器

# 需要重点检查的设置：
# - downloader.rate_limit（若被封则增大）
# - parser.use_ocr（PDF 为扫描件时启用）
# - analyzer.complex_word_threshold（根据语料调整）
```

### 2. 配置情感词典

```bash
# 使用自带词典
cp examples/cn_financial_sentiment.txt data/dictionaries/

# 或创建自定义词典
# 格式：POS:word 或 NEG:word（每行一个）
```

### 3. 准备输入数据

```bash
# 创建公司列表 CSV
# 格式：stock_code,company_name
# 例如：
# 000001,平安银行
# 600000,浦发银行

# 创建财务数据 CSV（用于 TNI）
# 格式：stock_code,year,roa,ocf 等
```

### 4. 测试运行

```bash
# 小规模测试运行
python -m src.pipeline analyze \
    --companies examples/company_list.csv \
    --years 2021 \
    --types annual

# 检查结果
ls -lh data/results/
```

---

## 更新

### 升级到最新版

```bash
# 激活环境
source venv/bin/activate

# 拉取最新代码
git pull origin main

# 更新依赖
pip install -e . --upgrade

# 重新运行测试
make test
```

### 检查版本

```bash
python -c "import src; print(f'Version: {src.__version__}')"
```

---

## 卸载

```bash
# 退出虚拟环境
deactivate

# 删除虚拟环境
rm -rf venv/

# 卸载包
pip uninstall cninfo-financial-analyzer

# 可选：删除数据
rm -rf data/raw/*
rm -rf data/parsed/*
rm -rf data/results/*
```

---

## 后续步骤

完成安装后建议执行：

1. 阅读 `README.md` 获取项目概览
2. 阅读 `USAGE_GUIDE.md` 获取详细示例
3. 检查并调整 `config.yaml` 配置
4. 运行 `examples/full_analysis_example.py`
5. 开始分析工作

---

## 获取帮助

如果遇到问题：

1. 检查本指南与故障排查部分
2. 查看日志：`tail -f logs/analyzer.log`
3. 在 GitHub Issues 中搜索或提问
4. 开启调试模式：`config['logging']['level'] = 'DEBUG'`
5. 提交新 Issue 时请包含：

   * 操作系统与 Python 版本
   * 错误信息与 Traceback
   * 可复现的步骤

**支持**：[GitHub Issues](https://github.com/kevin1000x/cninfo-financial-analyzer/issues)

---

**最后更新**：2024
**安装测试平台**：Ubuntu 22.04、macOS 13、Windows 11
