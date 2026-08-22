"""DeepSeek 解析器。

來源：https://api-docs.deepseek.com/quick_start/pricing/

實測重點（2026-07）：
  - 規格書給的網址正確，只是會補上尾斜線。不擋機器人，UA 有無皆可。
  - ⚠ 這站是 Docusaurus SPA，**任何不存在的路徑都回 HTTP 200 + 首頁 HTML**
    （/quick_start/pricing.md、/llms.txt、/search-index.json 全中招）。所以絕不能
    用狀態碼判斷成功，一定要驗證內容裡真的有價格表 —— 見 _has_pricing_table()。
    這道斷言要比對**去掉標籤後的可見文字**，不能比對原始 HTML：官方 2026-08-22
    在標籤中間插了一個 <br>，畫面一字未改、價格照樣解析得出，整家卻被誤判成
    抓取失敗。頁面長怎樣是別人的自由，我們只認畫面上讀得到的字。
  - 沒有 JSON 來源，文件原始碼也未公開，只能解析 HTML（好在是 SSG，價格在原始
    bytes 裡）。

2026-08-18 起離峰折扣（off-peak）又回來了：價格區每一列都拆成 OFF-PEAK／PEAK
兩小列。頁尾註腳寫明「Off-peak rates are half of the peak rates」，也就是 PEAK
才是標準價，OFF-PEAK 是時段折扣 —— 與 cache hit 同性質，不在追蹤範圍。

規格 §5 只要標準價：輸入取 CACHE MISS 那一列（不是 CACHE HIT 的快取折扣價），
時段取 PEAK 那一小列（不是 OFF-PEAK 的半價）。
"""

from __future__ import annotations

import re
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

# 價格列底下的時段小列。標準價是 PEAK；OFF-PEAK 是半價折扣，只記進 raw 備查。
PEAK_TIER = "PEAK"
OFF_PEAK_TIER = "OFF-PEAK"


def _cell_text(node: Any) -> str:
    return node.get_text(" ", strip=True)


def _norm(text: str) -> str:
    """比對用的正規化：大寫、空白收斂。標點保留（(CACHE MISS) 要分得出來）。"""
    return re.sub(r"\s+", " ", text).strip().upper()


def _grid(table: Any) -> list[list[Any]]:
    """把 <table> 展開成矩陣，rowspan／colspan 佔到的格子都填同一個 cell 物件。

    這張表是轉置的（模型是欄），而且滿是跨格：PRICING 那格 rowspan=6、價格列
    標籤 rowspan=2、CONTEXT LENGTH 的值 colspan=2。舊寫法是「取標籤那格之後的
    td」逐格數過去，2026-08 價格列多出 OFF-PEAK／PEAK 小列之後整排錯開一格 ——
    第一個模型拿到 'OFF-PEAK' 這個字（解析不到值、沿用舊價），第二個模型拿到的
    卻是第一個模型的離峰價，數字看起來正常、其實是別人的價格。
    展成矩陣後一律用欄位座標對齊，跨格再多層也不會錯位。
    """
    cells: dict[tuple[int, int], Any] = {}
    width = 0
    for row, tr in enumerate(table.find_all("tr")):
        col = 0
        for cell in tr.find_all(["td", "th"]):
            while (row, col) in cells:  # 被上面 rowspan 佔走的位置要跳過
                col += 1
            try:
                span_rows = max(1, int(cell.get("rowspan", 1)))
                span_cols = max(1, int(cell.get("colspan", 1)))
            except (TypeError, ValueError):
                span_rows = span_cols = 1
            for dr in range(span_rows):
                for dc in range(span_cols):
                    cells[(row + dr, col + dc)] = cell
            col += span_cols
            width = max(width, col)

    height = max((r for r, _ in cells), default=-1) + 1
    return [[cells.get((r, c)) for c in range(width)] for r in range(height)]


def _find_label(grid: list[list[Any]], label: str) -> tuple[int, int] | None:
    """找標籤格的座標 (列, 欄)。"""
    wanted = _norm(label)
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if cell is not None and _norm(_cell_text(cell)) == wanted:
                return r, c
    return None


def _model_columns(grid: list[list[Any]]) -> tuple[list[int], list[str]]:
    """從 MODEL 那一列取出每個模型佔的欄位與名稱。"""
    found = _find_label(grid, MODEL_ROW)
    if not found:
        return [], []

    row, col = found
    label_cell = grid[row][col]
    columns: list[int] = []
    names: list[str] = []
    for c in range(col + 1, len(grid[row])):
        cell = grid[row][c]
        if cell is None or cell is label_cell:
            continue  # 標籤自己 colspan 佔到的欄位
        columns.append(c)
        names.append(_cell_text(cell))
    return columns, names


def _row_values(
    grid: list[list[Any]], label: str, columns: list[int], tier: str | None = None
) -> list[str]:
    """取某一列在各模型欄位上的值；tier 指定時取該時段的小列。

    價格列的標籤格 rowspan=2，底下兩小列分別是 OFF-PEAK 與 PEAK，時段標籤就夾在
    標籤格與模型欄位之間。沒有時段小列時（官方哪天取消折扣）就用標籤自己那一列，
    行為與改版前相同。CONTEXT LENGTH 那種一格 colspan 橫跨兩個模型的值，因為矩陣
    上兩欄指向同一個 cell，自然就複製成每欄一份，不必特別處理。
    """
    found = _find_label(grid, label)
    if not found:
        return []

    row, col = found
    label_cell = grid[row][col]
    spanned = [r for r in range(len(grid)) if grid[r][col] is label_cell]
    first_model_col = min(columns) if columns else len(grid[row])

    target_row = row
    if tier is not None:
        wanted = _norm(tier)
        tiers: dict[str, int] = {}
        for r in spanned:
            for c in range(col + 1, first_model_col):
                cell = grid[r][c]
                if cell is None or cell is label_cell:
                    continue
                tiers.setdefault(_norm(_cell_text(cell)), r)
        if tiers:
            if wanted not in tiers:
                # 有時段小列卻找不到指定時段：寧可整家 failed 沿用上次資料，
                # 也不要靜默拿折扣價當標準價。
                raise base.FetchError(
                    "「{}」有時段分列（{}），但找不到「{}」那一小列，計價方式可能又改了".format(
                        label, "、".join(sorted(tiers)), tier
                    )
                )
            target_row = tiers[wanted]

    return [
        _cell_text(grid[target_row][c]) if grid[target_row][c] is not None else ""
        for c in columns
    ]


def _parse(html: str) -> list[dict[str, Any]]:
    soup = base.soup_of(html)
    table = soup.find("table")
    if table is None:
        raise base.FetchError("定價頁找不到表格，頁面結構可能改了")

    grid = _grid(table)
    columns, names = _model_columns(grid)
    if not names:
        raise base.FetchError(f"定價表找不到 {MODEL_ROW} 那一列，頁面結構可能改了")

    inputs = _row_values(grid, INPUT_ROW, columns, tier=PEAK_TIER)
    outputs = _row_values(grid, OUTPUT_ROW, columns, tier=PEAK_TIER)
    contexts = _row_values(grid, CONTEXT_ROW, columns)

    if not inputs or not outputs:
        raise base.FetchError(
            f"定價表找不到「{INPUT_ROW}」或「{OUTPUT_ROW}」那一列，頁面結構可能改了"
        )

    # 離峰價不是追蹤標的，但記進 raw 才查得出「這次到底抓的是哪一格」。
    off_inputs = _row_values(grid, INPUT_ROW, columns, tier=OFF_PEAK_TIER)
    off_outputs = _row_values(grid, OUTPUT_ROW, columns, tier=OFF_PEAK_TIER)
    tiered = off_inputs != inputs or off_outputs != outputs

    models = []
    for i, name in enumerate(names):
        # 名稱帶註腳編號，例如 'deepseek-v4-flash (1)'。
        model_id = name.split("(")[0].strip()
        if not model_id:
            continue

        raw_input = inputs[i] if i < len(inputs) else ""
        raw_output = outputs[i] if i < len(outputs) else ""
        raw_context = contexts[i] if i < len(contexts) else ""

        raw: dict[str, Any] = {
            "label": name,
            "input_cache_miss": raw_input,
            "output": raw_output,
            "context": raw_context,
        }
        if tiered:
            raw["pricing_tier"] = PEAK_TIER
            raw["input_cache_miss_off_peak"] = off_inputs[i] if i < len(off_inputs) else ""
            raw["output_off_peak"] = off_outputs[i] if i < len(off_outputs) else ""

        models.append(
            base.make_model(
                model_id=model_id,
                display_name=model_id,
                input_price_per_mtok=base.to_mtok(base.parse_price(raw_input)),
                output_price_per_mtok=base.to_mtok(base.parse_price(raw_output)),
                context_window=base.parse_context_window(raw_context),
                source_url=PRICING_URL,
                raw=raw,
            )
        )

    return models


def _has_pricing_table(html: str) -> bool:
    """定價表真的在這份 HTML 裡嗎？

    Docusaurus 對任何不存在的路徑都回 200 + 首頁，狀態碼不能信，所以要驗內容。
    但**不能拿原始 HTML 直接 substring 比對**：2026-08-22 官方在標籤正中間插了
    一個 <br>（`1M INPUT TOKENS<br>(CACHE MISS)`），畫面上一個字沒變、表格也照樣
    解析得出正確價格，整家卻被這道關卡判成抓取失敗、沿用舊資料兩天。
    改成比對「去掉標籤後的可見文字」（並套 _norm 收斂空白／大小寫），
    行內標記怎麼加、字母怎麼大小寫都不影響，首頁還是照樣擋得下來。
    """
    return INPUT_ROW in _norm(base.visible_text(html))


def collect() -> base.ProviderData:
    html = base.get_text(PRICING_URL)

    # Docusaurus 對任何路徑都回 200 + 首頁 HTML，狀態碼完全不能信。
    # 斷言價格表真的在頁面上，否則寧可整家標 failed 沿用上次資料。
    if not _has_pricing_table(html):
        raise base.FetchError(
            f"頁面內容沒有「{INPUT_ROW}」，多半是 Docusaurus 對不存在的路徑回了首頁"
        )

    models = _parse(html)
    data = base.ProviderData(
        display_name=DISPLAY_NAME,
        pricing_url=PRICING_URL,
        news_url=NEWS_URL,
        models=models,
        policy_pages=[base.policy_page(label, url) for label, url in POLICY_PAGES],
    )
    data.notes.append("輸入價為 cache miss 標準價；cache hit 折扣價不在追蹤範圍，請點官方連結查看。")
    if any(m.get("raw", {}).get("pricing_tier") for m in models):
        data.notes.append(
            "官方分尖峰／離峰計價，這裡記的是尖峰（標準）價；離峰為半價，適用時段請點官方連結確認。"
        )
    return data
