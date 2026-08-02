"""比對與變動偵測邏輯（規格 §6）。

核心原則：絕不用空資料覆蓋好資料。抓取失敗、模型消失、數字離譜時，
一律沿用上次的值並標 needs_review，交給人工三秒確認。

模型從來源消失時會標 status="missing" + missing_since，資料照樣保留；
removed_model 事件只在消失的第一天發一次。人工確認真的下架後，把那筆從
current.json 刪掉即可，儀表板會據此把該事件改顯示成「已確認下架」。
"""

from __future__ import annotations

from typing import Any

PRICE_FIELDS = ("input_price_per_mtok", "output_price_per_mtok")

# 單次變動超過這個比例，多半是解析錯位而非真的調價。
ANOMALY_RATIO = 0.5

# 模型數量掉到上次的這個比例以下，視為「明顯減少」。
MODEL_COUNT_RETAIN_RATIO = 0.75

# 來源已經看不到、但資料仍保留待人工確認的模型，標在 model["status"]。
# 沒有這個標記的模型就是正常在架上（不寫 status 欄位，維持既有 schema）。
MISSING = "missing"


def _flag(item: dict[str, Any], reason: str, *, sticky: bool = False) -> None:
    """標記一個項目需覆核，理由累加不重複。

    sticky=True 是「事件型」理由：某個時間點發生了一件事，需要人看一眼
    （價格暴跌、政策頁改內容）。這種理由下次比對不會再算出來一次 ——
    價格已經寫回 current.json、雜湊已經對上 —— 旗標會自己消失。
    排程改成 12 小時後這個空窗只剩半天，晚上 20:00 抓到的事情，
    早上 08:00 那次一跑就清掉了，開儀表板時已經看不到。
    所以事件型理由額外記進 pending_reviews 帶到下次，直到人工用
    `python collector/ack.py` 確認為止。

    其餘是「條件型」理由（解析不到值、抓取失敗、模型未再出現），每次比對都會
    重新判定，狀況修好就自己消失，不需要也不應該留著。
    """
    item["needs_review"] = True
    reasons = item.setdefault("review_reasons", [])
    if reason not in reasons:
        reasons.append(reason)

    if sticky:
        pending = item.setdefault("pending_reviews", [])
        if reason not in pending:
            pending.append(reason)


def _unalias(item: dict[str, Any]) -> None:
    """dict() 淺拷貝後兩個理由 list 還跟來源共用，另開一份免得 _flag 改到來源資料。"""
    for key in ("review_reasons", "pending_reviews"):
        if key in item:
            item[key] = list(item[key])


def _carry_pending(item: dict[str, Any], prev: dict[str, Any]) -> None:
    """把上次還沒被人工確認的事件型理由帶到這次。"""
    for reason in prev.get("pending_reviews", []):
        _flag(item, reason, sticky=True)
    if prev.get("acknowledged_at"):
        item["acknowledged_at"] = prev["acknowledged_at"]


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

    # model 是這次剛解析出來的新 dict，不帶 status/missing_since——
    # 所以之前標 missing 的模型只要再出現，標記就自動消失，不必額外清理。
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

        _carry_pending(model, prev)

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
                    sticky=True,
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
        # 條件型理由整份換掉，不沿用上次的：
        #   - dict() 是淺拷貝，沿用等於跟 prev 共用同一個 list，_flag 會改到來源資料。
        #   - 上次的理由多半已經過期（例如連續失敗幾天留下的「本次抓取失敗」），
        #     這次是解析成功但模型不見了，兩者混在一起會看不出真正該確認什麼。
        # 事件型（pending_reviews）則要留著，那是還沒有人確認過的東西。
        kept.pop("review_reasons", None)
        kept.pop("pending_reviews", None)
        _carry_pending(kept, prev)

        # 保留下來的模型會一直留在 prev 裡，隔天比對又是「上次有、這次沒有」。
        # 事件若不認這個標記，changelog 會天天多一筆同樣的下架通知，唯一的
        # 止血方式變成人工編 current.json（2026-07-31 的 o1-mini 就是這樣收掉的）。
        first_time = prev.get("status") != MISSING
        kept["status"] = MISSING
        kept["missing_since"] = prev.get("missing_since", today)
        _flag(
            kept,
            f"{kept['missing_since']} 起來源就沒再出現，已保留舊資料待確認是否真的下架",
        )
        merged.append(kept)

        if not baseline and first_time:
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
        if prev:
            _carry_pending(page, prev)

        if page.get("content_hash") is None:
            # 抓不到 → 沿用上次雜湊，不要誤判成「政策變動」。
            if prev:
                page["content_hash"] = prev.get("content_hash")
                page["last_changed"] = prev.get("last_changed")
                # 口徑要跟雜湊一起沿用，否則下次成功抓取會拿新口徑去比舊雜湊。
                page["hash_method"] = prev.get("hash_method")
            _flag(page, "政策頁抓取失敗，無法比對")
            merged.append(page)
            continue

        if prev is None or prev.get("content_hash") is None:
            page["last_changed"] = today
        elif prev.get("hash_method") != page.get("hash_method"):
            # 雜湊口徑換了（例如改成只算主文），舊雜湊算的是別的東西。
            # 這時候「不一樣」是我們自己造成的，不是政策改了 —— 靜靜收下當新基準，
            # 不發事件也不標覆核，否則換一次口徑就會四家一起假警報。
            page["last_changed"] = prev.get("last_changed", today)
        elif prev["content_hash"] != page["content_hash"]:
            page["last_changed"] = today
            _flag(page, f"{today} 政策頁內容有變動，請人工檢視", sticky=True)
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
            _unalias(item)
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
