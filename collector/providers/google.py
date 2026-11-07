"""Google（Gemini API）解析器。

來源：https://aistudio.google.com/docs/pricing.md

為什麼是這個網址（2026-11 實測）：
  - 2026-11-02 文件站整個從 ai.google.dev/gemini-api/docs 搬到 aistudio.google.com/docs，
    舊網址 301 過去。新站的同一個路徑看 UA 回 Markdown 或 client-rendered 的 HTML
    （後者 <table> 數為 0、沒有價格）；.md 網址一律回 Markdown，不必再挑 UA。
  - 舊站「送 Chrome UA 會被導去 OAuth 登入頁」的陷阱在新站不存在了。
  - 沒有 JSON 端點。ListModels API（generativelanguage.googleapis.com）無金鑰會
    403，而且它只有 token 上限、沒有價格，無法取代定價頁。

頁面格式：
  - Markdoc 原始碼：每個模型一段「## 名稱 {% id="模型 id" %}」，價格在 {% table %} 裡。
    表格不是管線表格：列之間用 --- 分隔，每格是一個「* 」項目，內容可以跨行。
  - 分級用 {% tab title="Standard" %} 包起來；只有一種價格的模型（例如 Live 系列）
    沒有 tab，表格直接放在段落裡。

已知缺口：定價頁沒有 context window，models 頁的規格表無金鑰拿不到
→ 標 unavailable（見 README）。
"""

from __future__ import annotations

import re
from typing import Any

from . import base

PROVIDER_ID = "google"
DISPLAY_NAME = "Google Gemini"
PRICING_URL = "https://aistudio.google.com/docs/pricing"
PRICING_MD_URL = "https://aistudio.google.com/docs/pricing.md"
NEWS_URL = "https://aistudio.google.com/docs/changelog"

POLICY_PAGES = [
    ("Prohibited use policy", "https://policies.google.com/terms/generative-ai/use-policy"),
]

# 第 2 欄是 Free Tier、第 3 欄才是付費價，欄序寫死會抓到「Free of charge」。
PAID_COLUMN = 2

# 嵌入模型的輸入列按 modality 拆開，第一列叫「Text input price」而不是「Input price」。
# 只認 ^input price 的話整個模型會被當成「不是模型段落」靜默跳過（gemini-embedding-2
# 就這樣漏抓）。image／audio／video input price 仍不認：那些是分級定價，不追蹤。
_INPUT_LABEL = re.compile(r"^(text )?input price", re.I)
_OUTPUT_LABEL = re.compile(r"^output price", re.I)

# 付費欄明寫「Not available」＝該模型沒有付費層（例如 Gemma 4 只有免費層），
# 那是官方公佈的事實，不是解析失敗 → 標 unavailable，不進待覆核區。
# 只認整格就是這個字：空格或其他讀不懂的內容仍要標 needs_review。
_NOT_AVAILABLE = re.compile(r"^\s*not available\s*$", re.I)

# 表頭寫「per 1M tokens in USD」，但個別格子可能偷渡別的單位，例如
# gemini-2.5-flash-image 的輸出價是「$0.039 per image」。直接吃下去會在儀表板上
# 變成便宜到荒謬的每百萬 token 單價 —— 寧可標成未知，也不要放一個錯的數字。
#
# 只檢查第一個數字自己的描述（見 _first_price），不能整格搜：
#   「$0.30 (text / image)」的 image 是 modality 清單，不是單位；
#   「$2.00 (text/image), equivalent to $0.0011 per image」的主價格是每百萬 token，
#   per image 是後面那個數字的附註 —— 攔下這兩個都會把好資料丟掉。
_PER_UNIT = re.compile(
    r"\bper\s+(image|second|minute|hour|character|request|prompt|video|page)", re.I
)
_PRICE = re.compile(r"\$\s*\d[\d,]*(?:\.\d+)?")


def _first_price(cell: str) -> tuple[float | None, bool]:
    """回傳（第一個價格, 該價格是否為非 token 單位）。

    一格常內嵌多個值（依 modality 或 ≤/>200k 分段），取第一個 = 最基礎的那個，
    完整原文留在 raw 供查證。第一個數字的描述範圍 = 它到下一個 $ 之間。
    """
    match = _PRICE.search(cell)
    if not match:
        return None, False

    rest = cell[match.end():]
    nxt = rest.find("$")
    segment = rest if nxt == -1 else rest[:nxt]

    if _PER_UNIT.search(segment):
        return None, True
    return base.parse_price(match.group(0)), False


_SECTION = re.compile(r'^##\s+(?P<title>.+?)\s*\{%\s*id="(?P<id>[^"]+)"\s*%\}\s*$', re.M)
_TAB = re.compile(r'\{%\s*tab\s+title="(?P<title>[^"]+)"\s*%\}')
_TAB_END = re.compile(r"\{%\s*/tab\s*%\}")
_TABLE = re.compile(r"\{%\s*table\s*%\}(?P<body>.*?)\{%\s*/table\s*%\}", re.S)
_ROW_SEPARATOR = re.compile(r"^\s*---\s*$", re.M)
_CELL_START = re.compile(r"^(?P<indent>[ \t]*)\*\s+(?P<text>.*)$")
_MARKDOC_TAG = re.compile(r"\{%.*?%\}")
TIER = "Standard"


def _standard_tables(md: str) -> list[tuple[str, str]]:
    """找出每個模型的 standard 分級表格，回傳 [(model_id, table 原始碼)]。

    先切段落再找分頁：同一段裡 Batch / Flex / Priority 的表格式完全相同，價格較低，
    混進來會直接蓋掉標準價（規格 §5 不追蹤）。段落有分頁卻沒有 Standard 時寧可
    跳過，也不要退回去抓第一張表 —— 那張可能是別的分級。
    """
    found = []
    headings = list(_SECTION.finditer(md))

    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(md)
        section = md[heading.end():end]

        tabs = list(_TAB.finditer(section))
        if tabs:
            standard = next((t for t in tabs if t["title"] == TIER), None)
            if standard is None:
                continue
            closing = _TAB_END.search(section, standard.end())
            section = section[standard.end(): closing.start() if closing else len(section)]

        table = _TABLE.search(section)
        if table:
            found.append((heading["id"], table["body"]))

    return found


def _clean_cell(lines: list[str]) -> str:
    # 反斜線是 Markdoc 的強制換行，原文留在 raw 裡只會多一串 \\ 雜訊。
    return base.strip_markup(_MARKDOC_TAG.sub("", " ".join(lines)).replace("\\", " "))


def _rows(table: str) -> list[list[str]]:
    """把 Markdoc 表格拆成 [列][格]。

    每一列用 --- 分隔，一格從「* 」開始，到下一個同縮排的「* 」為止：格子內容可以
    跨行（分段計價常見），也可能自己帶一層更深的項目符號，所以只認第一格的縮排。
    """
    rows: list[list[str]] = []

    for chunk in _ROW_SEPARATOR.split(table):
        cells: list[list[str]] = []
        indent: str | None = None

        for line in chunk.splitlines():
            start = _CELL_START.match(line)
            if start and (indent is None or start["indent"] == indent):
                indent = start["indent"]
                cells.append([start["text"]])
            elif cells:
                cells[-1].append(line.strip())

        if cells:
            rows.append([_clean_cell(cell) for cell in cells])

    return rows


def _paid_cell(cells: list[str]) -> str | None:
    return cells[PAID_COLUMN] if len(cells) > PAID_COLUMN else None


def _parse(md: str) -> tuple[list[dict[str, Any]], list[str]]:
    models: list[dict[str, Any]] = []
    notes: list[str] = []

    for model_id, table in _standard_tables(md):
        rows = _rows(table)
        if not rows:
            continue

        # 表頭寫明「Paid Tier, per 1M tokens in USD」。萬一哪天改成按張／按秒計價，
        # 寧可整個跳過也不要把 $0.30/張 當成 $0.30/1M token 存進去。
        header = " ".join(rows[0]).lower()
        if "1m token" not in header:
            notes.append(f"{model_id} 的計價單位不是每百萬 token（表頭：{' | '.join(rows[0])}），已略過。")
            continue

        input_price = output_price = None
        has_input_row = has_output_row = False
        non_token: list[str] = []
        not_offered: list[str] = []
        raw: dict[str, Any] = {}

        def price_of(cell: str, field: str) -> float | None:
            if _NOT_AVAILABLE.match(cell):
                not_offered.append(field)
                return None
            price, other_unit = _first_price(cell)
            if other_unit:
                non_token.append(field)
            return price

        for cells in rows[1:]:
            if not cells:
                continue
            label = cells[0]
            paid = _paid_cell(cells)
            if paid is None:
                continue

            if _INPUT_LABEL.match(label):
                has_input_row = True
                input_price, raw["input"] = price_of(paid, "input_price_per_mtok"), paid
            elif _OUTPUT_LABEL.match(label):
                has_output_row = True
                output_price, raw["output"] = price_of(paid, "output_price_per_mtok"), paid

        if not has_input_row and not has_output_row:
            continue  # 這個 h2 不是模型段落

        # 嵌入模型只收輸入、表上根本沒有 Output price 那一列，那是事實不是解析失敗。
        unavailable = ("context_window",) + tuple(non_token) + tuple(not_offered)
        if not has_output_row:
            unavailable += ("output_price_per_mtok",)

        models.append(
            base.make_model(
                model_id=model_id,
                display_name=model_id,
                input_price_per_mtok=base.to_mtok(input_price),
                output_price_per_mtok=base.to_mtok(output_price),
                context_window=None,
                source_url=f"{PRICING_URL}#{model_id}",
                # 繪圖模型的 h2 id 都帶 image（gemini-*-image / *-image-preview），
                # 靠 id 判斷就夠；Imagen 按張計價、根本進不了這個解析器，不會誤標。
                modality="image" if "image" in model_id else None,
                raw=raw,
                unavailable_fields=unavailable,
            )
        )

    return models, notes


def collect() -> base.ProviderData:
    models, notes = _parse(base.get_markdown(PRICING_MD_URL))

    if not models:
        # 頁面拿到了卻一個模型都解析不出來，代表格式又改了。寧可整家 failed 讓 diff
        # 沿用舊資料，也不要回傳空清單 —— 2026-11-02 就這樣噴了整批假下架事件。
        raise base.FetchError("定價頁解析出 0 個模型，頁面結構可能改了")

    data = base.ProviderData(
        display_name=DISPLAY_NAME,
        pricing_url=PRICING_URL,
        news_url=NEWS_URL,
        models=models,
        policy_pages=[base.policy_page(label, url) for label, url in POLICY_PAGES],
    )
    data.notes.append(
        "context window 官方定價頁未提供；價格取付費層 standard 分級，"
        "多 modality 或分段計價的模型顯示的是第一個（最基礎）數字，詳情請點官方連結。"
    )
    data.notes.extend(notes)
    return data
