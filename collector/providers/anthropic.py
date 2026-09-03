"""Anthropic（Claude）解析器。

來源：
  價格 https://platform.claude.com/docs/en/about-claude/pricing.md
  視窗 https://platform.claude.com/docs/en/about-claude/models/overview.md

為什麼是這些網址（2026-07 實測）：
  - 規格書原本給的 https://www.anthropic.com/pricing 已導向 claude.com/pricing，
    那是消費者訂閱方案頁（Free/Pro/Max/Team），沒有 API token 單價表。
  - 文件站 docs.anthropic.com 已搬到 platform.claude.com。
  - 兩個網域完全不擋機器人，連 UA 都不用帶。
  - HTML 版的價格被包在 Next.js App Router 的 RSC payload 轉義字串裡，
    不是乾淨的 DOM 表格；Markdown 版是漂亮的管線表格，直接用它。
"""

from __future__ import annotations

import re
from typing import Any

from . import base

PROVIDER_ID = "anthropic"
DISPLAY_NAME = "Anthropic"
PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICING_MD_URL = "https://platform.claude.com/docs/en/about-claude/pricing.md"
MODELS_MD_URL = "https://platform.claude.com/docs/en/about-claude/models/overview.md"
NEWS_URL = "https://www.anthropic.com/news"

POLICY_PAGES = [
    ("Usage policies", "https://www.anthropic.com/legal/aup"),
]

# 定價頁有 30 幾張表（批次、快取、fast mode、雲端平台…），只認 Model pricing 那張。
# 認表頭而不是認順序，表格搬家也不會抓錯。
_INPUT_HEADER = "Base input tokens"
_OUTPUT_HEADER = "Output tokens"
_PRICING_HEADERS = (_INPUT_HEADER, _OUTPUT_HEADER)

# 「Claude Sonnet 5 through August 31, 2026」與「… starting September 1, 2026」是
# 同一個模型的兩段時效價格，官方同時列出兩列。id 保留時效字樣讓兩列各自獨立，
# 否則後者會覆蓋前者；查 context window 時才把字樣拿掉去對照。
_TEMPORAL_QUALIFIER = re.compile(r"\s+(through|starting)\s+.*$", re.I)
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")


def _clean_name(cell: str) -> str:
    return _PARENTHETICAL.sub("", cell).strip()


def _base_name(name: str) -> str:
    return _TEMPORAL_QUALIFIER.sub("", name).strip()


def _norm_header(cell: str) -> str:
    """表頭比對用的正規化：小寫、收斂空白。

    官方 2026-09-02 把表頭從 `Base Input Tokens` 改成 `Base input tokens`，畫面上
    只差大小寫、表格一列沒動，整家卻因為精確比對而抓取失敗，Claude 全系列價格
    凍了一天多。這關卡只該擋「表格真的不見了」，不該被排版習慣絆倒。
    比照 openai._find_col 的既有作法，認字不認大小寫。
    """
    return re.sub(r"\s+", " ", cell).strip().lower()


def _find_table(tables: list[list[list[str]]], headers: tuple[str, ...]) -> list[list[str]] | None:
    wanted = [_norm_header(h) for h in headers]
    for table in tables:
        if not table:
            continue
        head = [_norm_header(cell) for cell in table[0]]
        if all(h in head for h in wanted):
            return table
    return None


def _context_windows() -> dict[str, int]:
    """從 models overview 的兩張比較表撈 模型名 → context window。

    那兩張表是轉置的（模型是欄、屬性是列），且只涵蓋現行與近期模型；
    撈不到的模型 context 會是 None，由 make_model 標 needs_review。
    """
    md = base.get_markdown(MODELS_MD_URL)
    result: dict[str, int] = {}

    for table in base.markdown_tables(md):
        if not table:
            continue
        header = table[0]
        for row in table[1:]:
            if not row or row[0].strip("* ").lower() != "context window":
                continue
            for col, value in enumerate(row[1:], start=1):
                if col >= len(header):
                    break
                window = base.parse_context_window(value)
                if window:
                    result[_clean_name(header[col])] = window

    return result


def _parse_pricing(md: str, contexts: dict[str, int]) -> list[dict[str, Any]]:
    table = _find_table(base.markdown_tables(md), _PRICING_HEADERS)
    if not table:
        raise base.FetchError("定價頁找不到 Model pricing 表格，頁面結構可能改了")

    header = [_norm_header(cell) for cell in table[0]]
    input_col = header.index(_norm_header(_INPUT_HEADER))
    output_col = header.index(_norm_header(_OUTPUT_HEADER))
    models: list[dict[str, Any]] = []

    for row in table[1:]:
        if len(row) <= max(input_col, output_col) or not row[0]:
            continue

        name = _clean_name(row[0])
        if not name:
            continue

        context = contexts.get(_base_name(name))

        # 比較表有讀到、只是查無這個模型 → 是已知的結構缺口（退役與限量機種不列在
        # 比較表上），不必天天叫人去看。整張比較表都沒讀到才是真的出事，那時
        # contexts 會是空的，所有模型一起標 needs_review，警訊才不會被吃掉。
        known_gap = bool(contexts) and context is None

        models.append(
            base.make_model(
                model_id=base.slugify(name),
                display_name=name,
                input_price_per_mtok=base.to_mtok(base.parse_price(row[input_col])),
                output_price_per_mtok=base.to_mtok(base.parse_price(row[output_col])),
                context_window=context,
                source_url=PRICING_URL,
                raw={
                    "label": row[0],
                    "input": row[input_col],
                    "output": row[output_col],
                },
                unavailable_fields=("context_window",) if known_gap else (),
            )
        )

    return models


def collect() -> base.ProviderData:
    md = base.get_markdown(PRICING_MD_URL)

    try:
        contexts = _context_windows()
    except Exception as exc:  # noqa: BLE001 - 視窗抓不到不該讓價格一起陪葬
        contexts = {}
        print(f"[warn] anthropic context window 抓取失敗：{exc}")

    data = base.ProviderData(
        display_name=DISPLAY_NAME,
        pricing_url=PRICING_URL,
        news_url=NEWS_URL,
        models=_parse_pricing(md, contexts),
        policy_pages=[base.policy_page(label, url) for label, url in POLICY_PAGES],
    )
    if not contexts:
        data.notes.append("這次沒抓到 context window 對照表，視窗欄位待覆核。")
    return data
