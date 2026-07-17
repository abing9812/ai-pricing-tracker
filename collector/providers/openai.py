"""OpenAI 解析器。

來源：https://developers.openai.com/api/docs/pricing.md

為什麼是這個網址（2026-07 實測）：
  - 規格書原本給的 https://openai.com/api/pricing/ 無 UA 會 403；帶瀏覽器 UA 會
    重導到 /business/pricing/，那是企業方案頁，根本沒有 token 單價表。
  - 舊的 platform.openai.com/docs/pricing 已 301 到 developers.openai.com。
  - 文件站有 Markdown 版（.md），純 GET、不需偽裝 UA、價格是字面 JS 陣列，
    比解析 HTML 穩得多（HTML 表格全用 inline style，沒有任何 class 可定位）。

已知缺口：定價頁沒有 context window，模型頁又是逐一分頁 + tailwind class，
太脆弱不值得每天抓 50 次，因此 context_window 標為 unavailable（見 README）。
"""

from __future__ import annotations

import re
from typing import Any

from . import base

PROVIDER_ID = "openai"
DISPLAY_NAME = "OpenAI"
PRICING_URL = "https://developers.openai.com/api/docs/pricing"
PRICING_MD_URL = "https://developers.openai.com/api/docs/pricing.md"
NEWS_URL = "https://developers.openai.com/api/docs/changelog"

POLICY_PAGES = [
    ("Usage policies", "https://openai.com/policies/usage-policies/"),
]

# 定價頁同時有 standard / batch / flex / priority 四張表，同一個模型在四張表裡
# 都出現、價格不同。只認 standard —— 規格 §5 明定不追蹤批次與分級折扣，
# 而且若把四張表一起解析，batch 的半價會直接覆蓋掉標準價。
TIER = "standard"

# 模型名稱裡的 "(<272K context length)" 是計價註記不是名字的一部分：
# 超過 272K 的請求整筆以 2× input / 1.5× output 計價，這裡記的是標準區間價。
_CONTEXT_NOTE = re.compile(r"\s*\((<|>)?\s*\d+K context length\)\s*", re.I)


def _tier_rows(md: str, tier: str) -> str:
    """取出指定 tier 那張表的 rows=[...] 原始字串。"""
    marker = f'tier="{tier}"'
    start = md.find(marker)
    if start == -1:
        raise base.FetchError(f"定價頁找不到 tier={tier} 的表格，頁面結構可能改了")

    rows_start = md.find("rows={[", start)
    if rows_start == -1:
        raise base.FetchError(f"tier={tier} 後面找不到 rows=，頁面結構可能改了")

    rows_end = md.find("]}", rows_start)
    if rows_end == -1:
        raise base.FetchError(f"tier={tier} 的 rows= 沒有結尾，頁面結構可能改了")

    return md[rows_start + len("rows={["):rows_end]


def _cell(token: str) -> float | None:
    """把一格轉成數字；'-'、null、空字串代表該模型沒有這個計價項。"""
    token = token.strip()
    if token in {'"-"', "null", "", "-"}:
        return None
    try:
        return float(token)
    except ValueError:
        return None


def _parse_rows(raw: str, source_url: str) -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = []

    for match in re.finditer(r'\[\s*"([^"]+)"\s*,\s*([^\]]+)\]', raw):
        label = match.group(1)
        cells = [_cell(t) for t in match.group(2).split(",")]

        # 欄位數不固定：有 cache writes 的是 5 欄 [名稱, input, cached, writes, output]，
        # 沒有的是 4 欄 [名稱, input, cached, output]。output 永遠是最後一欄，
        # 所以用 cells[-1] 取，不能寫死索引。
        if len(cells) < 2:
            continue

        input_price = cells[0]
        output_price = cells[-1]

        model_id = _CONTEXT_NOTE.sub("", label).strip()
        note = label != model_id

        models.append(
            base.make_model(
                model_id=model_id,
                display_name=model_id,
                input_price_per_mtok=base.to_mtok(input_price),
                output_price_per_mtok=base.to_mtok(output_price),
                context_window=None,
                source_url=source_url,
                raw={
                    "label": label,
                    "tier": TIER,
                    "cells": cells,
                    **({"note": "標準區間價；超過 272K 的請求官方另以 2× input／1.5× output 計價"} if note else {}),
                },
                unavailable_fields=("context_window",),
            )
        )

    return models


def collect() -> base.ProviderData:
    md = base.get_markdown(PRICING_MD_URL)
    models = _parse_rows(_tier_rows(md, TIER), PRICING_URL)

    data = base.ProviderData(
        display_name=DISPLAY_NAME,
        pricing_url=PRICING_URL,
        news_url=NEWS_URL,
        models=models,
        policy_pages=[base.policy_page(label, url) for label, url in POLICY_PAGES],
    )
    data.notes.append("context window 官方定價頁未提供；價格為 standard 分級、272K 以內的標準區間價。")
    return data
