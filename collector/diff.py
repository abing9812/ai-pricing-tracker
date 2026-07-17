"""比對與變動偵測邏輯（規格 §6）。

核心原則：絕不用空資料覆蓋好資料。抓取失敗、模型消失、數字離譜時，
一律沿用上次的值並標 needs_review，交給人工三秒確認。
"""

from __future__ import annotations

from typing import Any

PRICE_FIELDS = ("input_price_per_mtok", "output_price_per_mtok")

# 單次變動超過這個比例，多半是解析錯位而非真的調價。
ANOMALY_RATIO = 0.5

# 模型數量掉到上次的這個比例以下，視為「明顯減少」。
MODEL_COUNT_RETAIN_RATIO = 0.75


def _flag(item: dict[str, Any], reason: str) -> None:
    """標記一個項目需覆核，理由累加不重複。"""
    item["needs_review"] = True
    reasons = item.setdefault("review_reasons", [])
    if reason not in reasons:
        reasons.append(reason)


def _is_baseline(prev_provider: dict[str, Any] | None) -> bool:
    """這家上次完全沒有紀錄 → 當作基準，不產生變動事件。

    首次執行、上次是種子資料、或日後新增第五家時都會走這裡：整個目錄
    當作既有現況收下，不會噴出一整排假的 new_model。

    判斷條件刻意是「有沒有這家的紀錄」而不是「上次有沒有模型」——
    後者會讓一家只有政策頁、模型還沒解析成功時，政策變動被整個吞掉。
    """
    return not prev_provider


def _diff_models(
    provider_id: str,
    prev_models: list[dict[str, Any]],
    curr_models: list[dict[str, Any]],
    today: str,
    baseline: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prev_by_id = {m["id"]: m for m in prev_models}
    curr_by_id = {m["id"]: m for m in curr_models}
    events: list[dict[str, Any]] = []
    merged: list[dict[str, Any]] = []

    for model in curr_models:
        prev = prev_by_id.get(model["id"])
        model["last_seen"] = today

        if prev is None:
            model["last_changed"] = today
            if not baseline:
                events.append(
                    {
                        "date": today,
                        "provider": provider_id,
                        "model": model["id"],
                        "type": "new_model",
                        "source_url": model["source_url"],
                    }
                )
            merged.append(model)
            continue

        changed = False
        for f in PRICE_FIELDS:
            old, new = prev.get(f), model.get(f)

            if new is None:
                # 官方根本沒公佈這個欄位（例如嵌入模型沒有輸出價）→ 這是事實，
                # 不是解析失敗，別拿它去佔用待覆核區。
                if model["field_status"].get(f) == "unavailable":
                    continue
                # 解析不到 → 沿用舊值，不記變動（規格 §8）。
                model[f] = old
                model["field_status"][f] = "needs_review"
                _flag(model, f"{f} 解析不到值，沿用上次的數字")
                continue

            if old is None or old == new:
                continue

            changed = True
            events.append(
                {
                    "date": today,
                    "provider": provider_id,
                    "model": model["id"],
                    "type": "price_change",
                    "field": f,
                    "old": old,
                    "new": new,
                    "source_url": model["source_url"],
                }
            )
            if old and abs(new - old) / abs(old) > ANOMALY_RATIO:
                _flag(
                    model,
                    f"{f} 單次變動 {old} → {new}，幅度超過 ±{int(ANOMALY_RATIO * 100)}%，疑似解析錯位",
                )

        if (
            model.get("context_window") is None
            and model["field_status"].get("context_window") != "unavailable"
        ):
            model["context_window"] = prev.get("context_window")
            model["field_status"]["context_window"] = "needs_review"
            _flag(model, "context_window 解析不到值，沿用上次的數字")

        model["last_changed"] = today if changed else prev.get("last_changed", today)
        merged.append(model)

    # 上次有、這次沒有 → 不刪，保留舊資料並標 needs_review（多半是解析失敗）。
    for model_id, prev in prev_by_id.items():
        if model_id in curr_by_id:
            continue
        kept = dict(prev)
        _flag(kept, "上次有、這次沒抓到，已保留舊資料待確認是否真的下架")
        merged.append(kept)
        if not baseline:
            events.append(
                {
                    "date": today,
                    "provider": provider_id,
                    "model": model_id,
                    "type": "removed_model",
                    "source_url": prev.get("source_url", ""),
                }
            )

    return merged, events


def _diff_policies(
    provider_id: str,
    prev_pages: list[dict[str, Any]],
    curr_pages: list[dict[str, Any]],
    today: str,
    baseline: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prev_by_url = {p["url"]: p for p in prev_pages}
    events: list[dict[str, Any]] = []
    merged: list[dict[str, Any]] = []

    for page in curr_pages:
        prev = prev_by_url.get(page["url"])

        if page.get("content_hash") is None:
            # 抓不到 → 沿用上次雜湊，不要誤判成「政策變動」。
            if prev:
                page["content_hash"] = prev.get("content_hash")
                page["last_changed"] = prev.get("last_changed")
            _flag(page, "政策頁抓取失敗，無法比對")
            merged.append(page)
            continue

        if prev is None or prev.get("content_hash") is None:
            page["last_changed"] = today
        elif prev["content_hash"] != page["content_hash"]:
            page["last_changed"] = today
            _flag(page, "政策頁內容有變動，請人工檢視")
            if not baseline:
                events.append(
                    {
                        "date": today,
                        "provider": provider_id,
                        "type": "policy_change",
                        "label": page["label"],
                        "source_url": page["url"],
                    }
                )
        else:
            page["last_changed"] = prev.get("last_changed", today)

        merged.append(page)

    return merged, events


def diff_provider(
    provider_id: str,
    prev_provider: dict[str, Any] | None,
    curr_provider: dict[str, Any],
    today: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """比對單一 provider，回傳（合併後狀態, 變動事件）。"""
    prev_provider = prev_provider or {}
    baseline = _is_baseline(prev_provider)
    merged = dict(curr_provider)

    # 抓取整個失敗 → 沿用上次全部資料，只換 fetch_status（規格 §8）。
    if curr_provider["fetch_status"] == "failed":
        merged["models"] = [dict(m) for m in prev_provider.get("models", [])]
        merged["policy_pages"] = [dict(p) for p in prev_provider.get("policy_pages", [])]
        for item in merged["models"] + merged["policy_pages"]:
            _flag(item, "本次抓取失敗，畫面上是上次的資料")
        _flag(merged, curr_provider.get("fetch_error", "抓取失敗"))
        return merged, []

    models, model_events = _diff_models(
        provider_id,
        prev_provider.get("models", []),
        curr_provider.get("models", []),
        today,
        baseline,
    )
    policies, policy_events = _diff_policies(
        provider_id,
        prev_provider.get("policy_pages", []),
        curr_provider.get("policy_pages", []),
        today,
        baseline,
    )
    merged["models"] = models
    merged["policy_pages"] = policies

    if curr_provider["fetch_status"] == "partial":
        _flag(merged, "只抓到部分資料")

    prev_count = len(prev_provider.get("models", []))
    curr_count = len(curr_provider.get("models", []))
    if prev_count and curr_count < prev_count * MODEL_COUNT_RETAIN_RATIO:
        _flag(
            merged,
            f"模型數量由 {prev_count} 掉到 {curr_count}，疑似解析失敗",
        )

    if any(m.get("needs_review") for m in models) or any(
        p.get("needs_review") for p in policies
    ):
        merged.setdefault("needs_review", True)

    return merged, model_events + policy_events


def recent_events(changelog: list[dict[str, Any]], today: str, days: int = 7) -> list[dict[str, Any]]:
    """篩出最近 N 天的變動（儀表板也會自己算一次，這裡供 CLI 摘要用）。"""
    from datetime import date, timedelta

    cutoff = date.fromisoformat(today) - timedelta(days=days)
    return [e for e in changelog if date.fromisoformat(e["date"]) >= cutoff]
