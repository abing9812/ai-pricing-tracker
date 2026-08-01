"""人工確認：看過待覆核項目之後，把旗標清掉。

用法：
    python collector/ack.py                        # 列出目前所有待覆核項目
    python collector/ack.py openai/gpt-5.6-luna    # 確認一筆模型
    python collector/ack.py google/"Prohibited use policy"   # 確認一個政策頁
    python collector/ack.py openai                 # 確認 OpenAI 底下全部
    python collector/ack.py --all                  # 全部確認
    python collector/ack.py --all --dry-run        # 只看會清掉什麼，不寫檔

為什麼需要這支：
  待覆核理由分兩種。**條件型**（解析不到值、抓取失敗、模型未再出現）每次比對都
  會重新判定，狀況修好就自己消失 —— 這種確認了也會再回來，本來就該再回來。
  **事件型**（價格暴跌、政策頁改內容）只在事發那次算得出來，下次比對價格已經
  寫回 current.json、雜湊已經對上，旗標就自己消失了。排程改成 12 小時一次之後
  這個空窗只剩半天，晚上 20:00 抓到的事情，早上開儀表板時已經被清掉。
  所以事件型理由會留在 pending_reviews 裡跨次帶著，等你跑這支確認。

確認的是「我看過了」，不是「這筆沒問題」。真的抓錯了就去修解析器，
真的下架了就把該筆從 data/current.json 刪掉（見 README）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CURRENT_PATH = ROOT / "data" / "current.json"
DOCS_CURRENT_PATH = ROOT / "docs" / "data" / "current.json"


def _label(item: dict[str, Any]) -> str:
    """模型看 id、政策頁看 label —— 兩者都是使用者在命令列打得出來的字。"""
    return item.get("id") or item.get("label") or "?"


def _pending_items(data: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """列出所有待覆核項目：(provider_id, 目標字串, 項目本身)。"""
    found: list[tuple[str, str, dict[str, Any]]] = []
    for pid, provider in (data.get("providers") or {}).items():
        if provider.get("needs_review"):
            found.append((pid, pid, provider))
        for item in list(provider.get("models") or []) + list(provider.get("policy_pages") or []):
            if item.get("needs_review"):
                found.append((pid, f"{pid}/{_label(item)}", item))
    return found


def _matches(target: str, pid: str, key: str) -> bool:
    """`openai` 命中整家（含底下每一筆）；`openai/o3` 只命中那一筆。"""
    if target == pid:
        return True
    return target.casefold() == key.casefold()


def _clear(item: dict[str, Any], today: str) -> list[str]:
    """清掉一筆的旗標，回傳被清掉的理由（供列印）。"""
    reasons = list(item.get("review_reasons") or [])
    for key in ("needs_review", "review_reasons", "pending_reviews"):
        item.pop(key, None)
    item["acknowledged_at"] = today
    return reasons


def _reconcile(data: dict[str, Any]) -> None:
    """整家的旗標是衍生的（底下有任何一筆待覆核就亮）。

    只確認了某一筆模型時，整家那個旗標還會留在 current.json 裡，下次比對才會
    重算 —— 中間這段時間儀表板會顯示「整家資料待覆核」卻點不出是哪筆。這裡補算。
    """
    for provider in (data.get("providers") or {}).values():
        if provider.get("review_reasons"):
            continue  # 整家自己就有理由（抓取失敗、模型數量驟減…），不能清
        children = list(provider.get("models") or []) + list(provider.get("policy_pages") or [])
        if not any(child.get("needs_review") for child in children):
            provider.pop("needs_review", None)


def main() -> int:
    parser = argparse.ArgumentParser(description="把待覆核旗標標成已人工確認")
    parser.add_argument(
        "targets",
        nargs="*",
        help="要確認的項目：provider（整家）或 provider/名稱（單筆）。不給就只列出清單。",
    )
    parser.add_argument("--all", action="store_true", help="確認目前所有待覆核項目")
    parser.add_argument("--dry-run", action="store_true", help="只印會清掉什麼，不寫檔")
    args = parser.parse_args()

    if not CURRENT_PATH.exists():
        print(f"找不到 {CURRENT_PATH}，先跑一次 collector/main.py", file=sys.stderr)
        return 2

    data = json.loads(CURRENT_PATH.read_text(encoding="utf-8"))
    pending = _pending_items(data)

    if not pending:
        print("目前無待覆核項目。")
        return 0

    if not args.all and not args.targets:
        print(f"待覆核（{len(pending)} 筆）：\n")
        for _pid, key, item in pending:
            sticky = set(item.get("pending_reviews") or [])
            print(f"  {key}")
            if not item.get("review_reasons"):
                # 整家的旗標多半是衍生的（底下有東西待覆核），本身沒有理由。
                print("      · 底下有待覆核項目；確認完那幾筆，這行會自動消失")
            for reason in item.get("review_reasons") or []:
                # 條件型的清了也會回來（狀況還在），先講清楚免得以為沒生效。
                kind = "事件型，確認後不再出現" if reason in sticky else "條件型，狀況還在就會再出現"
                print(f"      · {reason}")
                print(f"        （{kind}）")
        print("\n確認：python collector/ack.py <上面的名稱>   全部確認：--all")
        return 0

    today = datetime.now(timezone.utc).date().isoformat()
    hit: list[str] = []
    for pid, key, item in pending:
        if args.all or any(_matches(t, pid, key) for t in args.targets):
            print(f"  {key}")
            for reason in _clear(item, today):
                print(f"      ✓ {reason}")
            hit.append(key)

    if not hit:
        print("沒有命中任何待覆核項目。跑一次不帶參數的 ack.py 看看有哪些。", file=sys.stderr)
        return 1

    _reconcile(data)

    if args.dry_run:
        print(f"\n[dry-run] 不寫檔。會確認 {len(hit)} 筆。")
        return 0

    CURRENT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # 儀表板讀的是 docs/ 底下那份，正本改了要一起換，否則畫面不會變。
    DOCS_CURRENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CURRENT_PATH, DOCS_CURRENT_PATH)

    print(f"\n已確認 {len(hit)} 筆，寫回 data/current.json 並鏡像到 docs/data/。記得 commit。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
