# -*- coding: utf-8 -*-
"""
族群 x 大盤 均線強弱分析 — 股票清單設定
要新增/刪除族群或個股,直接編輯這個檔案即可，不用動 generate_report.py。
"""

# 大盤基準：key 用於程式內部識別
# 改用 FinMind 抓取（Yahoo Finance 沒有穩定收錄櫃買指數，加權指數也一併
# 改走 FinMind 以維持程式邏輯一致）。finmind_candidates 是依序嘗試的候選
# data_id，程式會自動挑第一個真的抓得到資料的使用，不用手動確認。
BENCHMARKS = [
    {
        "key": "twii",
        "name": "加權指數 (TAIEX)",
        "finmind_candidates": ["TAIEX", "TAIEX_TR"],
        "chart_url": "https://www.wantgoo.com/index/0000",
    },
    {
        "key": "otc",
        "name": "櫃買指數 (TPEx)",
        "finmind_candidates": ["TPEX", "OTC", "TPEx"],
        "chart_url": "https://www.wantgoo.com/index/two",
    },
]

# 族群與成分股。code 不含 .TW / .TWO 後綴，程式會自動偵測上市或上櫃。
GROUPS = [
    {"name": "記憶體", "stocks": [
        {"code": "2408", "name": "南亞科"},
        {"code": "2344", "name": "華邦電"},
        {"code": "2337", "name": "旺宏"},
        {"code": "3260", "name": "威剛"},
        {"code": "8299", "name": "群聯"},
        {"code": "3006", "name": "晶豪科"},
    ]},
    {"name": "CCL銅箔基板", "stocks": [
        {"code": "2383", "name": "台光電"},
        {"code": "6274", "name": "台燿"},
        {"code": "6213", "name": "聯茂"},
        {"code": "1303", "name": "南亞"},
    ]},
    {"name": "被動元件", "stocks": [
        {"code": "2327", "name": "國巨"},
        {"code": "2492", "name": "華新科"},
        {"code": "3026", "name": "禾伸堂"},
        {"code": "6173", "name": "信昌電"},
        {"code": "2472", "name": "立隆電"},
        {"code": "3624", "name": "光頡"},
    ]},
    {"name": "ABF載板", "stocks": [
        {"code": "3037", "name": "欣興"},
        {"code": "8046", "name": "南電"},
        {"code": "3189", "name": "景碩"},
        {"code": "4958", "name": "臻鼎-KY"},
    ]},
    {"name": "電源供應", "stocks": [
        {"code": "2308", "name": "台達電"},
        {"code": "2301", "name": "光寶科"},
    ]},
    {"name": "BBU", "stocks": [
        {"code": "3211", "name": "順達"},
        {"code": "4931", "name": "新盛力"},
        {"code": "6781", "name": "AES-KY"},
    ]},
    {"name": "ODM", "stocks": [
        {"code": "2382", "name": "廣達"},
        {"code": "3231", "name": "緯創"},
        {"code": "6669", "name": "緯穎"},
        {"code": "2317", "name": "鴻海"},
        {"code": "2324", "name": "仁寶"},
        {"code": "2356", "name": "英業達"},
    ]},
    {"name": "光通訊", "stocks": [
        {"code": "3081", "name": "聯亞"},
        {"code": "2455", "name": "全新"},
        {"code": "4991", "name": "環宇-KY"},
        {"code": "4979", "name": "華星光"},
        {"code": "3234", "name": "光環"},
        {"code": "3363", "name": "上詮"},
        {"code": "3450", "name": "聯鈞"},
        {"code": "6451", "name": "訊芯-KY"},
    ]},
    {"name": "矽晶圓", "stocks": [
        {"code": "6488", "name": "環球晶"},
        {"code": "3532", "name": "台勝科"},
        {"code": "6182", "name": "合晶"},
    ]},
    {"name": "功率元件", "stocks": [
        {"code": "8261", "name": "富鼎"},
        {"code": "2481", "name": "強茂"},
        {"code": "5425", "name": "台半"},
        {"code": "3675", "name": "德微"},
        {"code": "6435", "name": "大中"},
    ]},
    {"name": "航運", "stocks": [
        {"code": "2603", "name": "長榮"},
        {"code": "2609", "name": "陽明"},
        {"code": "2615", "name": "萬海"},
    ]},
    {"name": "晶圓代工(成熟製程)", "stocks": [
        {"code": "2303", "name": "聯電"},
        {"code": "5347", "name": "世界"},
    ]},
    {"name": "石英元件", "stocks": [
        {"code": "3042", "name": "晶技"},
        {"code": "2484", "name": "希華"},
        {"code": "8182", "name": "加高"},
    ]},
    {"name": "TGV設備", "stocks": [
        {"code": "6207", "name": "雷科"},
        {"code": "8064", "name": "東捷"},
        {"code": "8027", "name": "鈦昇"},
        {"code": "3055", "name": "蔚華科"},
    ]},
    {"name": "散熱", "stocks": [
        {"code": "3017", "name": "奇鋐"},
        {"code": "3324", "name": "雙鴻"},
        {"code": "3653", "name": "健策"},
    ]},
  {"name": "半導體設備", "stocks": [
        {"code": "6187", "name": "萬潤"},
        {"code": "2360", "name": "致茂"},
        {"code": "2467", "name": "志聖"},
        {"code": "3131", "name": "弘塑"},
        {"code": "6640", "name": "均華"},
        {"code": "2404", "name": "漢唐"},
        {"code": "5536", "name": "聖暉*"},
        {"code": "6139", "name": "亞翔"},
    ]},
    
]
