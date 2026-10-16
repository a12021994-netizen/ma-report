# -*- coding: utf-8 -*-
"""
族群 x 大盤 Beta 分析 — 自動產生腳本

用法:
    python beta_report.py

會做的事:
    1. 用 FinMind 抓取 stock_list.py 裡所有個股與大盤指數（加權、櫃買）的歷史股價
    2. 以日報酬計算每檔個股、每個族群（等權組合）對加權、對櫃買的 Beta 與 R²
       （60 日與 250 日兩種回看窗口）
    3. 把資料套進 beta_template.html，輸出到 output/族群Beta分析_日期.html

這支腳本與 generate_report.py（均線強弱頁）完全獨立，只共用 stock_list.py 的族群清單。
抓資料的程式碼刻意複製一份過來而不是 import，這樣改動均線頁的程式不會影響這裡。

需求套件:
    pip install requests pandas

選擇性設定:
    環境變數 FINMIND_TOKEN — 有設定時會帶上 FinMind API token，提高請求額度
"""

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from stock_list import BENCHMARKS, GROUPS

# ---------- 設定 ----------
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "output"
TEMPLATE_FILE = SCRIPT_DIR / "beta_template.html"

BETA_WINDOWS = [60, 250]    # 回看窗口（交易日）
MIN_COVERAGE = 0.8          # 窗口內有效報酬天數至少要有 80% 才計算
TRADING_DAYS_PER_YEAR = 252 # 年化波動用

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_START_DATE = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "").strip()
REQUEST_DELAY_SEC = 0.4     # 每檔股票查詢間隔，避免被限流
FETCH_RETRIES = 3           # 連線逾時或伺服器暫時錯誤時，最多嘗試幾次
FETCH_RETRY_WAIT_SEC = 3    # 每次重試前等待的秒數（第 n 次重試等 n 倍）


# ---------- 抓資料 ----------
def request_finmind(params):
    """呼叫 FinMind API；遇到逾時、連線中斷或伺服器暫時錯誤會自動重試，最後仍失敗就丟出例外"""
    headers = {"Authorization": f"Bearer {FINMIND_TOKEN}"} if FINMIND_TOKEN else {}
    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            resp = requests.get(FINMIND_URL, params=params, headers=headers, timeout=15)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            return resp
        except requests.RequestException as e:
            if attempt == FETCH_RETRIES:
                raise
            print(f"  [重試 {attempt}/{FETCH_RETRIES - 1}] data_id={params['data_id']}: {e}")
            time.sleep(FETCH_RETRY_WAIT_SEC * attempt)


def fetch_close_series(data_id: str):
    """向 FinMind 要指定 data_id 的歷史收盤價，回傳以日期為索引的 Series，抓不到回傳 None"""
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": data_id,
        "start_date": FINMIND_START_DATE,
    }
    try:
        resp = request_finmind(params)
        if resp.status_code != 200:
            return None
        rows = resp.json().get("data", [])
        if not rows:
            return None

        df = pd.DataFrame(rows)
        if "close" not in df.columns:
            return None
        df["date"] = pd.to_datetime(df["date"])
        close = pd.to_numeric(df.set_index("date")["close"], errors="coerce").sort_index()
        close = close[close > 0].dropna()
        close = close[~close.index.duplicated(keep="last")]
        if len(close) < 5:
            return None
        return close
    except Exception as e:
        print(f"  [警告] FinMind data_id={data_id} 抓取失敗: {e}")
        return None


def try_fetch_benchmark(candidates):
    """依序嘗試候選 data_id，回傳 (使用的 data_id, Series) 或 (None, None)"""
    for data_id in candidates:
        close = fetch_close_series(data_id)
        time.sleep(REQUEST_DELAY_SEC)
        if close is not None:
            return data_id, close
    return None, None


# ---------- 計算 ----------
def daily_returns(close):
    """收盤價 → 日報酬（簡單報酬），第一天沒有前一日所以去掉"""
    return close.pct_change().dropna()


def compute_beta(asset_ret, market_ret, window):
    """
    以日期對齊（inner join）後取最近 window 天，計算 beta 與 R²。
    有效天數不足 window * MIN_COVERAGE 時回傳 None 值。
    """
    empty = {"b": None, "r2": None, "n": 0}
    if asset_ret is None or market_ret is None:
        return empty
    # 先只取大盤最近 window 個交易日，個股在這段期間停牌的日子就自然缺席，不補值
    market_window = market_ret.tail(window)
    joined = pd.concat([asset_ret, market_window], axis=1, join="inner").dropna()
    n = len(joined)
    if n < window * MIN_COVERAGE:
        return {**empty, "n": n}
    a, m = joined.iloc[:, 0], joined.iloc[:, 1]
    var_m = m.var()
    if not var_m or math.isnan(var_m):
        return {**empty, "n": n}
    beta = a.cov(m) / var_m
    corr = a.corr(m)
    r2 = corr ** 2 if not math.isnan(corr) else None
    return {
        "b": round(float(beta), 2),
        "r2": round(float(r2), 2) if r2 is not None else None,
        "n": n,
    }


def compute_all_betas(asset_ret, bench_rets):
    """對每個大盤、每個窗口算一次 beta，回傳 {bench_key: {window: {...}}}"""
    return {
        key: {str(w): compute_beta(asset_ret, mret, w) for w in BETA_WINDOWS}
        for key, mret in bench_rets.items()
    }


def group_returns(member_rets):
    """族群等權日報酬：每天取有資料的成員報酬平均"""
    if not member_rets:
        return None
    return pd.concat(member_rets, axis=1).mean(axis=1, skipna=True).dropna()


def compute_bench_stats(key, ret, bench_rets):
    """大盤卡片用：年化波動，以及對另一個大盤指數的 beta 與相關係數"""
    stats = {}
    for w in BETA_WINDOWS:
        tail = ret.tail(w)
        vol = tail.std() * math.sqrt(TRADING_DAYS_PER_YEAR) * 100 if len(tail) > 1 else None
        cross = {}
        for other_key, other_ret in bench_rets.items():
            if other_key == key:
                continue
            res = compute_beta(ret, other_ret, w)
            cross = {
                "key": other_key,
                "b": res["b"],
                "corr": round(math.sqrt(res["r2"]), 2) if res["r2"] is not None else None,
            }
            break
        stats[str(w)] = {"vol": round(float(vol), 1) if vol is not None else None, "cross": cross}
    return stats


# ---------- 組資料 ----------
def build_benchmarks():
    """抓大盤指數，回傳 (給網頁用的資料, {key: 日報酬 Series}, 資料日期)"""
    closes, used_ids = {}, {}
    for b in BENCHMARKS:
        candidates = b["finmind_candidates"]
        print(f"抓取大盤 {b['name']} (FinMind 候選: {candidates}) ...", end=" ")
        data_id, close = try_fetch_benchmark(candidates)
        if close is None:
            print("失敗 — 候選代碼都抓不到，可能是免費額度用完或代碼需要更新")
            continue
        print(f"OK (用 data_id={data_id}, 共 {len(close)} 筆)")
        closes[b["key"]] = close
        used_ids[b["key"]] = data_id

    bench_rets = {k: daily_returns(c) for k, c in closes.items()}

    out = []
    data_date = None
    for b in BENCHMARKS:
        close = closes.get(b["key"])
        if close is None:
            out.append({"key": b["key"], "name": b["name"], "date": "", "stats": None})
            continue
        last = close.index[-1]
        if data_date is None:
            data_date = last
        out.append({
            "key": b["key"],
            "name": b["name"],
            "date": last.strftime("%m/%d") + " 收盤",
            "close": round(float(close.iloc[-1]), 2),
            "stats": compute_bench_stats(b["key"], bench_rets[b["key"]], bench_rets),
        })
    return out, bench_rets, data_date


def build_groups(bench_rets):
    """跑過每個族群，算個股與族群組合的 beta"""
    result = []
    total = sum(len(g["stocks"]) for g in GROUPS)
    done = 0
    for g in GROUPS:
        rows, member_rets = [], []
        for s in g["stocks"]:
            done += 1
            code, name = s["code"], s["name"]
            print(f"[{done}/{total}] 抓取 {code} {name} ...", end=" ")
            close = fetch_close_series(code)
            time.sleep(REQUEST_DELAY_SEC)
            if close is None:
                print("失敗")
                rows.append({"code": code, "name": name, "date": "",
                             "betas": compute_all_betas(None, bench_rets)})
                continue
            ret = daily_returns(close)
            member_rets.append(ret)
            betas = compute_all_betas(ret, bench_rets)
            first = next(iter(betas.values()), {}).get(str(BETA_WINDOWS[0]), {})
            print(f"OK (β{BETA_WINDOWS[0]}={first.get('b')})")
            rows.append({"code": code, "name": name,
                         "date": close.index[-1].strftime("%m/%d"), "betas": betas})
        result.append({
            "name": g["name"],
            "stocks": rows,
            "total": compute_all_betas(group_returns(member_rets), bench_rets),
        })
    return result


def render_html(benchmarks, groups, data_date):
    html = TEMPLATE_FILE.read_text(encoding="utf-8")
    payload = {
        "windows": BETA_WINDOWS,
        "benchmarks": benchmarks,
        "groups": groups,
        "dataDate": data_date.strftime("%m/%d") if data_date is not None else "",
    }
    html = html.replace("__BETA_JSON__", json.dumps(payload, ensure_ascii=False))
    tw_time = datetime.now(ZoneInfo("Asia/Taipei"))
    html = html.replace("__GENERATED_AT__", tw_time.strftime("%Y-%m-%d %H:%M"))
    return html


def main():
    print("=== 族群 x 大盤 Beta 分析 - 資料更新開始 ===\n")
    if FINMIND_TOKEN:
        print("（已設定 FINMIND_TOKEN）\n")

    benchmarks, bench_rets, data_date = build_benchmarks()
    if not bench_rets:
        print("\n大盤指數全部抓取失敗，無法計算 beta，結束。")
        return 1
    print()
    groups = build_groups(bench_rets)

    html = render_html(benchmarks, groups, data_date)
    OUTPUT_DIR.mkdir(exist_ok=True)
    today = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d")
    out_path = OUTPUT_DIR / f"族群Beta分析_{today}.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"\n完成！已輸出: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
