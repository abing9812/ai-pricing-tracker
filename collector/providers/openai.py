"""OpenAI 解析器。

來源：https://developers.openai.com/api/docs/pricing.md

為什麼是這個網址（2026-07 實測）：
  - 規格書原本給的 https://openai.com/api/pricing/ 無 UA 會 403；帶瀏覽器 UA 會
    重導到 /business/pricing/，那是企業方案頁，根本沒有 token 單價表。
  - 舊的 platform.openai.com/docs/pricing 已 301 到 developers.openai.com。
  - 文件站有 Markdown 版（.md），純 GET、不需偽裝 UA，比解析 HTML 穩得多。

頁面格式（2026-07-28 改版後）：
  - 舊版價格是 JS 元件的字面陣列（tier="standard" … rows={[...]}），已消失。
  - 現在是標準 Markdown 管線表格，靠「### Standard pricing data」標題定位。
  - 欄位改成短／長情境各自計價（Short/Long context input/cached/writes/output 共 9 欄）。
    本表追蹤的輸入／輸出價取 Short context 欄——沿用改版前「標準區間價」的口徑；
    長情境價記進 raw 備查，不當主要價格（規格 §5 不追蹤分級計價）。

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

# 同一頁還有 Batch / Flex / Fast mode（前身 Priority）的表，同一個模型在各表都
# 出現、價格不同。只認 Standard —— 規格 §5 明定不追蹤批次與分級折扣，
# 而且若把各表一起解析，batch 的半價會直接覆蓋掉標準價。
TIER = "standard"
SECTION_HEADING = "Standard pricing data"

# 表頭欄名 → 候選名單（小寫比對）。放進候選而不是寫死索引，是因為 2026-07-28
# 這次改版欄位就從 4 欄變 9 欄；下次若改回單一 Input/Output 也還接得住。
# 只做整格精確比對，不做子字串比對——"input" 去 substring 會同時撞到
# "cached input" 跟 "long context input"，抓錯欄位比抓不到更糟。
_INPUT_COLS = ("short context input", "input")
_OUTPUT_COLS = ("short context output", "output")
_LONG_INPUT_COLS = ("long context input",)
_LONG_OUTPUT_COLS = ("long context output",)

# 模型名稱裡的 "(<272K context length)" 是計價註記不是名字的一部分：
# 超過 272K 的請求以長情境欄計價，這裡記的是標準（短情境）區間價。
_CONTEXT_NOTE = re.compile(r"\s*\((<|>)?\s*\d+K context length\)\s*", re.I)


def _standard_table(md: str) -> list[list[str]]:
    """取出「Standard pricing data」標題底下第一張表格。"""
    heading = re.search(rf"^###\s+{re.escape(SECTION_HEADING)}\s*$", md, re.M)
    if heading is None:
        raise base.FetchError(
            f"定價頁找不到「{SECTION_HEADING}」標題，頁面結構可能改了"
        )

    section = md[heading.end():]
    next_heading = re.search(r"^###\s", section, re.M)
    if next_heading:
        section = section[: next_heading.start()]

    tables = base.markdown_tables(section)
    if not tables or len(tables[0]) < 2:
        raise base.FetchError(
            f"「{SECTION_HEADING}」底下找不到價格表格，頁面結構可能改了"
        )
    return tables[0]


def _find_col(header: list[str], candidates: tuple[str, ...], what: str) -> int | None:
    """依欄名找欄位索引；required 的欄位由呼叫端決定找不到要不要拋錯。"""
    lowered = [h.strip().lower() for h in header]
    for name in candidates:
        if name in lowered:
            return lowered.index(name)
    return None


def _parse_table(table: list[list[str]], source_url: str) -> list[dict[str, Any]]:
    header, *rows = table

    col_in = _find_col(header, _INPUT_COLS, "輸入價")
    col_out = _find_col(header, _OUTPUT_COLS, "輸出價")
    if col_in is None or col_out is None:
        raise base.FetchError(
            f"價格表找不到輸入／輸出價欄位（表頭：{header}），頁面結構可能改了"
        )

    # 長情境欄是 2026-07-28 之後才有的，缺了不算錯，只是 raw 少一筆備查資料。
    col_lin = _find_col(header, _LONG_INPUT_COLS, "長情境輸入價")
    col_lout = _find_col(header, _LONG_OUTPUT_COLS, "長情境輸出價")

    models: list[dict[str, Any]] = []
    for row in rows:
        if len(row) <= max(col_in, col_out) or not row[0]:
            continue

        label = row[0]
        model_id = _CONTEXT_NOTE.sub("", label).strip()

        raw: dict[str, Any] = {"label": label, "tier": TIER, "cells": row}
        long_in = base.parse_price(row[col_lin]) if col_lin is not None and col_lin < len(row) else None
        long_out = base.parse_price(row[col_lout]) if col_lout is not None and col_lout < len(row) else None
        if long_in is not None or long_out is not None:
            raw["long_context_input_per_mtok"] = base.to_mtok(long_in)
            raw["long_context_output_per_mtok"] = base.to_mtok(long_out)
            raw["note"] = "標準（短情境）區間價；長情境請求官方另計價，見 raw 欄位"

        models.append(
            base.make_model(
                model_id=model_id,
                display_name=model_id,
                input_price_per_mtok=base.to_mtok(base.parse_price(row[col_in])),
                output_price_per_mtok=base.to_mtok(base.parse_price(row[col_out])),
                context_window=None,
                source_url=source_url,
                raw=raw,
                unavailable_fields=("context_window",),
            )
        )

    if not models:
        # 表格在、卻一個模型都解析不出來，代表列格式變了。寧可整家 failed
        # 讓 diff 沿用舊資料，也不要回傳空清單觸發整批「未再出現」。
        raise base.FetchError("Standard 價格表解析出 0 個模型，列格式可能改了")

    return models


def collect() -> base.ProviderData:
    md = base.get_markdown(PRICING_MD_URL)
    models = _parse_table(_standard_table(md), PRICING_URL)

    data = base.ProviderData(
        display_name=DISPLAY_NAME,
        pricing_url=PRICING_URL,
        news_url=NEWS_URL,
        models=models,
        policy_pages=[base.policy_page(label, url) for label, url in POLICY_PAGES],
    )
    data.notes.append("context window 官方定價頁未提供；價格為 standard 分級的短情境（≤272K）區間價。")
    return data
