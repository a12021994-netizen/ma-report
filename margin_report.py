# -*- coding: utf-8 -*-
"""
台股上市櫃 個股融資維持率排行 — 自動產生腳本

用法:
    python margin_report.py

會做的事:
    1. 從證交所 (TWSE) 與櫃買中心 (TPEx) 官方網站，逐日抓取近 LOOKBACK_CALENDAR_DAYS 天
       「全部個股」的融資買進 / 賣出 / 現償 / 餘額，以及當天的收盤價、成交均價
    2. 用每天的融資進出推估每檔股票的「融資平均成本」(加權移動平均)
    3. 融資維持率 = 收盤價 ÷ (融資平均成本 × 融資成數) × 100%
    4. 把結果套進 margin_template.html，輸出 output/融資維持率_YYYYMMDD.html

估算方法 (官方沒有公布個股融資維持率，這裡是推估值):
    * 第一天的融資餘額，成本假設為當天的成交均價
    * 之後每天：賣出 + 現償 的部分依平均成本移出 (不影響平均成本)；
      新增的融資部位以當天成交均價 (成交金額 ÷ 成交股數，沒有就用收盤價) 計入成本
    * 「成本換手率」= 目前融資餘額中，有多少比例是在觀察期間內買進的。
      數值越高代表初始假設的影響越小、推估越可信

需求套件:
    pip install requests pandas
"""

import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ---------- 設定 ----------
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "output"
CACHE_DIR = SCRIPT_DIR / "margin_cache"   # 過去日期的原始資料快取 (GitHub Actions 用 actions/cache 保存)
TEMPLATE_FILE = SCRIPT_DIR / "margin_template.html"

LOOKBACK_CALENDAR_DAYS = 150   # 往回抓幾個日曆天 (約 100 個交易日)，越長成本推估越準
REQUEST_DELAY_SEC = 1.6        # 每次對外請求間隔；TWSE / TPEx 交錯請求，同一主機約 3 秒一次
FETCH_RETRIES = 3
FETCH_RETRY_WAIT_SEC = 5

# 融資成數 (融資金額 / 股票市值)。證金公司規定：上市 6 成、上櫃 5 成
MARGIN_RATIO = {"上市": 0.6, "上櫃": 0.5}

TW_TZ = ZoneInfo("Asia/Taipei")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

session = requests.Session()
session.headers.update(HEADERS)


# ---------- 共用工具 ----------
def to_num(v):
    """把 '1,234.50'、'--'、'' 之類的字串轉成 float，轉不了回傳 None"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("+", "").strip()
    # 有些欄位會夾 HTML，例如 <p style=...>-</p>
    if "<" in s:
        s = re.sub(r"<[^>]+>", "", s).strip()
    if s in ("", "-", "--", "---", "X", "除權息", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def norm(s):
    return str(s).replace(" ", "").replace("　", "").strip()


def find_col(fields, *keywords, default=None, start=0):
    """在欄位名稱裡找第一個同時包含所有 keywords 的欄位索引 (從 start 開始找)"""
    for i in range(start, len(fields)):
        name = norm(fields[i])
        if all(k in name for k in keywords):
            return i
    return default


def http_get_json(url, params):
    last_err = None
    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            resp = session.get(url, params=params, timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            last_err = e
            if attempt < FETCH_RETRIES:
                print(f"    [重試 {attempt}/{FETCH_RETRIES - 1}] {url} {params}: {e}")
                time.sleep(FETCH_RETRY_WAIT_SEC * attempt)
    print(f"    [失敗] {url} {params}: {last_err}")
    return None


def roc_date(d):
    return f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"


def cached(kind, d, fetcher):
    """過去日期的資料不會再變，有抓到就存成快取檔；今天的資料不快取 (可能還沒公布完整)"""
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{kind}_{d:%Y%m%d}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8")), True
        except ValueError:
            pass
    data = fetcher(d)
    today = datetime.now(TW_TZ).date()
    if data and d < today:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data, False


def tables_of(payload):
    """TWSE / TPEx 新版 rwd API 都是 {"tables":[{"fields":[...],"data":[...]}]}"""
    if not payload:
        return []
    tables = payload.get("tables")
    if isinstance(tables, list):
        return [t for t in tables if isinstance(t, dict) and t.get("fields") and t.get("data")]
    # 舊版格式：fields9/data9、creditFields/creditList、aaData 等
    out = []
    for k, v in payload.items():
        if isinstance(v, list) and v and isinstance(v[0], list):
            fk = k.replace("data", "fields").replace("List", "Fields")
            fields = payload.get(fk) or []
            out.append({"fields": fields, "data": v})
    return out


def payload_date_ok(payload, d):
    """有些 API 查假日會回傳最近一個交易日的資料，用回傳的日期欄位確認"""
    if not payload:
        return False
    for key in ("date", "reportDate"):
        v = payload.get(key)
        if v:
            digits = "".join(ch for ch in str(v) if ch.isdigit())
            if len(digits) == 8:
                return digits == f"{d:%Y%m%d}"
            if len(digits) == 7:  # 民國年 1151030
                return digits == f"{d.year - 1911:03d}{d:%m%d}"
    return True


# ---------- 上市 (TWSE) ----------
def fetch_twse_margin(d):
    payload = http_get_json(
        "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN",
        {"date": f"{d:%Y%m%d}", "selectType": "ALL", "response": "json"},
    )
    if not payload or str(payload.get("stat", "")).upper() != "OK" or not payload_date_ok(payload, d):
        return None
    for t in tables_of(payload):
        f = t["fields"]
        if len(f) >= 8 and "代號" in norm(f[0]):
            # 欄位：代號 名稱 | 融資：買進 賣出 現金償還 前日餘額 今日餘額 次一營業日限額 | 融券 ...
            out = {}
            for r in t["data"]:
                code = norm(r[0])
                out[code] = {
                    "name": norm(r[1]),
                    "buy": to_num(r[2]), "sell": to_num(r[3]), "repay": to_num(r[4]),
                    "prev": to_num(r[5]), "bal": to_num(r[6]), "limit": to_num(r[7]),
                }
            return out or None
    return None


def fetch_twse_price(d):
    payload = http_get_json(
        "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
        {"date": f"{d:%Y%m%d}", "type": "ALLBUT0999", "response": "json"},
    )
    if not payload or str(payload.get("stat", "")).upper() != "OK" or not payload_date_ok(payload, d):
        return None
    for t in tables_of(payload):
        f = t["fields"]
        i_code = find_col(f, "證券代號")
        i_close = find_col(f, "收盤價")
        if i_code is None or i_close is None:
            continue
        i_vol = find_col(f, "成交股數")
        i_amt = find_col(f, "成交金額")
        return parse_prices(t["data"], i_code, i_close, i_vol, i_amt)
    return None


# ---------- 上櫃 (TPEx) ----------
def fetch_tpex_margin(d):
    payload = http_get_json(
        "https://www.tpex.org.tw/www/zh-tw/margin/balance",
        {"date": f"{d:%Y/%m/%d}", "id": "", "response": "json"},
    )
    tables = tables_of(payload) if payload_date_ok(payload, d) else []
    if not tables:
        # 舊版 API 備援
        payload = http_get_json(
            "https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php",
            {"l": "zh-tw", "o": "json", "d": roc_date(d)},
        )
        tables = tables_of(payload) if payload_date_ok(payload, d) else []
    for t in tables:
        f = [norm(x) for x in t["fields"]]
        rows = t["data"]
        if not rows or len(rows[0]) < 7:
            continue
        # 新版欄位：代號 名稱 前資餘額(張) 資買 資賣 現償 資餘額 資屬證金 資使用率 資限額 前券餘額 ...
        i_prev = find_col(f, "前資餘額", default=2)
        i_buy = find_col(f, "資買", default=3)
        i_sell = find_col(f, "資賣", default=4)
        i_repay = find_col(f, "現償", default=5)
        i_bal = find_col(f, "資餘額", default=6, start=i_prev + 1)
        i_limit = find_col(f, "資限額", default=None)
        out = {}
        for r in rows:
            code = norm(r[0])
            if not code or not code[0].isalnum():
                continue
            out[code] = {
                "name": norm(r[1]),
                "buy": to_num(r[i_buy]), "sell": to_num(r[i_sell]), "repay": to_num(r[i_repay]),
                "prev": to_num(r[i_prev]), "bal": to_num(r[i_bal]),
                "limit": to_num(r[i_limit]) if i_limit is not None else None,
            }
        if out:
            return out
    return None


def fetch_tpex_price(d):
    payload = http_get_json(
        "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes",
        {"date": f"{d:%Y/%m/%d}", "id": "", "response": "json"},
    )
    tables = tables_of(payload) if payload_date_ok(payload, d) else []
    if not tables:
        payload = http_get_json(
            "https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php",
            {"l": "zh-tw", "o": "json", "d": roc_date(d)},
        )
        tables = tables_of(payload) if payload_date_ok(payload, d) else []
    for t in tables:
        f = t["fields"]
        i_code = find_col(f, "代號", default=0)
        i_close = find_col(f, "收盤", default=2)
        i_vol = find_col(f, "成交股數")
        i_amt = find_col(f, "成交金額")
        if i_amt is None:
            i_amt = find_col(f, "成交金額(元)")
        result = parse_prices(t["data"], i_code, i_close, i_vol, i_amt)
        if result:
            return result
    return None


def parse_prices(rows, i_code, i_close, i_vol, i_amt):
    out = {}
    for r in rows:
        if len(r) <= i_close:
            continue
        code = norm(r[i_code])
        close = to_num(r[i_close])
        if not code or close is None or close <= 0:
            continue
        vwap = None
        if i_vol is not None and i_amt is not None:
            vol, amt = to_num(r[i_vol]), to_num(r[i_amt])
            if vol and amt and vol > 0:
                vwap = amt / vol
                # 成交均價應該落在合理範圍，否則 (單位不一致等) 改用收盤價
                if not (close * 0.85 <= vwap <= close * 1.15):
                    vwap = None
        out[code] = {"close": close, "vwap": vwap}
    return out or None


# ---------- 抓資料 ----------
MARKETS = {
    "上市": {"margin": ("twse_margin", fetch_twse_margin), "price": ("twse_price", fetch_twse_price)},
    "上櫃": {"margin": ("tpex_margin", fetch_tpex_margin), "price": ("tpex_price", fetch_tpex_price)},
}


def collect_days():
    """回傳 {market: [(date, margin_dict, price_dict), ...]} (日期由舊到新)"""
    today = datetime.now(TW_TZ).date()
    days = [today - timedelta(days=i) for i in range(LOOKBACK_CALENDAR_DAYS, -1, -1)]
    days = [d for d in days if d.weekday() < 5]
    result = {m: [] for m in MARKETS}
    for idx, d in enumerate(days, 1):
        print(f"[{idx}/{len(days)}] {d}")
        for market, cfg in MARKETS.items():
            mkey, mfetch = cfg["margin"]
            margin, hit = cached(mkey, d, mfetch)
            if not hit:
                time.sleep(REQUEST_DELAY_SEC)
            if not margin:
                print(f"    {market} 融資：無資料 (休市或尚未公布)")
                continue
            pkey, pfetch = cfg["price"]
            price, hit = cached(pkey, d, pfetch)
            if not hit:
                time.sleep(REQUEST_DELAY_SEC)
            if not price:
                print(f"    {market} 股價：抓取失敗，略過這天")
                continue
            result[market].append((d, margin, price))
            print(f"    {market} 融資 {len(margin)} 檔、股價 {len(price)} 檔")
    return result


# ---------- 推估融資成本 ----------
def estimate(market, days):
    """依日期順序滾動計算每檔股票的融資平均成本"""
    ratio = MARGIN_RATIO[market]
    state = {}   # code -> dict(cost, bal, init, start, last_close, ...)
    for d, margin, price in days:
        for code, m in margin.items():
            bal = m["bal"]
            if bal is None:
                continue
            p = price.get(code)
            st = state.get(code)
            px = None
            if p:
                px = p["vwap"] or p["close"]
            elif st:
                px = st["last_close"]   # 當天沒成交，用前一個收盤
            if px is None:
                continue

            if st is None or st["cost"] is None or st["bal"] <= 0:
                # 第一次出現 (或融資歸零後重新出現)：整筆餘額的成本假設為當天均價
                hist = st["hist"] if st else []
                hist.append(bal)
                if bal > 0:
                    state[code] = {"cost": px, "bal": bal, "init": bal, "start": d, "name": m["name"],
                                   "last_close": p["close"] if p else px, "hist": hist}
                elif st is not None:
                    st["bal"] = 0
                continue

            prev = m["prev"] if m["prev"] is not None else st["bal"]
            outflow = (m["sell"] or 0) + (m["repay"] or 0)
            remaining = max(min(prev, st["bal"]) - outflow, 0) if prev > 0 else 0
            if bal <= 0:
                st.update(cost=None, bal=0, init=0)
            else:
                remaining = min(remaining, bal)
                inflow = bal - remaining
                st["init"] = st["init"] * (remaining / st["bal"]) if st["bal"] > 0 else 0
                st["cost"] = (st["cost"] * remaining + px * inflow) / bal
                st["bal"] = bal
            if p:
                st["last_close"] = p["close"]
            st["name"] = m["name"]
            st["hist"].append(bal)

    if not days:
        return [], None
    last_date, last_margin, last_price = days[-1]
    rows = []
    for code, st in state.items():
        m = last_margin.get(code)
        p = last_price.get(code)
        if not m or not p or not st.get("cost") or st["bal"] <= 0:
            continue
        close = p["close"]
        loan_per_share = st["cost"] * ratio
        hist = st["hist"]
        bal = st["bal"]
        chg1 = bal - hist[-2] if len(hist) >= 2 else None
        chg5 = bal - hist[-6] if len(hist) >= 6 else None
        rows.append({
            "code": code,
            "name": st.get("name") or m["name"],
            "market": market,
            "close": round(close, 2),
            "cost": round(st["cost"], 2),
            "ratio": round(close / loan_per_share * 100, 1),
            "bal": int(bal),
            "chg1": int(chg1) if chg1 is not None else None,
            "chg5": int(chg5) if chg5 is not None else None,
            "loan": round(bal * 1000 * loan_per_share / 1e8, 2),   # 估計融資金額 (億元)
            "usage": round(bal / m["limit"] * 100, 1) if m.get("limit") else None,
            "turnover": round((1 - st["init"] / bal) * 100) if bal > 0 else None,
            "days": sum(1 for dd, _, _ in days if dd >= st["start"]),
        })
    return rows, last_date


# ---------- 輸出 ----------
def render_html(payload):
    html = TEMPLATE_FILE.read_text(encoding="utf-8")
    html = html.replace("__MARGIN_JSON__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("__GENERATED_AT__", datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M"))
    return html


def main():
    days_by_market = collect_days()
    all_rows = []
    data_dates = {}
    for market, days in days_by_market.items():
        rows, last_date = estimate(market, days)
        print(f"{market}：{len(days)} 個交易日，產出 {len(rows)} 檔，最新資料日 {last_date}")
        all_rows.extend(rows)
        if last_date:
            data_dates[market] = last_date.isoformat()

    if not all_rows:
        print("沒有任何資料，停止輸出")
        sys.exit(1)

    all_rows.sort(key=lambda r: r["ratio"])
    payload = {
        "dataDates": data_dates,
        "marginRatio": MARGIN_RATIO,
        "lookbackDays": {m: len(d) for m, d in days_by_market.items()},
        "rows": all_rows,
    }
    OUTPUT_DIR.mkdir(exist_ok=True)
    latest = max(data_dates.values()).replace("-", "")
    out_path = OUTPUT_DIR / f"融資維持率_{latest}.html"
    out_path.write_text(render_html(payload), encoding="utf-8")
    print(f"已輸出 {out_path}")


if __name__ == "__main__":
    main()
