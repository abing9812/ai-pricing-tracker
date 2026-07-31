"""共用抓取／解析介面。

每個 provider 模組需提供：

    PROVIDER_ID, DISPLAY_NAME, PRICING_URL, NEWS_URL
    def collect() -> ProviderData

`collect()` 可以直接拋例外，main.py 會逐家用 try/except 隔離，
一家壞掉不影響其他家。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests
from bs4 import BeautifulSoup

TIMEOUT = 30
RETRIES = 3

# 多家定價頁對預設的 requests UA 直接回 403，一律偽裝成瀏覽器。
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class FetchError(Exception):
    """抓取失敗（網路錯誤、非 2xx、逾時）。"""


@dataclass
class ProviderData:
    """單一 provider 一次抓取的產出。"""

    display_name: str
    pricing_url: str
    news_url: str
    fetch_status: str = "ok"  # ok | partial | failed
    models: list[dict[str, Any]] = field(default_factory=list)
    policy_pages: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        out = {
            "display_name": self.display_name,
            "pricing_url": self.pricing_url,
            "news_url": self.news_url,
            "fetch_status": self.fetch_status,
            "models": self.models,
            "policy_pages": self.policy_pages,
        }
        if self.notes:
            out["notes"] = self.notes
        return out


def http_get(url: str, *, headers: dict[str, str] | None = None) -> requests.Response:
    """GET 一個網址，失敗時退避重試，最後仍失敗就拋 FetchError。"""
    merged = dict(BROWSER_HEADERS)
    if headers:
        merged.update(headers)

    last_err: Exception | None = None
    for attempt in range(RETRIES):
        try:
            resp = requests.get(url, headers=merged, timeout=TIMEOUT)
            resp.raise_for_status()
            # content-type 沒寫 charset 時 requests 會退回 ISO-8859-1，把 UTF-8 頁面
            # 解成亂碼（DeepSeek 的定價頁就是這樣）。讓它照內容猜，猜不出才用 UTF-8。
            if "charset" not in resp.headers.get("content-type", "").lower():
                resp.encoding = resp.apparent_encoding or "utf-8"
            return resp
        except Exception as exc:  # noqa: BLE001 - 重試後統一轉成 FetchError
            last_err = exc
            if attempt < RETRIES - 1:
                time.sleep(2 ** attempt)
    raise FetchError(f"GET {url} 失敗：{last_err}")


def get_text(url: str, **kwargs: Any) -> str:
    return http_get(url, **kwargs).text


def get_json(url: str, **kwargs: Any) -> Any:
    headers = {"Accept": "application/json,text/plain,*/*"}
    headers.update(kwargs.pop("headers", None) or {})
    return http_get(url, headers=headers, **kwargs).json()


def get_markdown(url: str, **kwargs: Any) -> str:
    """抓一份 Markdown，並驗證 content-type 真的是 markdown。

    只看 HTTP 200 會被兩種行為靜默毒害：
      - OpenAI 的 /api/docs/models/*.md 回 200 但 content-type 是 text/html（整頁 HTML）
      - DeepSeek 的 Docusaurus 對任何不存在的路徑都回 200 + 首頁 HTML
    兩者都會讓解析器拿到一份看似正常、實則毫無價格的文件。
    """
    resp = http_get(url, **kwargs)
    ctype = resp.headers.get("content-type", "")
    if "markdown" not in ctype:
        raise FetchError(f"{url} 回傳的不是 markdown（content-type: {ctype or '未提供'}）")
    return resp.text


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def slugify(text: str) -> str:
    """把模型顯示名轉成穩定的 id：'Claude Opus 4.8' → 'claude-opus-4.8'。"""
    slug = re.sub(r"[^a-z0-9.]+", "-", text.lower())
    return slug.strip("-")


def strip_markup(text: str) -> str:
    """去掉 markdown 連結與 MDX 標籤，保留可讀文字。

    '[through August 31](/docs/x)' → 'through August 31'
    '<Tooltip content="…">1M tokens</Tooltip>' → '1M tokens'
    """
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def markdown_tables(md: str) -> list[list[list[str]]]:
    """把 markdown 內所有管線表格解析成 [表格][列][格] 的巢狀列表。

    分隔列（|---|---|）會被丟掉，每格已 strip_markup 過。
    """
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []

    for line in md.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if all(set(c) <= set("-: ") and c for c in cells):
                continue  # 分隔列
            current.append([strip_markup(c) for c in cells])
        elif current:
            tables.append(current)
            current = []

    if current:
        tables.append(current)
    return tables


def next_data(html: str) -> dict[str, Any] | None:
    """取出 Next.js 頁面內嵌的 __NEXT_DATA__ JSON，沒有就回 None。"""
    soup = soup_of(html)
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except json.JSONDecodeError:
        return None


def visible_text(html: str) -> str:
    """抽出頁面的可見文字並正規化空白。

    政策頁雜湊用這個而非整份 HTML：build id、nonce、廣告碼每次都變，
    直接雜湊 HTML 會天天誤報政策變動。
    """
    soup = soup_of(html)
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def to_mtok(value: float | None, unit: str = "per_mtok") -> float | None:
    """把價格換算成「每百萬 token USD」。

    unit: per_mtok（已是每百萬）、per_ktok（每千）、per_token（每個）
    """
    if value is None:
        return None
    factors = {"per_mtok": 1.0, "per_ktok": 1_000.0, "per_token": 1_000_000.0}
    if unit not in factors:
        raise ValueError(f"未知的價格單位：{unit}")
    return round(value * factors[unit], 6)


def parse_price(text: str | None) -> float | None:
    """從 '$2.50' / '2.50 USD' / '$1.25 / 1M tokens' 這類字串抓出數字。"""
    if not text:
        return None
    match = re.search(r"\$?\s*(\d+(?:,\d{3})*(?:\.\d+)?)", text)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def parse_context_window(text: str | None) -> int | None:
    """把 '200K' / '1M' / '128,000 tokens' 解析成整數 token 數。"""
    if not text:
        return None
    cleaned = text.replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*([KMkm])?", cleaned)
    if not match:
        return None
    value = float(match.group(1))
    suffix = (match.group(2) or "").upper()
    multiplier = {"K": 1_000, "M": 1_000_000, "": 1}[suffix]
    return int(value * multiplier)


def make_model(
    *,
    model_id: str,
    display_name: str,
    input_price_per_mtok: float | None,
    output_price_per_mtok: float | None,
    context_window: int | None,
    source_url: str,
    currency: str = "USD",
    modality: str | None = None,
    raw: dict[str, Any] | None = None,
    unavailable_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    """組一筆模型資料。

    欄位有三種狀態：
      ok           — 解析到值
      needs_review — 該來源有這個欄位，但這次沒解析到（可能是頁面改版）→ 進待覆核區
      unavailable  — 官方來源根本沒有公佈這個欄位（已知的永久缺口）→ 不進待覆核區

    分這兩種是刻意的：OpenAI 與 Google 的定價頁沒有 context window，若把它們
    當「解析失敗」，待覆核區會永遠塞著幾十筆點過去也沒用的項目，真正的異常
    反而被淹掉。已知缺口記在 README，不佔用人工注意力。
    """
    values = {
        "input_price_per_mtok": input_price_per_mtok,
        "output_price_per_mtok": output_price_per_mtok,
        "context_window": context_window,
    }

    def status(key: str, val: Any) -> str:
        if val is not None:
            return "ok"
        return "unavailable" if key in unavailable_fields else "needs_review"

    model = {
        "id": model_id,
        "display_name": display_name,
        **values,
        "currency": currency,
        "source_url": source_url,
        "field_status": {key: status(key, val) for key, val in values.items()},
    }
    if modality:
        # 只在非預設（文字）時記錄，例如 "image"。儀表板據此顯示「繪圖」標籤。
        model["modality"] = modality
    if raw:
        # 保留原始值以便查錯（規格 §8：單位標準化後仍要能回溯）。
        model["raw"] = raw
    return model


def policy_page(label: str, url: str) -> dict[str, Any]:
    """抓一個政策頁並算內容雜湊；抓不到時 content_hash 為 None（由 diff 標 needs_review）。"""
    try:
        text = visible_text(get_text(url))
        return {"label": label, "url": url, "content_hash": sha256_text(text)}
    except Exception as exc:  # noqa: BLE001 - 政策頁失敗不該拖垮定價抓取
        return {
            "label": label,
            "url": url,
            "content_hash": None,
            "fetch_error": str(exc),
        }
