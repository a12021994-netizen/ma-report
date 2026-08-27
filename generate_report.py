# -*- coding: utf-8 -*-
"""
族群 x 大盤 均線強弱分析 — 自動產生腳本

用法:
    python generate_report.py

會做的事:
    1. 用 yfinance 抓取 stock_list.py 裡所有個股與大盤指數的歷史股價
    2. 自動判斷個股是上市(.TW)還是上櫃(.TWO)
    3. 計算 MA5 / MA10 / MA20 / MA60 / MA120 / MA240 與 52 週高點
    4. 把資料套進 template.html，輸出一份帶有今天日期的 HTML 檔案

需求套件:
    pip install yfinance --break-system-packages   # 或在 venv 裡不用加這個參數
"""

import json
import time
import sys
from datetime import datetime
from pathlib import Path

import yfinance as yf

from stock_list import BENCHMARKS, GROUPS

# ---------- 設定 ----------
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "output"
HISTORY_PERIOD = "2y"   # 抓 2 年資料，確保 MA240 與 52 週高點算得出來
REQUEST_DELAY_SEC = 0.4  # 每檔股票查詢間隔，避免被 Yahoo 限流
MA_PERIODS = [5, 10, 20, 60, 120, 240]


def fetch_history(ticker: str):
    """抓取單一 ticker 的歷史資料，失敗回傳 None"""
    try:
        df = yf.Ticker(ticker).history(period=HISTORY_PERIOD, auto_adjust=False)
        if df is None or df.empty:
            return None
        return df
    except Exception as e:
        print(f"  [警告] {ticker} 抓取失敗: {e}")
        return None


def try_fetch_stock(code: str):
    """依序嘗試 .TW（上市）與 .TWO（上櫃），回傳 (使用的 ticker, DataFrame) 或 (None, None)"""
    for suffix in (".TW", ".TWO"):
        ticker = f"{code}{suffix}"
        df = fetch_history(ticker)
        time.sleep(REQUEST_DELAY_SEC)
        if df is not None and len(df) > 5:
            return ticker, df
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
            ticker, df = try_fetch_stock(code)
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
            print(f"OK ({ticker}, 收盤 {stats['close']})")
            stocks_out.append({"code": code, "name": name, **stats})
        result_groups.append({"name": g["name"], "stocks": stocks_out})

    return result_groups


def build_benchmarks_data():
    result = []
    for b in BENCHMARKS:
        print(f"抓取大盤 {b['name']} ({b['yahoo_ticker']}) ...", end=" ")
        df = fetch_history(b["yahoo_ticker"])
        time.sleep(REQUEST_DELAY_SEC)
        if df is None:
            print("失敗 — 請確認 yahoo_ticker 設定是否正確")
            result.append({
                "key": b["key"], "name": b["name"], "date": "",
                "close": None, "ma": {str(p): None for p in MA_PERIODS},
                "chartUrl": b["chart_url"],
            })
            continue
        stats = compute_stock_stats(df)
        print(f"OK (收盤 {stats['close']})")
        result.append({
            "key": b["key"],
            "name": b["name"],
            "date": stats["date"] + " 收盤",
            "close": stats["close"],
            "ma": {str(p): stats[f"ma{p}"] for p in MA_PERIODS},
            "chartUrl": b["chart_url"],
        })
    return result


def render_html(benchmarks_data, groups_data):
    template_path = SCRIPT_DIR / "template.html"
    html = template_path.read_text(encoding="utf-8")

    html = html.replace("__BENCHMARKS_JSON__", json.dumps(benchmarks_data, ensure_ascii=False))
    html = html.replace("__GROUPS_JSON__", json.dumps(groups_data, ensure_ascii=False))
    html = html.replace("__GENERATED_AT__", datetime.now().strftime("%Y-%m-%d %H:%M"))
    return html


def main():
    print("=== 族群 x 大盤 均線強弱分析 - 資料更新開始 ===\n")

    benchmarks_data = build_benchmarks_data()
    print()
    groups_data = build_groups_data()

    html = render_html(benchmarks_data, groups_data)

    OUTPUT_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    out_path = OUTPUT_DIR / f"族群大盤均線分析_{today}.html"
    out_path.write_text(html, encoding="utf-8")

    print(f"\n完成！已輸出: {out_path}")


if __name__ == "__main__":
    sys.exit(main())
