"""DeepSeek 解析器。

來源：https://api-docs.deepseek.com/quick_start/pricing/

實測重點（2026-07）：
  - 規格書給的網址正確，只是會補上尾斜線。不擋機器人，UA 有無皆可。
  - ⚠ 這站是 Docusaurus SPA，**任何不存在的路徑都回 HTTP 200 + 首頁 HTML**
    （/quick_start/pricing.md、/llms.txt、/search-index.json 全中招）。所以絕不能
    用狀態碼判斷成功，一定要驗證內容裡真的有價格表 —— 見 collect() 的斷言。
  - 沒有 JSON 來源，文件原始碼也未公開，只能解析 HTML（好在是 SSG，價格在原始
    bytes 裡）。
  - 離峰折扣（off-peak）在 V4 之後已取消，全站沒有這個字樣，不需處理。

規格 §5 只要標準價：輸入取 CACHE MISS 那一列（不是 CACHE HIT 的快取折扣價）。
"""

from __future__ import annotations

from typing import Any

from . import base

PROVIDER_ID = "deepseek"
DISPLAY_NAME = "DeepSeek"
PRICING_URL = "https://api-docs.deepseek.com/quick_start/pricing/"
NEWS_URL = "https://api-docs.deepseek.com/news/"

# 用 cdn 上的靜態條款檔而不是文件站的頁面：文件站對任何路徑都回 200，
# 網址一旦失效會靜默地把首頁雜湊起來，天天誤報政策變動；cdn 上 404 就是 404。
POLICY_PAGES = [
    (
        "Terms of service",
        "https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html",
    ),
]

MODEL_ROW = "MODEL"
INPUT_ROW = "1M INPUT TOKENS (CACHE MISS)"
OUTPUT_ROW = "1M OUTPUT TOKENS"
CONTEXT_ROW = "CONTEXT LENGTH"


def _cell_text(node: Any) -> str:
    return node.get_text(" ", strip=True)


def _row_values(table: Any, label: str, model_count: int) -> list[str]:
    """取某一列的值，並對齊模型欄位。

    這張表是轉置的（模型是欄），而且有兩個對齊陷阱：
      1. rowspan：PRICING 那格 rowspan=3，所以群組第一列比後兩列多一個 <td>。
         因此不能用固定索引，要先找到標籤那格，再取它「之後」的 td。
      2. colspan：CONTEXT LENGTH 的值只有一格、colspan=2，代表兩個模型共用同一
         個值 —— 要複製成每欄一份，不是只給第一個模型。
    """
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        for index, cell in enumerate(cells):
            if _cell_text(cell) != label:
                continue

            values = cells[index + 1:]
            if not values:
                return []

            # 單一格橫跨所有模型 → 所有模型共用這個值。
            if len(values) == 1 and int(values[0].get("colspan", 1)) >= model_count > 1:
                return [_cell_text(values[0])] * model_count

            return [_cell_text(v) for v in values]
    return []


def _parse(html: str) -> list[dict[str, Any]]:
    soup = base.soup_of(html)
    table = soup.find("table")
    if table is None:
        raise base.FetchError("定價頁找不到表格，頁面結構可能改了")

    names = _row_values(table, MODEL_ROW, 0)
    if not names:
        raise base.FetchError(f"定價表找不到 {MODEL_ROW} 那一列，頁面結構可能改了")

    count = len(names)
    inputs = _row_values(table, INPUT_ROW, count)
    outputs = _row_values(table, OUTPUT_ROW, count)
    contexts = _row_values(table, CONTEXT_ROW, count)

    if not inputs or not outputs:
        raise base.FetchError(
            f"定價表找不到「{INPUT_ROW}」或「{OUTPUT_ROW}」那一列，頁面結構可能改了"
        )

    models = []
    for i, name in enumerate(names):
        # 名稱帶註腳編號，例如 'deepseek-v4-flash (1)'。
        model_id = name.split("(")[0].strip()
        if not model_id:
            continue

        raw_input = inputs[i] if i < len(inputs) else ""
        raw_output = outputs[i] if i < len(outputs) else ""
        raw_context = contexts[i] if i < len(contexts) else ""

        models.append(
            base.make_model(
                model_id=model_id,
                display_name=model_id,
                input_price_per_mtok=base.to_mtok(base.parse_price(raw_input)),
                output_price_per_mtok=base.to_mtok(base.parse_price(raw_output)),
                context_window=base.parse_context_window(raw_context),
                source_url=PRICING_URL,
                raw={
                    "label": name,
                    "input_cache_miss": raw_input,
                    "output": raw_output,
                    "context": raw_context,
                },
            )
        )

    return models


def collect() -> base.ProviderData:
    html = base.get_text(PRICING_URL)

    # Docusaurus 對任何路徑都回 200 + 首頁 HTML，狀態碼完全不能信。
    # 斷言價格表真的在頁面上，否則寧可整家標 failed 沿用上次資料。
    if INPUT_ROW not in html:
        raise base.FetchError(
            f"頁面內容沒有「{INPUT_ROW}」，多半是 Docusaurus 對不存在的路徑回了首頁"
        )

    data = base.ProviderData(
        display_name=DISPLAY_NAME,
        pricing_url=PRICING_URL,
        news_url=NEWS_URL,
        models=_parse(html),
        policy_pages=[base.policy_page(label, url) for label, url in POLICY_PAGES],
    )
    data.notes.append("輸入價為 cache miss 標準價；cache hit 折扣價不在追蹤範圍，請點官方連結查看。")
    return data
