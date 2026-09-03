# -*- coding: utf-8 -*-
"""
族群 x 大盤 均線強弱分析 — 自動產生腳本

用法:
    python generate_report.py

會做的事:
    1. 用 FinMind 抓取 stock_list.py 裡所有個股與大盤指數的歷史股價
    2. 計算 MA5 / MA10 / MA20 / MA60 / MA120 / MA240 與 52 週高點
    3. 把資料套進 template.html，輸出一份帶有今天日期的 HTML 檔案
    4. 把每個族群的強弱比例記錄進 history.json，供網頁畫趨勢線

需求套件:
    pip install requests pandas --break-system-packages   # 或在 venv 裡不用加這個參數
"""

import json
import time
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import requests
import pandas as pd

from stock_list import BENCHMARKS, GROUPS

# ---------- 設定 ----------
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "output"
HISTORY_PERIOD = "2y"   # 抓 2 年資料，確保 MA240 與 52 週高點算得出來
REQUEST_DELAY_SEC = 0.4  # 每檔股票查詢間隔，避免被 Yahoo 限流
MA_PERIODS = [5, 10, 20, 60, 120, 240]

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_START_DATE = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
HISTORY_FILE = SCRIPT_DIR / "history.json"
HISTORY_DAYS_TO_KEEP = 60   # 每個族群最多保留多少天的歷史紀錄
SPARKLINE_DAYS = 7          # 網頁上顯示最近幾天的趨勢線


def fetch_finmind_history(data_id: str):
    """向 FinMind 要指定 data_id 的歷史日資料，回傳整理好的 DataFrame，抓不到回傳 None"""
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": data_id,
        "start_date": FINMIND_START_DATE,
    }
    try:
        resp = requests.get(FINMIND_URL, params=params, timeout=15)
        if resp.status_code != 200:
            return None
        payload = resp.json()
        rows = payload.get("data", [])
        if not rows:
            return None

        df = pd.DataFrame(rows)
        if "close" not in df.columns or "max" not in df.columns:
            return None

        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        df["Close"] = pd.to_numeric(df["close"], errors="coerce")
        df["High"] = pd.to_numeric(df["max"], errors="coerce")
        df = df.dropna(subset=["Close"])
        df = df[df["Close"] > 0]

        if len(df) < 5:
            return None
        return df
    except Exception as e:
        print(f"  [警告] FinMind data_id={data_id} 抓取失敗: {e}")
        return None


def try_fetch_finmind_benchmark(candidates):
    """依序嘗試候選 data_id，回傳 (使用的 data_id, DataFrame) 或 (None, None)"""
    for data_id in candidates:
        df = fetch_finmind_history(data_id)
        time.sleep(REQUEST_DELAY_SEC)
        if df is not None:
            return data_id, df
    return None, None


def compute_stock_stats(df):
    """依 DataFrame 算出收盤、當日最高、各均線、52週高點"""
    closes = df["Close"]
    last_row = df.iloc[-1]
    last_date = df.index[-1]

    ma = {}
    for p in MA_PERIODS:
        if len(closes) >= p:
            ma[p] = round(float(closes.tail(p).mean()), 2)
        else:
            ma[p] = None

    # 52 週高點（用近 252 個交易日的最高價）
    high_window = df["High"].tail(252)
    high52w = round(float(high_window.max()), 2) if not high_window.empty else None

    return {
        "date": last_date.strftime("%m/%d"),
        "date_iso": last_date.strftime("%Y-%m-%d"),
        "close": round(float(last_row["Close"]), 2),
        "high": round(float(last_row["High"]), 2),
        "ma5": ma[5], "ma10": ma[10], "ma20": ma[20],
        "ma60": ma[60], "ma120": ma[120], "ma240": ma[240],
        "high52w": high52w,
    }


def build_groups_data():
    """跑過 stock_list.py 裡的每個族群，抓資料並算均線"""
    result_groups = []
    total = sum(len(g["stocks"]) for g in GROUPS)
    done = 0

    for g in GROUPS:
        stocks_out = []
        for s in g["stocks"]:
            done += 1
            code, name = s["code"], s["name"]
            print(f"[{done}/{total}] 抓取 {code} {name} ...", end=" ")
            df = fetch_finmind_history(code)
            time.sleep(REQUEST_DELAY_SEC)
            if df is None:
                print("失敗")
                stocks_out.append({
                    "code": code, "name": name, "date": "",
                    "close": None, "high": None,
                    "ma5": None, "ma10": None, "ma20": None,
                    "ma60": None, "ma120": None, "ma240": None,
                    "high52w": None,
                })
                continue
            stats = compute_stock_stats(df)
            print(f"OK (收盤 {stats['close']})")
            stocks_out.append({"code": code, "name": name, **stats})
        result_groups.append({"name": g["name"], "stocks": stocks_out})

    return result_groups


def build_benchmarks_data():
    result = []
    data_date = None   # 用大盤指數實際收盤日期，作為 history.json 記錄用的日期
    for b in BENCHMARKS:
        candidates = b["finmind_candidates"]
        print(f"抓取大盤 {b['name']} (FinMind 候選: {candidates}) ...", end=" ")
        data_id, df = try_fetch_finmind_benchmark(candidates)
        if df is None:
            print("失敗 — FinMind 候選代碼都抓不到，可能是免費額度用完或代碼需要更新")
            result.append({
                "key": b["key"], "name": b["name"], "date": "",
                "close": None, "ma": {str(p): None for p in MA_PERIODS},
                "chartUrl": b["chart_url"],
            })
            continue
        stats = compute_stock_stats(df)
        print(f"OK (用 data_id={data_id}, 收盤 {stats['close']})")
        if data_date is None:
            data_date = stats["date_iso"]   # 以第一個成功抓到的大盤指數為準（優先加權指數）
        result.append({
            "key": b["key"],
            "name": b["name"],
            "date": stats["date"] + " 收盤",
            "close": stats["close"],
            "ma": {str(p): stats[f"ma{p}"] for p in MA_PERIODS},
            "chartUrl": b["chart_url"],
        })
    return result, data_date


def compute_group_avg_pct(stocks):
    """算出一個族群在 6 條均線上，平均有多少比例的股票站上均線（0~100）"""
    sum_pct = 0.0
    periods_counted = 0
    for p in MA_PERIODS:
        above, total = 0, 0
        for s in stocks:
            close = s.get("close")
            ma = s.get(f"ma{p}")
            if close is None or ma is None:
                continue
            total += 1
            if close > ma:
                above += 1
        if total:
            sum_pct += above / total * 100
            periods_counted += 1
    if periods_counted:
        return round(sum_pct / periods_counted, 1)
    return None


def load_history():
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def update_history(groups_data, today_str):
    """把今天每個族群的強弱比例存進 history.json，並修剪掉太舊的資料"""
    history = load_history()
    for g in groups_data:
        name = g["name"]
        avg_pct = compute_group_avg_pct(g["stocks"])
        if avg_pct is None:
            continue
        history.setdefault(name, {})
        history[name][today_str] = avg_pct
        dates_sorted = sorted(history[name].keys())
        if len(dates_sorted) > HISTORY_DAYS_TO_KEEP:
            for old_date in dates_sorted[:-HISTORY_DAYS_TO_KEEP]:
                del history[name][old_date]

    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return history


def attach_sparkline_data(groups_data, history):
    """把每個族群最近幾天的比例數字（含日期），附加到 groups_data 裡供網頁畫趨勢圖"""
    for g in groups_data:
        hist = history.get(g["name"], {})
        recent_dates = sorted(hist.keys())[-SPARKLINE_DAYS:]
        g["history"] = [{"date": d[5:], "pct": hist[d]} for d in recent_dates]
    return groups_data


def render_html(benchmarks_data, groups_data):
    template_path = SCRIPT_DIR / "template.html"
    html = template_path.read_text(encoding="utf-8")

    html = html.replace("__BENCHMARKS_JSON__", json.dumps(benchmarks_data, ensure_ascii=False))
    html = html.replace("__GROUPS_JSON__", json.dumps(groups_data, ensure_ascii=False))
    tw_time = datetime.now(ZoneInfo("Asia/Taipei"))
    html = html.replace("__GENERATED_AT__", tw_time.strftime("%Y-%m-%d %H:%M"))
    return html


def main():
    print("=== 族群 x 大盤 均線強弱分析 - 資料更新開始 ===\n")

    benchmarks_data, data_date = build_benchmarks_data()
    print()
    groups_data = build_groups_data()

    # 優先用資料本身實際的收盤日期記錄歷史，抓不到大盤時才退回用執行當下的系統日期
    record_date = data_date or datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
    print(f"\n本次記錄的資料日期: {record_date}"
          + ("" if data_date else "（大盤抓取失敗，改用系統當下日期）"))

    history = update_history(groups_data, record_date)
    groups_data = attach_sparkline_data(groups_data, history)

    html = render_html(benchmarks_data, groups_data)

    OUTPUT_DIR.mkdir(exist_ok=True)
    today = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d")
    out_path = OUTPUT_DIR / f"族群大盤均線分析_{today}.html"
    out_path.write_text(html, encoding="utf-8")

    print(f"\n完成！已輸出: {out_path}")


if __name__ == "__main__":
    sys.exit(main())
