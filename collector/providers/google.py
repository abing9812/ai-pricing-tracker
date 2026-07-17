"""Google（Gemini API）解析器。

來源：https://ai.google.dev/gemini-api/docs/pricing

實測重點（2026-07）：
  - 規格書給的 https://ai.google.dev/pricing 現行網址是 /gemini-api/docs/pricing。
  - ⚠ 千萬不要送瀏覽器 UA。送 Chrome UA 會被 302 進 devsite 的 OAuth 登入流程
    （最後落到 accounts.google.com），拿回一張完全沒有價格的頁面。用 requests
    的預設 UA 反而直接 200。這與其他三家的直覺相反，改動前請先看這段。
  - 沒有 JSON 端點。ListModels API（generativelanguage.googleapis.com）無金鑰會
    403，而且它只有 token 上限、沒有價格，無法取代 HTML 解析。
  - 好消息是價格是 server-rendered，原始 HTML 裡就有，不需要無頭瀏覽器。

已知缺口：定價頁沒有 context window，models 頁的規格表是 client-rendered
（原始 HTML 的 <table> 數為 0），無金鑰的情況下拿不到 → 標 unavailable（見 README）。
"""

from __future__ import annotations

import re
from typing import Any

from . import base

PROVIDER_ID = "google"
DISPLAY_NAME = "Google Gemini"
PRICING_URL = "https://ai.google.dev/gemini-api/docs/pricing"
NEWS_URL = "https://ai.google.dev/gemini-api/docs/changelog"

POLICY_PAGES = [
    ("Prohibited use policy", "https://policies.google.com/terms/generative-ai/use-policy"),
]

# devsite 看到瀏覽器 UA 會轉址到 OAuth；預設的 python-requests UA 直接 200。
PLAIN_UA = {"User-Agent": "python-requests"}

# 第 2 欄是 Free Tier、第 3 欄才是付費價，欄序寫死會抓到「Free of charge」。
PAID_COLUMN = 2

_INPUT_LABEL = re.compile(r"^input price", re.I)
_OUTPUT_LABEL = re.compile(r"^output price", re.I)

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


def _standard_tables(html: str) -> list[tuple[str, Any]]:
    """找出每個模型的 standard 分級表格，回傳 [(model_id, table)]。

    定位方式是從表格往回找，而不是從 h2 往下找：表格被包在
    <devsite-selector><section> 裡，不是 h2 的兄弟節點，順著 h2 的 siblings
    走永遠走不到。往回找最近的 h3（分級）與 h2（模型）才可靠。

    tier 的 h3 id 會加流水號（standard, standard_1 … standard_20）且不與模型
    對應，所以只能用前綴判斷，不能拿它配對模型。全頁 71 張表裡 standard 只有
    21 張，其餘是 batch / flex / priority —— 那些不在追蹤範圍（規格 §5），
    且價格較低，混進來會直接覆蓋掉標準價。
    """
    soup = base.soup_of(html)
    found = []

    for table in soup.find_all("table", class_="pricing-table"):
        tier = table.find_previous("h3", id=True)
        model = table.find_previous("h2", id=True)
        if not tier or not model:
            continue
        if not tier["id"].startswith("standard"):
            continue
        found.append((model["id"], table))

    return found


def _rows(table: Any) -> list[list[str]]:
    return [
        [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        for tr in table.find_all("tr")
    ]


def _paid_cell(cells: list[str]) -> str | None:
    return cells[PAID_COLUMN] if len(cells) > PAID_COLUMN else None


def _parse(html: str) -> tuple[list[dict[str, Any]], list[str]]:
    models: list[dict[str, Any]] = []
    notes: list[str] = []

    for model_id, table in _standard_tables(html):
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
        raw: dict[str, Any] = {}

        def price_of(cell: str, field: str) -> float | None:
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
        unavailable = ("context_window",) + tuple(non_token)
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
                raw=raw,
                unavailable_fields=unavailable,
            )
        )

    return models, notes


def collect() -> base.ProviderData:
    html = base.get_text(PRICING_URL, headers=PLAIN_UA)

    if "accounts.google.com" in html[:4000] or "oauth2authorize" in html[:4000]:
        raise base.FetchError(
            "定價頁被導向 Google 登入頁（多半是 UA 被判定為瀏覽器），沒有拿到價格"
        )

    models, notes = _parse(html)

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
