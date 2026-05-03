# Sentiment Dictionary — Source & Citation

The runtime sentiment dictionary at `data/dictionaries/cn_financial_sentiment.txt`
is **not** original to this project. It is a derivative (POS:/NEG: text format
adaptation) of the Chinese financial sentiment dictionary released by
Jiang Fuwei et al. (2020), upstream at:

https://github.com/MengLingchao/Chinese_financial_sentiment_dictionary/

**Source file:** `data/dictionaries/source/中文金融情感词典_姜富伟等(2020).xlsx`
(byte-identical copy from upstream — do not edit by hand; re-pull from upstream
if you suspect corruption)

**Conversion script:** `scripts/build_sentiment_dictionary.py` reads the source
xlsx and writes the runtime POS:/NEG: file. Re-run after replacing the source xlsx.

**Word counts:**

| Polarity | Raw rows in xlsx | After dedup |
|----------|------------------|-------------|
| Positive | 3338             | 3194        |
| Negative | 5890             | 5621        |
| Total    | 9228             | 8815        |

Raw row counts match the upstream README. The xlsx contains intra-sheet duplicates
(positive ~144, negative ~269); since `TextAnalyzer` stores words in a `set`,
deduping at build time is a no-op for the analyzer and produces a smaller, cleaner file.

## Required citations

Per the upstream README, anyone using this dictionary (in original or derived form)
must cite both works:

1. Fuwei Jiang, Joshua Lee, Xiumin Martin, and Guofu Zhou. "Manager Sentiment and
   Stock Returns." *Journal of Financial Economics* 132(1), 2019, 126-149.

2. 姜富伟、孟令超、唐国豪. "媒体文本情绪与股票回报预测."《经济学(季刊)》,
   2021 年第 4 期, 1323-1344.

## License & redistribution

The upstream repository does not declare an explicit OSI-approved license.
Its README states (translated):

> 在尊重知识产权的前提下，读者可以免费使用该词典 — readers may use the dictionary
> for free, subject to respecting intellectual property rights, provided the citations
> above are included.

This project bundles the dictionary solely for research and demo use. Downstream
redistribution should follow the upstream README's terms.

## Why bundle, not download at runtime

The dictionary is small (~150 KB xlsx, ~150 KB runtime txt), version-stable,
and required for the `tone` metric to produce comparable results across
deployments. Bundling avoids runtime fetch failures and keeps the Docker image
reproducible.
