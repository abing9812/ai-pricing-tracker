"""進入點：協調抓取、比對、寫檔。

用法：
    python collector/main.py                # 抓四家，比對後寫回 data/
    python collector/main.py --only openai  # 只跑一家，方便單獨調解析器
    python collector/main.py --dry-run      # 只印結果，不寫檔

任何一家出錯都不會讓整包崩潰：該家標 failed、沿用上次資料、繼續跑其他家。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from diff import diff_provider  # noqa: E402
from providers import anthropic, deepseek, google, openai  # noqa: E402

PROVIDERS = [openai, anthropic, google, deepseek]

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CURRENT_PATH = DATA_DIR / "current.json"
CHANGELOG_PATH = DATA_DIR / "changelog.json"

# GitHub Pages 的來源設為 docs/，只服務 docs/ 底下的檔案，
# 儀表板讀不到外面的 data/。所以寫完正本後鏡像一份進去。
DOCS_DATA_DIR = ROOT / "docs" / "data"


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[warn] {path.name} 解析失敗（{exc}），這次當作空的重建", file=sys.stderr)
        return fallback


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def collect_provider(module: Any) -> dict[str, Any]:
    """跑一家的 collect()，失敗就回一個 failed 的殼（資料由 diff 沿用上次的）。"""
    try:
        data = module.collect()
        if not data.models and data.fetch_status == "ok":
            data.fetch_status = "partial"
            data.notes.append("解析後沒有任何模型，可能是頁面結構變了")

        # 重複 id 會讓後面那筆靜默蓋掉前面那筆（例如同一模型的兩段時效價格被
        # 收斂成同一個 id），比對結果會憑空多出價格變動。寧可吵也不要靜默。
        ids = [m["id"] for m in data.models]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            data.fetch_status = "partial"
            data.notes.append(f"解析出重複的模型 id：{', '.join(duplicates)}，請檢查解析器")

        return data.as_dict()
    except Exception as exc:  # noqa: BLE001 - 逐家隔離，一家壞不影響其他家
        print(f"[error] {module.PROVIDER_ID} 抓取失敗：{exc}", file=sys.stderr)
        return {
            "display_name": module.DISPLAY_NAME,
            "pricing_url": module.PRICING_URL,
            "news_url": module.NEWS_URL,
            "fetch_status": "failed",
            "fetch_error": f"抓取失敗：{exc}",
            "models": [],
            "policy_pages": [],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 模型價格與政策追蹤器")
    parser.add_argument("--only", help="只跑指定的一家（openai/anthropic/google/deepseek）")
    parser.add_argument("--dry-run", action="store_true", help="不寫檔，只印摘要")
    args = parser.parse_args()

    modules = PROVIDERS
    if args.only:
        modules = [m for m in PROVIDERS if m.PROVIDER_ID == args.only]
        if not modules:
            print(f"沒有這家：{args.only}", file=sys.stderr)
            return 2

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    previous = load_json(CURRENT_PATH, {})
    changelog = load_json(CHANGELOG_PATH, [])

    # 種子資料是給儀表板看版面用的假資料，不能拿來當比對基準，
    # 否則第一次真正抓取會噴出一整排假的「降價」。
    if previous.get("seed"):
        print("[info] 上次是種子資料，這次當作首次執行：不產生變動事件、清掉種子 changelog")
        previous = {}
        changelog = []

    prev_providers = previous.get("providers", {})
    providers: dict[str, Any] = {}
    new_events: list[dict[str, Any]] = []

    for module in modules:
        pid = module.PROVIDER_ID
        print(f"[info] 抓取 {pid} …")
        current = collect_provider(module)
        merged, events = diff_provider(pid, prev_providers.get(pid), current, today)
        providers[pid] = merged
        new_events.extend(events)

        status = merged["fetch_status"]
        flag = " ⚠ 待覆核" if merged.get("needs_review") else ""
        print(f"       {status}：{len(merged['models'])} 個模型，{len(events)} 筆變動{flag}")

    # --only 時保留沒跑到的那幾家的舊資料，不要讓它們從畫面上消失。
    for pid, data in prev_providers.items():
        providers.setdefault(pid, data)

    payload = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "providers": providers,
    }
    changelog = changelog + new_events

    for event in new_events:
        print(f"       ↳ {event['type']}: {event.get('model') or event.get('label')}")

    if args.dry_run:
        print("\n[dry-run] 不寫檔。current.json 會長這樣：\n")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    write_json(CURRENT_PATH, payload)
    write_json(CHANGELOG_PATH, changelog)

    DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CURRENT_PATH, DOCS_DATA_DIR / "current.json")
    shutil.copy2(CHANGELOG_PATH, DOCS_DATA_DIR / "changelog.json")

    print(f"\n完成：{len(new_events)} 筆新變動，已寫入 data/ 並鏡像到 docs/data/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
