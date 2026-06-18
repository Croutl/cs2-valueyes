from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import requests
import re
import json
import sqlite3
import uvicorn
import math
import os
import urllib3
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
urllib3.disable_warnings()

app = FastAPI(title="CS2 ValuEyes", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ⚠️ 请替换为你自己的 CSQAQ API Token
# 建议通过环境变量设置（更安全）：set CSQAQ_API_TOKEN=你的token
CSQAQ_API_TOKEN = os.environ.get("CSQAQ_API_TOKEN", "")
if not CSQAQ_API_TOKEN:
    print("   ⚠️ 环境变量 CSQAQ_API_TOKEN 未设置，使用内置 Token（可能有调用限制）")
    print("     建议免费注册 https://csqaq.com/ 获取自己的 Token")
    print("     设置方式：set CSQAQ_API_TOKEN=你的token")
    CSQAQ_API_TOKEN = 'DYLV71Z737M9M9M4A6M3I484'

API_TOKEN = CSQAQ_API_TOKEN
DB_PATH = 'cs_skins.db'

def init_db():
    json_path = '饰品id_20260423.json'
    need_rebuild = False
    if not os.path.exists(DB_PATH):
        need_rebuild = True
    else:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.execute('SELECT COUNT(*) FROM skins')
            count = cursor.fetchone()[0]
            conn.close()
            if count == 0:
                need_rebuild = True
        except:
            need_rebuild = True
    if need_rebuild:
        if not os.path.exists(json_path):
            return False
        print("   📦 正在初始化饰品数据库...")
        with open(json_path, 'r', encoding='utf-8') as f:
            all_skins = json.load(f)
        conn = sqlite3.connect(DB_PATH)
        conn.execute('''CREATE TABLE IF NOT EXISTS skins (
            id INTEGER PRIMARY KEY, name TEXT, market_hash_name TEXT)''')
        conn.executemany('INSERT OR REPLACE INTO skins VALUES (?, ?, ?)',
            [(item['id'], item['name'], item.get('market_hash_name', '')) for item in all_skins])
        conn.commit()
        conn.close()
        print(f"   ✅ 饰品数据库已初始化（{len(all_skins)} 条）")
    # 始终创建价格历史表（无论数据库是否新建）
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS price_history (
        skin_id INTEGER NOT NULL,
        price REAL NOT NULL,
        source TEXT NOT NULL,
        collected_at TEXT NOT NULL,
        PRIMARY KEY (skin_id, source, collected_at))''')
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_price_history_skin
        ON price_history(skin_id, collected_at)''')
    conn.commit()
    conn.close()
    return True

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

init_db()

container_cache = {}
skin_cache = {}
market_cache = {"data": None, "time": 0}
CACHE_TTL = 300  # 5分钟

def safe_json(resp):
    try:
        return resp.json()
    except Exception as e:
        return {"_parse_error": str(e)}

def get_container_detail(container_id: int):
    if container_id in container_cache:
        return container_cache[container_id]
    url = f"https://api.csqaq.com/api/v1/info/good/container_detail?id={container_id}"
    resp = requests.get(url, headers={'ApiToken': API_TOKEN}, verify=False, timeout=10)
    data = safe_json(resp)
    if data and data.get('code') == 200:
        container_cache[container_id] = data['data']
        return data['data']
    return []

def get_skin_detail(skin_id: int):
    if skin_id in skin_cache:
        return skin_cache[skin_id]
    url = f"https://api.csqaq.com/api/v1/info/good?id={skin_id}"
    resp = requests.get(url, headers={'ApiToken': API_TOKEN}, verify=False, timeout=10)
    data = safe_json(resp)
    if data and data.get('code') == 200:
        skin_cache[skin_id] = data['data']['goods_info']
        return data['data']['goods_info']
    return {}

def get_skin_statistic(skin_id: int):
    url = f"https://api.csqaq.com/api/v1/info/good/statistic?id={skin_id}"
    resp = requests.get(url, headers={'ApiToken': API_TOKEN}, verify=False, timeout=10)
    data = safe_json(resp)
    if data and data.get('code') == 200:
        return data['data']
    return []

def get_chart_data(skin_id: int, key: str = 'sell_price', platform: int = 1, period: int = 90):
    url = "https://api.csqaq.com/api/v1/info/chart"
    payload = {"good_id": str(skin_id), "key": key, "platform": platform, "period": str(period), "style": "all_style"}
    timeout_val = 15 if period >= 90 else 8
    resp = requests.post(url, headers={'ApiToken': API_TOKEN, 'Content-Type': 'application/json'},
                         json=payload, verify=False, timeout=timeout_val)
    data = safe_json(resp)
    if data and data.get('code') == 200:
        return data['data']
    return None

def get_arbitrage_list(page: int = 1, res: int = 0, platforms: str = 'BUFF-YYYP',
                       sort_by: int = 1, min_price: float = 1, max_price: float = 5000,
                       turnover: int = 0, text: str = ''):
    url = "https://api.csqaq.com/api/v1/info/exchange_detail"
    payload = {"page_index": page, "res": res, "platforms": platforms, "sort_by": sort_by,
               "min_price": min_price, "max_price": max_price, "turnover": turnover}
    if text:
        payload['text'] = text
    resp = requests.post(url, headers={'ApiToken': API_TOKEN, 'Content-Type': 'application/json'},
                         json=payload, verify=False, timeout=10)
    data = safe_json(resp)
    if data and data.get('code') == 200:
        return data['data']
    return []

# ===== csqaq 官方 API 封装 =====

def get_roi_list():
    url = "https://api.csqaq.com/api/v1/info/roi"
    resp = requests.post(url, headers={'ApiToken': API_TOKEN, 'Content-Type': 'application/json'},
                         json={}, verify=False, timeout=10)
    data = safe_json(resp)
    if data and data.get('code') == 200:
        return data['data']
    return []

def get_case_stats():
    url = "https://api.csqaq.com/api/v1/stat/case"
    resp = requests.get(url, headers={'ApiToken': API_TOKEN}, verify=False, timeout=10)
    data = safe_json(resp)
    if data and data.get('code') == 200:
        return data['data']
    return []

def get_container_roi_detail(container_id: int):
    url = f"https://api.csqaq.com/api/v1/info/roi_detail?id={container_id}"
    resp = requests.get(url, headers={'ApiToken': API_TOKEN}, verify=False, timeout=10)
    data = safe_json(resp)
    if data and data.get('code') == 200 and data.get('data'):
        roi_data = data['data']
        return roi_data
    return []

# ==================== 估值模型 ====================
QUALITY_ORDER = ['军规级', '受限级', '保密级', '隐秘级', '稀有']
WEAR_RANGES = {
    '崭新出厂': {'min': 0.00, 'max': 0.07, 'mid': 0.035},
    '略有磨损': {'min': 0.07, 'max': 0.15, 'mid': 0.11},
    '久经沙场': {'min': 0.15, 'max': 0.38, 'mid': 0.265},
    '破损不堪': {'min': 0.38, 'max': 0.45, 'mid': 0.415},
    '战痕累累': {'min': 0.45, 'max': 1.00, 'mid': 0.725},
}

def get_wear_type(name: str):
    for w in WEAR_RANGES:
        if f'({w})' in name:
            return w
    return None

def calculate_valuation(skin_data: dict, skin_id: int = None) -> dict:
    """
    估值模型 v2.0 — 四个维度：
    - 炼金基价：基于品质的基础价值（含星标溢价）
    - 存世稀缺：实际存世量越少越稀缺
    - 市场深度：Steam 求购/在售比 + Buff 求购/在售比
    - 趋势健康：涨跌幅过大→折价/溢价
    """
    rarity = skin_data.get('rarity_localized_name', '')
    price = skin_data.get('buff_sell_price', 0) or 0
    rate_30 = skin_data.get('sell_price_rate_30', 0) or 0
    rate_90 = skin_data.get('sell_price_rate_90', 0) or 0
    name = skin_data.get('name', '')
    sell_num = skin_data.get('buff_sell_num', 0) or 0
    buy_num = skin_data.get('buff_buy_num', 0) or 0
    steam_sell = skin_data.get('steam_sell_price', 0) or 0
    steam_buy = skin_data.get('steam_buy_price', 0) or 0
    steam_sell_num = skin_data.get('steam_sell_num', 0) or 0
    steam_buy_num = skin_data.get('steam_buy_num', 0) or 0

    is_stattrak = 'StatTrak' in name
    is_souvenir = '纪念品' in name
    is_main_weapon = any(name.startswith(g) for g in ['AK-47', 'AWP', 'USP'])
    is_main_weapon_m4 = any(name.startswith(g) for g in ['M4A1', 'M4A4'])

    # === 1. 炼金基价（以崭新出厂为基准） ===
    # API 返回的品质可能有"保密"或"保密级"两种格式，都兼容
    fn_base = {'军规级': 5, '军规': 5, '受限级': 25, '受限': 25,
               '保密级': 125, '保密': 125, '隐秘级': 625, '隐秘': 625}
    base_fn_price = fn_base.get(rarity, 1)

    # 磨损折算系数（以崭新出厂为 1.0）
    wear = skin_data.get('exterior_localized_name', '')
    wear_multiplier = {
        '崭新出厂': 1.0,
        '略有磨损': 0.65,
        '久经沙场': 0.45,
        '破损不堪': 0.32,
        '战痕累累': 0.18,
    }
    wear_mult = wear_multiplier.get(wear, 0.5)
    alchemy_base = round(base_fn_price * wear_mult, 2)

    # StatTrak 溢价 +40%
    if is_stattrak:
        alchemy_base = round(alchemy_base * 1.4, 2)
    # 纪念品溢价 +20%
    if is_souvenir:
        alchemy_base = round(alchemy_base * 1.2, 2)
    # 主战武器溢价（AK-47 / AWP / USP 为 CS 经典主战武器，需求旺盛）
    if is_main_weapon:
        alchemy_base = round(alchemy_base * 1.5, 2)
    # M4 主战溢价 +30%
    if is_main_weapon_m4:
        alchemy_base = round(alchemy_base * 1.3, 2)

    # === 2. 存世稀缺系数（基于真实的 statistic 数据） ===
    scarcity = 1.0
    if skin_id:
        stat_data = get_skin_statistic(skin_id)
        if stat_data and len(stat_data) > 1:
            total_count = stat_data[-1]['statistic']
            if total_count > 0:
                if total_count < 100:      scarcity = 5   # 极度稀缺
                elif total_count < 500:    scarcity = 3  # 很稀缺
                elif total_count < 2000:   scarcity = 1.8   # 较稀缺
                elif total_count < 5000:  scarcity = 1.5   # 正常
                else:                       scarcity = 1.0   # 烂大街
        else:
            # 无数据时回退到品质估算
            rarity_scarcity = {'消费级': 0.8, '工业级': 0.9, '军规级': 1.0, '受限级': 1.2, '保密级': 1.5, '隐秘级': 2.0, '隐秘': 2.0}
            scarcity = rarity_scarcity.get(rarity, 1.0)

    # === 3. 市场深度系数 ===
    depth = 1.0
    # Buff 求购/在售比 — 越高说明需求越强
    if sell_num > 0 and buy_num > 0:
        buff_ratio = buy_num / sell_num
        if buff_ratio > 0.5:       depth += 0.3   # 需求旺盛
        elif buff_ratio > 0.2:     depth += 0.1   # 需求正常
        elif buff_ratio < 0.05:    depth -= 0.2   # 无人问津
    # Steam 市场深度
    if steam_sell_num > 0 and steam_buy_num > 0:
        steam_ratio = steam_buy_num / steam_sell_num
        if steam_ratio > 5:        depth += 0.2   # Steam 求购远多于在售
        elif steam_ratio < 1:      depth -= 0.1   # 卖家多买家少
    # 价差（Steam求购/Buff售价）：价差越小流动性越好
    if steam_buy > 0 and price > 0:
        spread = steam_buy / price
        if spread > 0.95:          depth += 0.2   # 几乎平价，流动性极好
        elif spread < 0.7:         depth -= 0.1   # 价差大，变现成本高

    # === 4. 趋势健康系数 ===
    if rate_30 < -30 or rate_90 < -40:
        health = 1.2   # 超跌→低估机会
    elif rate_30 > 50 or rate_90 > 100:
        health = 0.8   # 暴涨→泡沫风险
    elif rate_30 < -15:
        health = 1.1   # 中度下跌→轻度低估
    elif rate_30 > 25:
        health = 0.9   # 中度上涨→轻度高估
    else:
        health = 1.0

    # === 5. 综合估值 ===
    raw_v = round(alchemy_base * scarcity * depth * health, 2)
    ratio = (raw_v / price * 100) if price > 0 else 100

    # 安全限幅：估值与市价偏差不超过 ±30%
    if ratio > 130:
        my_v = round(price * 1.3, 2)
        limiter_note = "上限保护"
    elif ratio < 70:
        my_v = round(price * 0.7, 2)
        limiter_note = "下限保护"
    else:
        my_v = raw_v
        limiter_note = None

    final_ratio = (my_v / price * 100) if price > 0 else 100

    if final_ratio > 120: signal = "🟢 被低估"
    elif final_ratio > 108: signal = "🟡 轻微低估"
    elif final_ratio < 80: signal = "🔴 被高估"
    elif final_ratio < 92: signal = "🟠 轻微高估"
    else: signal = "⚪ 合理区间"

    return {
        "我的估值": f"¥{my_v}", "市场价(Buff)": f"¥{price}",
        "估值/市价比": f"{final_ratio:.0f}%", "信号": signal,
        "分解": {
            "炼金基价": f"¥{alchemy_base:.2f}",
            "品质": f"{rarity}",
            "磨损": f"{wear}",
            f"{'StatTrak' if is_stattrak else ''}{'纪念品' if is_souvenir else ''}{'主战' if is_main_weapon else ''}{'M4主战' if is_main_weapon_m4 else ''}溢价": f"{'✓' if is_stattrak or is_souvenir or is_main_weapon or is_main_weapon_m4 else '—'}",
        },
        "说明": "估值基于品质、存世量、市场流动性和趋势综合计算" + (f"（已触发{limiter_note}）" if limiter_note else ""),
    }

# ==================== 1️⃣ 武器箱开箱回报率列表（官方API） ====================
def get_roi_list_merged():
    roi_list = get_roi_list()
    stats_list = get_case_stats()
    stats_map = {s['case_id']: s for s in stats_list} if stats_list else {}

    with open('饰品id_20260423.json', 'r', encoding='utf-8') as f:
        id_map = json.load(f)
    id_to_name = {item['id']: item['name'] for item in id_map}

    result = []
    for item in roi_list:
        cid = item['id']
        stat = stats_map.get(cid, {})
        key_price = item.get('price', 0) or 0
        income = item.get('income', 0) or 0
        roi = item.get('roi', 0) or 0
        profit = round(income - key_price, 2) if key_price > 0 else 0
        container_name = id_to_name.get(item.get('good_id'), item.get('name', ''))
        result.append({
            'id': cid,
            '名称': item.get('name', ''),
            '图片': item.get('img', ''),
            '获取方式': item.get('comment', ''),
            '上线时间': item.get('created_at', '')[:10] if item.get('created_at') else '',
            '在售价': key_price,
            '在售量': item.get('num', 0) or 0,
            '预期收益': income,
            '回报率': f"{roi:.2f}%",
            '纯利润': profit,
            'good_id': item.get('good_id'),
            '今日开箱': stat.get('daily', 0),
            '本周开箱': stat.get('weekly', 0),
            '总开箱': stat.get('total', 0),
        })
    return sorted(result, key=lambda x: float(x['回报率'].rstrip('%')), reverse=True)

# ==================== 2️⃣ 单个武器箱详情 ====================
def get_case_detail(case_id: int):
    roi_list = get_roi_list()
    target = None
    for item in roi_list:
        if item['id'] == case_id:
            target = item
            break
    if not target:
        return {"错误": "武器箱不存在"}

    good_id = target.get('good_id')
    items = get_container_detail(good_id) if good_id else []

    with open('饰品id_20260423.json', 'r', encoding='utf-8') as f:
        id_map = json.load(f)
    id_to_name = {item['id']: item['name'] for item in id_map}

    container_name = id_to_name.get(good_id, target.get('name', ''))

    # 品质归类
    cn_map = {'军规': '军规级', '受限': '受限级', '保密': '保密级', '隐秘': '隐秘级', '非凡': '隐秘级'}
    quality_data = {q: {'items': [], 'total': 0} for q in QUALITY_ORDER}

    for item in items:
        sid = item['id']
        full_name = id_to_name.get(sid, item.get('short_name', ''))
        price = item.get('price', 0)
        rln = item.get('rln', '')
        qln = item.get('qln', '')
        quality = cn_map.get(rln, rln)
        if qln == '★' or quality not in quality_data:
            quality = '稀有'
        if quality in quality_data:
            quality_data[quality]['items'].append({'id': sid, '名称': full_name, '价格': price})
            quality_data[quality]['total'] += price

    quality_summary = []
    for q in QUALITY_ORDER:
        qd = quality_data[q]
        if qd['items']:
            count = len(qd['items'])
            quality_summary.append({
                '品质': q, '数量': count, '平均价格': round(qd['total'] / count, 2),
                '饰品列表': qd['items'],
            })

    roi_detail_data = get_container_roi_detail(case_id)
    trend = []
    if roi_detail_data:
        for p in roi_detail_data[::max(1, len(roi_detail_data)//20)]:
            trend.append({'日期': p['date'][:10], '预期收益': p['income'], '回报率': p['roi']})

    key_price = target.get('price', 0) or 0
    income = target.get('income', 0) or 0
    roi = target.get('roi', 0) or 0

    return {
        '武器箱': container_name,
        '武器箱ID': case_id,
        'good_id': good_id,
        '钥匙价格': key_price,
        '预期收益': income,
        '回报率': f"{roi:.2f}%",
        '纯利润': round(income - key_price, 2),
        '获取方式': target.get('comment', ''),
        '上线时间': target.get('created_at', '')[:10] if target.get('created_at') else '',
        '品质分布': quality_summary,
        '总饰品数': len(items),
        '回报率走势': trend,
    }

# ==================== 3️⃣ 价格走势（精简版） ====================
def get_price_trend(skin_id: int, period: int = 90):
    info = get_skin_detail(skin_id)
    if not info:
        return {"错误": "饰品不存在"}

    current_price = info.get('buff_sell_price', 0)
    chart_data = get_chart_data(skin_id, 'sell_price', 1, period)

    price_chart = []
    if chart_data and chart_data.get('timestamp'):
        timestamps = chart_data['timestamp']
        prices = chart_data['main_data']
        num_data = chart_data.get('num_data', [])
        daily = {}
        for i in range(len(timestamps)):
            dt = datetime.fromtimestamp(timestamps[i] / 1000)
            dk = dt.strftime('%Y-%m-%d')
            if dk not in daily:
                daily[dk] = []
            daily[dk].append({
                'price': prices[i] if prices[i] is not None else 0,
                'num': num_data[i] if i < len(num_data) and num_data[i] is not None else 0,
            })
        for dk in sorted(daily.keys()):
            dd = daily[dk]
            price_chart.append({
                '时间': dk, '价格': dd[-1]['price'],
                '最高价': max(d['price'] for d in dd),
                '最低价': min(d['price'] for d in dd),
                '在售量': dd[-1]['num'],
            })

    inv = []
    stat = get_skin_statistic(skin_id)
    if stat and len(stat) > 1:
        for p in stat[-14:]:
            inv.append({'日期': p['created_at'][:10], '存世量': p['statistic']})

    return {
        '名称': info.get('name', ''), '当前价格': current_price,
        '价格走势': price_chart, '存世量趋势': inv,
        '各平台价格': {
            'Buff': info.get('buff_sell_price', 0), 'Steam': info.get('steam_sell_price', 0),
            '悠悠有品': info.get('yyyp_sell_price', 0),
        },
    }

# ==================== 4️⃣ AI 分析引擎（大盘 + 个品） ====================

# 自动从 .env 文件加载环境变量（如果有的话）
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_env_path):
    try:
        with open(_env_path, 'r', encoding='utf-8') as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith('#') and '=' in _line:
                    _k, _v = _line.split('=', 1)
                    _k, _v = _k.strip(), _v.strip().strip('"\'').strip()
                    if _k and _v:
                        os.environ[_k] = _v
                        print(f"   📄 .env 加载: {_k}={_v[:6]}...{_v[-4:]}")
    except Exception as _e:
        print(f"   ⚪ .env 文件读取跳过: {_e}")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

def get_market_index():
    """大盘指数：K线日线，缓存5分钟，失败时自动重试"""
    now = datetime.now().timestamp()
    if market_cache.get("data") and now - market_cache.get("time", 0) < CACHE_TTL:
        return market_cache["data"]

    url = "https://api.csqaq.com/api/v1/sub/kline?id=1&type=1day"
    try:
        resp = requests.get(url, headers={'ApiToken': API_TOKEN}, verify=False, timeout=10)
        data = safe_json(resp)
        if data and data.get('code') == 200:
            kline = data.get('data', [])
            if not kline or len(kline) < 8:
                print("   ⚠️ 大盘数据不足（少于8个K线点）")
                return None
            today = kline[-1]
            today_c = today.get('c', 0)
            today_o = today.get('o', 1)
            if len(kline) >= 8:
                seven = kline[-8]
                change_7d = round((today_c - seven['c']) / seven['c'] * 100, 2) if seven['c'] > 0 else 0
            else:
                change_7d = 0
            if len(kline) >= 31:
                thirty = kline[-31]
                change_30d = round((today_c - thirty['c']) / thirty['c'] * 100, 2) if thirty['c'] > 0 else 0
            else:
                change_30d = 0
            result = {
                'name': '饰品指数',
                'now': today_c,
                'rate_today': round((today_c - today_o) / today_o * 100, 2) if today_o > 0 else 0,
                'change_7d': change_7d,
                'change_30d': change_30d,
            }
            market_cache["data"] = result
            market_cache["time"] = now
            print(f"   📊 大盘数据已更新：近7天 {change_7d:+.2f}% | 近30天 {change_30d:+.2f}%")
            return result
        msg = data.get('msg', '') if data else '无响应'
        # 有缓存就返回旧数据（不阻塞前端）
        if market_cache.get("data"):
            print(f"   ⚪ 大盘数据更新失败({msg})，使用缓存")
            market_cache["time"] = now  # 延长缓存使用时间
            return market_cache["data"]
        print(f"   ⚠️ 大盘数据获取失败：{msg}")
        market_cache["time"] = 0
    except requests.Timeout:
        if market_cache.get("data"):
            print("   ⚪ 大盘数据超时，使用缓存")
            market_cache["time"] = now
            return market_cache["data"]
        print("   ⚠️ 大盘数据请求超时")
        market_cache["time"] = 0
    except requests.ConnectionError:
        if market_cache.get("data"):
            print("   ⚪ 大盘连接失败，使用缓存")
            market_cache["time"] = now
            return market_cache["data"]
        print("   ⚠️ 大盘数据连接失败，请检查网络")
        market_cache["time"] = 0
    except Exception as e:
        if market_cache.get("data"):
            market_cache["time"] = now
            return market_cache["data"]
        print(f"   ⚠️ 大盘数据异常：{e}")
        market_cache["time"] = 0
    return None

def get_sub_index(sub_id: int = 1, period: str = "daily"):
    """获取子指数数据（如步枪指数、手套指数等）"""
    url = f"https://api.csqaq.com/api/v1/sub_data?id={sub_id}&type={period}"
    resp = requests.get(url, headers={'ApiToken': API_TOKEN}, verify=False, timeout=10)
    data = safe_json(resp)
    return data.get('data') if data and data.get('code') == 200 else None

def bind_current_ip():
    """绑定当前 IP 到 API Token 白名单"""
    try:
        resp = requests.post("https://api.csqaq.com/api/v1/sys/bind_local_ip",
                             headers={'ApiToken': API_TOKEN}, verify=False, timeout=10)
        data = safe_json(resp)
        if data and data.get('code') == 200:
            print("   ✅ IP 已绑定到 API 白名单")
            return True
        if data and data.get('code') == 429:
            print("   ⚠️ IP 绑定频率受限（30秒内仅可绑定一次），若后续查询失败请稍后重启")
            return False
        print(f"   ⚠️ IP 绑定失败：{data}")
    except Exception as e:
        print(f"   ⚠️ IP 绑定请求异常：{e}")
    return False

def ai_analyze_skin(skin_id: int) -> dict:
    """AI 分析饰品投资价值"""
    info = get_skin_detail(skin_id)
    if not info:
        return {"错误": "饰品不存在"}

    name = info.get('name', '')
    price = info.get('buff_sell_price', 0)
    rate_1 = info.get('sell_price_rate_1', 0) or 0
    rate_7 = info.get('sell_price_rate_7', 0) or 0
    rate_30 = info.get('sell_price_rate_30', 0) or 0
    rate_90 = info.get('sell_price_rate_90', 0) or 0
    rarity = info.get('rarity_localized_name', '')
    wear = info.get('exterior_localized_name', '')
    sell_num = info.get('buff_sell_num', 0)
    steam_price = info.get('steam_sell_price', 0)
    buff_buy = info.get('buff_buy_price', 0)

    # 获取大盘指数
    market = get_market_index()
    market_rate_7 = 0
    market_rate_30 = 0
    if market:
        market_rate_7 = float(market.get('change_7d', 0) or 0)
        market_rate_30 = float(market.get('change_30d', 0) or 0)

    # === 核心判断逻辑（你定的规则） ===
    verdict = ""
    signals = []
    score = 50  # 基准50分

    # 规则1：7天涨太多 → 要跌
    if rate_7 > 30:
        signals.append(f"🔴 近7天爆拉 {rate_7:+.1f}%，已经突破30%的警戒线了。这种涨法历史上很少能持续，短期获利盘随时会砸盘，追高大概率被套。")
        score -= 20
    elif rate_7 > 15:
        signals.append(f"🟡 近7天拉了 {rate_7:+.1f}%，涨幅不算小。虽然在主升浪里看着诱人，但这个位置追进去性价比不高，等回调再上车更稳。")
        score -= 10
    elif rate_7 < -20:
        signals.append(f"🟢 近7天跌了 {rate_7:+.1f}%，深度回调。恐慌盘该走的都走了，留下来的都是老手。这种超跌往往就是反弹的起点，可以关注起来。")
        score += 15
    else:
        signals.append(f"⚪ 近7天涨跌 {rate_7:+.1f}%，波动在正常范围内，没什么好慌的。")
        score += 5

    # 规则2：大盘 vs 个品
    if market_rate_7 > rate_7 + 10:
        signals.append(f"🟢 大盘这波涨了 {market_rate_7:+.1f}%，但这兄弟才动了 {rate_7:+.1f}%，明显跑输大盘。按经验这种滞后品种往往有补涨需求，可以埋伏一手。")
        score += 15
    elif market_rate_7 < rate_7 - 10:
        signals.append(f"🟡 大盘才涨 {market_rate_7:+.1f}%，这货已经拉了 {rate_7:+.1f}%，独立行情虽然看着猛，但脱离大盘太远容易回吐。别追了，等回调。")
        score -= 10
    else:
        signals.append(f"⚪ 大盘近7天 {market_rate_7:+.1f}%，这兄弟走势跟大盘基本同步，没什么异常。")

    # 规则3：30天趋势判断
    if rate_30 < -25:
        signals.append(f"🟢 近一个月跌了 {rate_30:+.1f}%，这是中期超跌的信号。这市场里的规律就是——跌多了自然会弹，现在反而可能是捡便宜的好时候。")
        score += 10
    elif rate_30 > 50:
        signals.append(f"🔴 近一个月暴涨 {rate_30:+.1f}%，一个月翻了一半还多，泡沫味已经出来了。现在接盘的风险远大于收益，让进去的人先跑一跑再说。")
        score -= 15
    else:
        signals.append(f"⚪ 近30天涨跌 {rate_30:+.1f}%，中规中矩，趋势上没有太大问题。")

    # 规则4：流动性判断
    if sell_num > 1000:
        signals.append(f"⚪ Buff挂单 {sell_num} 件，流动性很足，想买想卖都方便，不用担心砸手里。")
        score += 5
    elif sell_num > 100:
        signals.append(f"⚪ Buff挂单 {sell_num} 件，流动性一般，买卖可能需要多挂两天。")
    else:
        signals.append(f"🟡 Buff才 {sell_num} 件在卖，货太少了，真要出货可能得降点价才行。这种货不适合大资金玩。")
        score -= 5

    # 规则5：挂刀价值判断
    if steam_price > 0 and price > 0:
        arbitrage_ratio = round(price / steam_price, 3)
        if arbitrage_ratio < 0.8:
            signals.append(f"🟢 挂刀比例 {arbitrage_ratio}，低于0.8，懂的都懂——这就是白嫖余额的好机会。挂刀党的最爱。")
            score += 10
        elif arbitrage_ratio < 0.95:
            signals.append(f"⚪ 挂刀比例 {arbitrage_ratio}，正常范围，不算特别划算但也凑合。")
        else:
            signals.append(f"🔴 挂刀比例 {arbitrage_ratio}，高于0.95了，拿它挂刀等于送钱给G胖。")
            score -= 5

    # 规则6：30天大盘 vs 个品
    if market_rate_30 > rate_30 + 15:
        signals.append(f"🟢 大盘近一个月涨了 {market_rate_30:+.1f}%，这货才 {rate_30:+.1f}%，严重跑输大盘。这种滞涨品种补涨起来往往很猛，懂行的已经在悄悄吸筹了。")
        score += 15
    elif market_rate_30 < rate_30 - 15:
        signals.append(f"🟡 大盘近一个月跌了 {market_rate_30:+.1f}%，但这货居然逆势涨了 {rate_30:+.1f}%。独立行情看起来很硬，但大盘如果继续跌，它能扛多久？小心补跌。")
        score -= 10
    elif market_rate_30 < -10 and rate_30 > -5:
        signals.append(f"🟡 大盘近一个月跌了 {market_rate_30:+.1f}%，这兄弟还算抗跌。但要注意——强势股补跌起来才是最狠的。")
        score -= 5
    else:
        signals.append(f"⚪ 大盘近30天 {market_rate_30:+.1f}%，个品 {rate_30:+.1f}%，走势基本同步，没什么异常信号。")

    # 综合评分 → 结论
    if score >= 70:
        verdict = "🟢 推荐买入"
        conclusion = "各项数据都在告诉你一个信号：这玩意儿被低估了。无论是从趋势、流动性还是大盘对比来看，现在的价位都算是合理买点。老玩家都懂，这种机会不是天天有。"
    elif score >= 50:
        verdict = "🟡 观望"
        conclusion = "数据面上看中规中矩，没有明显的低估也没有泡沫。这种行情最忌讳手痒——等它回调到支撑位再出手，别急着追。稳住，机会是等出来的。"
    elif score >= 30:
        verdict = "🟠 谨慎"
        conclusion = "几个关键指标都亮黄灯了，短期有高估的嫌疑。这波追进去大概率要站岗，不如先放一放，等市场冷静了再说。钱在手里永远有机会。"
    else:
        verdict = "🔴 不推荐"
        conclusion = "这数据看着就不对劲——明显高估了，趋势也不好。聪明钱都在往外撤，你就别往里冲了。这市场里活得久的，都是懂得什么时候不该出手的人。"

    # === 如果配置了 DeepSeek API，用 LLM 润色输出 ===
    llm_analysis = None
    if DEEPSEEK_API_KEY:
        try:
            llm_analysis = _call_deepseek(name, price, rate_7, rate_30, market_rate_7, market_rate_30,
                                          rarity, verdict, signals, sell_num, steam_price, wear)
        except Exception as e:
            llm_analysis = f"LLM 分析暂不可用（{str(e)}），以上为规则引擎分析结果"

    return {
        "名称": name,
        "当前价格": f"¥{price}",
        "品质": rarity,
        "磨损": wear,
        "综合评分": f"{score}/100",
        "结论": verdict,
        "分析摘要": conclusion,
        "信号明细": signals,
        "关键指标": {
            "近7天涨跌": f"{rate_7:+.2f}%",
            "近30天涨跌": f"{rate_30:+.2f}%",
            "近90天涨跌": f"{rate_90:+.2f}%",
            "大盘近7天": f"{market_rate_7:+.2f}%",
            "大盘近30天": f"{market_rate_30:+.2f}%",
            "Buff在售量": f"{sell_num}个",
            "Buff售价": f"¥{price}",
            "Steam售价": f"¥{steam_price}",
        },
        "LLM分析": llm_analysis,
    }

def _call_deepseek(name: str, price: float, rate_7: float, rate_30: float,
                    market_rate: float, market_rate_30: float, rarity: str, verdict: str, signals: list,
                    sell_num: int = 0, steam_price: float = 0, wear: str = "") -> str:
    """调用 DeepSeek API 生成丰富投资分析报告"""
    url = "https://api.deepseek.com/chat/completions"
    signals_text = "\n".join(signals)
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "system",
                "content": """你是 CS2 饰品市场里摸爬滚打多年的老手，分析报告要像资深玩家在群里给兄弟们的建议一样——专业、直白、不废话。

输出要求：
1. 第一句话就给结论——看涨、看跌还是观望，别模棱两可
2. 从短期走势、中期趋势、大盘对比、流动性、性价比 5 个角度说清楚为什么
3. 最后甩 1-2 条实在的建议

说话风格：
- 就像跟哥们儿聊天，用词接地气（"这波""拉盘""砸盘""接飞刀""上车"）
- 数据要有，但别堆砌，点到为止
- 控制在 300 字以内，每段 1-3 句话
- 语气可以狠一点，但不说没根据的话

底线规则（必须遵守）：
- 近7天涨幅超过30% → 必须警告短期过热、回调风险
- 大盘涨幅大于个品涨幅 → 提示可能有补涨空间
- 近30天跌幅超过25% → 提示中期超跌反弹机会"""
            },
            {
                "role": "user",
                "content": f"""【饰品档案】
{name}
品质：{rarity} | 磨损：{wear}
当前Buff售价：¥{price} | Steam售价：¥{steam_price}
 {'【主战溢价】AK-47 / AWP / USP 经典主战武器，估值已 ×1.5 溢价' if any(name.startswith(g) for g in ['AK-47', 'AWP', 'USP']) else ''}
  {'【M4主战溢价】M4A1 / M4A4 主流步枪，估值已 ×1.3 溢价' if any(name.startswith(g) for g in ['M4A1', 'M4A4']) else ''}

【价格走势】
近7天：{rate_7:+.1f}%
近30天：{rate_30:+.1f}%

【大盘对比】
饰品指数近7天：{market_rate:+.1f}%
饰品指数近30天：{market_rate_30:+.1f}%

【规则引擎结论】
{verdict}

【规则信号】
{signals_text}

请以 CS 饰品老玩家的身份，给兄弟们写一份实在的投资分析。直说、别绕弯子。"""
            }
        ],
        "temperature": 0.7,
        "max_tokens": 500,
    }
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    data = resp.json()
    usage = data.get('usage', {})
    print(f"   📊 DeepSeek 分析报告完成 | 输入: {usage.get('prompt_tokens', '?')} tokens | 输出: {usage.get('completion_tokens', '?')} tokens")
    return data['choices'][0]['message']['content']

def _call_deepseek_valuation(name: str, rarity: str, wear: str, price: float, my_v: float,
                               alchemy_base: float, scarcity: float, depth: float, health: float,
                               rate_30: float, sell_num: int, is_stattrak: bool, is_main_weapon: bool, is_main_weapon_m4: bool) -> str:
    """调用 DeepSeek 解读估值模型"""
    url = "https://api.deepseek.com/chat/completions"
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "system",
                "content": """你是 CS2 饰品估值分析师，用大白话解释为什么这个饰品值这个价。说得要让普通玩家一听就懂。

估值模型四块：
1. 炼金基价——品质和磨损决定的基本盘
2. 存世稀缺——货越少越贵，物以稀为贵
3. 市场深度——买的人多、卖的人少，价格就硬
4. 趋势健康——超跌是机会，暴涨是泡沫

怎么说话：
- 先说结论：目前是高估还是低估了
- 简单说哪个因素影响最大
- 控制在 100 字以内
- 就像老玩家在跟你盘货，直接干脆"""
            },
            {
                "role": "user",
                "content": f"""饰品：{name}
品质：{rarity} | 磨损：{wear}
{'StatTrak版本' if is_stattrak else ''}
市场价：¥{price}
 我的估值：¥{my_v}
 估值/市价比：{round(my_v/price*100 if price>0 else 0)}%
 {'主战武器（AK-47/AWP/USP），已 ×1.5 溢价' if is_main_weapon else ''}
  {'M4主战（M4A1/M4A4），已 ×1.3 溢价' if is_main_weapon_m4 else ''}

四维分解：
- 炼金基价：¥{alchemy_base}（品质+磨损基础）
- 存世稀缺系数：×{scarcity}（越高越稀缺）
- 市场深度系数：×{depth}（越高需求越强）
- 趋势健康系数：×{health}（{rate_30:+.1f}%近30天）
- 在售量：{sell_num}件

请用大白话说说这个价到底值不值。"""
            }
        ],
        "temperature": 0.5,
        "max_tokens": 200,
    }
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    data = resp.json()
    usage = data.get('usage', {})
    print(f"   📊 DeepSeek 估值解读完成 | 输入: {usage.get('prompt_tokens', '?')} tokens | 输出: {usage.get('completion_tokens', '?')} tokens")
    return data['choices'][0]['message']['content']

# ==================== 5️⃣ 历史数据采集器（本地爬虫） ====================

COLLECTOR_INTERVAL = 1800  # 每30分钟采集一次
HOT_SKIN_IDS = [21632, 243, 19653, 19558, 21763, 19771, 21819, 21015, 21497]
BATCH_API_URL = "https://api.csqaq.com/api/v1/goods/getPriceByMarketHashName"
collector_running = False
collector_skin_queue = set()

def get_market_hash_name(skin_id: int) -> str:
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute('SELECT market_hash_name FROM skins WHERE id=?', (skin_id,)).fetchone()
        conn.close()
        return row[0] if row and row[0] else ''
    except:
        return ''

def get_market_hash_names(skin_ids: list) -> dict:
    """批量查询 market_hash_name，返回 {skin_id: market_hash_name}"""
    if not skin_ids:
        return {}
    try:
        conn = sqlite3.connect(DB_PATH)
        placeholders = ','.join('?' * len(skin_ids))
        rows = conn.execute(f'SELECT id, market_hash_name FROM skins WHERE id IN ({placeholders})', skin_ids).fetchall()
        conn.close()
        return {row['id']: row['market_hash_name'] or '' for row in rows}
    except Exception as e:
        print(f"   ⚠️ 批量查询 market_hash_name 失败: {e}")
        return {}

def batch_get_prices_by_market_hash(hash_names: list) -> list:
    """
    批量获取饰品价格和在售数据
    POST https://api.csqaq.com/api/v1/goods/getPriceByMarketHashName
    """
    if not hash_names:
        return []
    try:
        payload = json.dumps({"marketHashNameList": hash_names})
        headers = {'ApiToken': API_TOKEN, 'Content-Type': 'application/json'}
        resp = requests.post(BATCH_API_URL, headers=headers, data=payload, verify=False, timeout=15)
        data = safe_json(resp)
        if data and data.get('code') == 200:
            return data.get('data', [])
        print(f"   ⚠️ 批量API返回异常: {data.get('msg', '未知') if data else '无响应'}")
        return []
    except requests.Timeout:
        print("   ⚠️ 批量API请求超时")
    except requests.ConnectionError:
        print("   ⚠️ 批量API连接失败")
    except Exception as e:
        print(f"   ⚠️ 批量API异常: {e}")
    return []

def save_price_snapshot(skin_id: int, info: dict = None):
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        if info is None:
            info = get_skin_detail(skin_id)
        if not info:
            return False
        sources = {
            'buff_sell': info.get('buff_sell_price', 0) or 0,
            'steam_sell': info.get('steam_sell_price', 0) or 0,
            'buff_buy': info.get('buff_buy_price', 0) or 0,
        }
        conn = sqlite3.connect(DB_PATH)
        for source, price in sources.items():
            if price > 0:
                conn.execute(
                    'INSERT OR REPLACE INTO price_history (skin_id, price, source, collected_at) VALUES (?, ?, ?, ?)',
                    (skin_id, price, source, now_str)
                )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"   ⚠️ 保存价格快照失败 (skin_id={skin_id}): {e}")
        return False

def save_batch_snapshot(skin_id: int, batch_item: dict):
    """从批量API返回的单条数据中保存价格快照"""
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        sources = {
            'buff_sell': batch_item.get('buff_sell_price', 0) or 0,
            'steam_sell': batch_item.get('steam_sell_price', 0) or 0,
            'buff_buy': batch_item.get('buff_buy_price', 0) or 0,
        }
        conn = sqlite3.connect(DB_PATH)
        for source, price in sources.items():
            if price > 0:
                conn.execute(
                    'INSERT OR REPLACE INTO price_history (skin_id, price, source, collected_at) VALUES (?, ?, ?, ?)',
                    (skin_id, price, source, now_str)
                )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"   ⚠️ 保存批量快照失败 (skin_id={skin_id}): {e}")
        return False

def get_local_price_history(skin_id: int, days: int = 120, source: str = 'buff_sell'):
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            'SELECT price, collected_at FROM price_history WHERE skin_id=? AND source=? AND collected_at>=? ORDER BY collected_at',
            (skin_id, source, cutoff)
        ).fetchall()
        conn.close()
        if not rows:
            return []
        daily = {}
        for price, ts in rows:
            day = ts[:10]
            daily[day] = price
        result = []
        for day in sorted(daily.keys()):
            result.append({'date': day, 'price': daily[day]})
        return result
    except Exception as e:
        print(f"   ⚠️ 读取本地历史失败 (skin_id={skin_id}): {e}")
        return []

def get_local_price_count(skin_id: int, days: int = 120):
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            'SELECT COUNT(DISTINCT collected_at) FROM price_history WHERE skin_id=? AND source=? AND collected_at>=?',
            (skin_id, 'buff_sell', (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S'))
        ).fetchone()
        conn.close()
        return row[0] if row else 0
    except:
        return 0

def collect_skin_prices(skin_ids: list):
    """
    批量采集饰品价格（使用批量API，单次请求最多50个）
    如果批量API失败，回退到单条查询
    """
    if not skin_ids:
        return 0
    # 批量查询 market_hash_name
    hash_map = get_market_hash_names(skin_ids)
    valid_ids = [sid for sid, h in hash_map.items() if h]
    if not valid_ids:
        print("   ⚠️ 批量采集：所有饰品均无 market_hash_name，回退到单条采集")
        return _collect_fallback(skin_ids)

    hash_names = [hash_map[sid] for sid in valid_ids]
    count = 0

    # 按最大50个一组分批调用批量API
    batch_size = 50
    for i in range(0, len(hash_names), batch_size):
        batch_hash = hash_names[i:i + batch_size]
        batch_ids = valid_ids[i:i + batch_size]
        try:
            results = batch_get_prices_by_market_hash(batch_hash)
            if results:
                # 建立 market_hash_name -> data 的映射
                result_map = {}
                for item in results:
                    mhn = item.get('market_hash_name', '') or item.get('name', '')
                    if mhn:
                        result_map[mhn] = item
                # 保存匹配到的饰品
                for j, h in enumerate(batch_hash):
                    if h in result_map:
                        if save_batch_snapshot(batch_ids[j], result_map[h]):
                            count += 1
            else:
                # 批量API失败，对该批次回退到单条
                for sid in batch_ids:
                    info = get_skin_detail(sid)
                    if info and save_price_snapshot(sid, info):
                        count += 1
                    time.sleep(0.3)
        except Exception as e:
            print(f"   ⚠️ 批量采集异常，回退单条: {e}")
            for sid in batch_ids:
                info = get_skin_detail(sid)
                if info and save_price_snapshot(sid, info):
                    count += 1
                time.sleep(0.3)
    return count

def _collect_fallback(skin_ids: list):
    """回退模式：逐条调用 get_skin_detail 采集"""
    count = 0
    for sid in skin_ids:
        try:
            info = get_skin_detail(sid)
            if info and save_price_snapshot(sid, info):
                count += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"   ⚠️ 回退采集失败 skin_id={sid}: {e}")
    return count

def background_collector_loop():
    global collector_running
    collector_running = True
    print(f"   🔄 后台价格采集器已启动（每{COLLECTOR_INTERVAL//60}分钟采集一次，使用批量API）")
    while collector_running:
        try:
            queue = list(collector_skin_queue)
            if queue:
                batch = queue[:50]
                for sid in batch:
                    collector_skin_queue.discard(sid)
                n = collect_skin_prices(batch)
                if n > 0:
                    print(f"   📥 后台采集：成功保存 {n}/{len(batch)} 个饰品的价格快照")
            # 每轮也自动采集热门饰品（保证热门数据最新）
            n = collect_skin_prices(HOT_SKIN_IDS)
            print(f"   📥 热门饰品采集：{n}/{len(HOT_SKIN_IDS)} 个已更新")
        except Exception as e:
            print(f"   ⚠️ 后台采集循环异常: {e}")
        for _ in range(COLLECTOR_INTERVAL):
            if not collector_running:
                break
            time.sleep(1)

def start_background_collector():
    t = threading.Thread(target=background_collector_loop, daemon=True)
    t.start()
    return t

# ==================== 6️⃣ 价格预测模型（技术分析） ====================

def _calc_sma(prices: list, period: int) -> list:
    """计算简单移动平均线"""
    result = []
    for i in range(len(prices)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(prices[i-period+1:i+1]) / period)
    return result

def _calc_rsi(prices: list, period: int = 14) -> list:
    """计算 RSI 相对强弱指数"""
    if len(prices) < period + 1:
        return [None] * len(prices)
    result = [None] * period
    gains, losses = 0, 0
    for i in range(1, period + 1):
        diff = prices[i] - prices[i-1]
        if diff >= 0:
            gains += diff
        else:
            losses += abs(diff)
    avg_gain = gains / period
    avg_loss = losses / period
    rs = avg_gain / avg_loss if avg_loss > 0 else 100
    result.append(100 - (100 / (1 + rs)))
    for i in range(period + 1, len(prices)):
        diff = prices[i] - prices[i-1]
        gain = diff if diff > 0 else 0
        loss = abs(diff) if diff < 0 else 0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        result.append(100 - (100 / (1 + rs)))
    return result

def _calc_bollinger(prices: list, period: int = 20, std_mult: float = 2.0) -> dict:
    """计算布林带"""
    sma = _calc_sma(prices, period)
    upper, lower = [], []
    for i in range(len(prices)):
        if sma[i] is None:
            upper.append(None)
            lower.append(None)
        else:
            sq_diffs = sum((prices[j] - sma[i]) ** 2 for j in range(i-period+1, i+1))
            std = (sq_diffs / period) ** 0.5
            upper.append(sma[i] + std_mult * std)
            lower.append(sma[i] - std_mult * std)
    return {"sma": sma, "upper": upper, "lower": lower}

def _calc_macd(prices: list, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """计算 MACD"""
    ema_fast = _calc_ema(prices, fast)
    ema_slow = _calc_ema(prices, slow)
    macd_line = []
    for i in range(len(prices)):
        if ema_fast[i] is None or ema_slow[i] is None:
            macd_line.append(None)
        else:
            macd_line.append(ema_fast[i] - ema_slow[i])
    signal_line = _calc_ema(macd_line, signal) if macd_line else []
    histogram = []
    for i in range(len(macd_line)):
        if macd_line[i] is None or signal_line[i] is None:
            histogram.append(None)
        else:
            histogram.append(macd_line[i] - signal_line[i])
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}

def _calc_ema(data: list, period: int) -> list:
    """计算指数移动平均线"""
    result = []
    multiplier = 2 / (period + 1)
    ema = None
    for i, val in enumerate(data):
        if val is None:
            result.append(None)
            continue
        if ema is None:
            # 第一个有效值用 SMA 初始化
            count = 0
            total = 0
            for j in range(max(0, i-period+1), i+1):
                if data[j] is not None:
                    total += data[j]
                    count += 1
            if count >= period:
                ema = total / count
            elif count > 0:
                ema = total / count
            else:
                result.append(None)
                continue
        else:
            ema = (val - ema) * multiplier + ema
        result.append(ema)
    return result

def _find_support_resistance(prices: list, lookback: int = 30) -> dict:
    """寻找支撑位和阻力位（基于局部极值）"""
    if len(prices) < 10:
        return {"support": min(prices) if prices else 0, "resistance": max(prices) if prices else 0}

    recent = prices[-min(lookback, len(prices)):]
    peaks, troughs = [], []
    for i in range(2, len(recent) - 2):
        if recent[i] > recent[i-1] and recent[i] > recent[i-2] and recent[i] > recent[i+1] and recent[i] > recent[i+2]:
            peaks.append(recent[i])
        if recent[i] < recent[i-1] and recent[i] < recent[i-2] and recent[i] < recent[i+1] and recent[i] < recent[i+2]:
            troughs.append(recent[i])

    resistance = max(peaks) if peaks else max(recent)
    support = min(troughs) if troughs else min(recent)

    # 多重极值聚类：取最常见的价格区间作为更强力的支撑/阻力
    def cluster(values: list, threshold: float = 0.02) -> list:
        if not values:
            return []
        sorted_vals = sorted(values)
        clusters = [[sorted_vals[0]]]
        for v in sorted_vals[1:]:
            if abs(v - sum(clusters[-1]) / len(clusters[-1])) / max(sum(clusters[-1]) / len(clusters[-1]), 0.01) < threshold:
                clusters[-1].append(v)
            else:
                clusters.append([v])
        return [sum(c) / len(c) for c in clusters]

    clustered_peaks = cluster(peaks)
    clustered_troughs = cluster(troughs)

    return {
        "support": round(min(troughs) if troughs else min(recent), 2),
        "resistance": round(max(peaks) if peaks else max(recent), 2),
        "strong_support": round(min(clustered_troughs), 2) if clustered_troughs else round(min(recent), 2),
        "strong_resistance": round(max(clustered_peaks), 2) if clustered_peaks else round(max(recent), 2),
    }

def _calc_price_momentum(prices: list) -> dict:
    """计算多个时间维度的价格动量"""
    if not prices:
        return {}
    current = prices[-1]
    return {
        "momentum_1d": round((current - prices[-2]) / prices[-2] * 100, 2) if len(prices) >= 2 and prices[-2] > 0 else 0,
        "momentum_3d": round((current - prices[-4]) / prices[-4] * 100, 2) if len(prices) >= 4 and prices[-4] > 0 else 0,
        "momentum_7d": round((current - prices[-8]) / prices[-8] * 100, 2) if len(prices) >= 8 and prices[-8] > 0 else 0,
        "momentum_14d": round((current - prices[-15]) / prices[-15] * 100, 2) if len(prices) >= 15 and prices[-15] > 0 else 0,
        "momentum_30d": round((current - prices[-31]) / prices[-31] * 100, 2) if len(prices) >= 31 and prices[-31] > 0 else 0,
    }

def predict_skin_trend(skin_id: int) -> dict:
    """
    饰品价格预测模型 v1.0
    基于技术分析（SMA、RSI、布林带、MACD、支撑阻力）预测短期价格走向
    """
    info = get_skin_detail(skin_id)
    if not info:
        return {"错误": "饰品不存在"}

    name = info.get('name', '')
    current_price = info.get('buff_sell_price', 0) or 0
    chart_data = None
    prices = []
    num_data = []
    timestamps = []
    used_local = False

    # 先试120天，超时就降级到90天
    for period in [120, 90]:
        try:
            chart_data = get_chart_data(skin_id, 'sell_price', 1, period)
            if chart_data and chart_data.get('timestamp') and len(chart_data.get('main_data', [])) >= 20:
                break
            chart_data = None
        except Exception as e:
            err_str = str(e)
            if 'timeout' in err_str.lower() or 'timed out' in err_str.lower() or 'timeout' in type(e).__name__.lower():
                print(f"   ⚠️ 获取{period}天数据超时，尝试降级...")
            else:
                print(f"   ⚠️ 获取{period}天数据异常: {e}")
            chart_data = None

    if chart_data and chart_data.get('timestamp'):
        timestamps = chart_data['timestamp']
        prices = [p if p is not None else 0 for p in chart_data['main_data']]
        num_data = chart_data.get('num_data', [])
        chart_days = len(set(datetime.fromtimestamp(t / 1000).strftime('%Y-%m-%d') for t in timestamps))
    else:
        chart_days = 0

    # 如果 API 数据不足，从本地历史数据库补全
    local_data = get_local_price_history(skin_id, 120)
    if len(prices) < 20 and local_data and len(local_data) >= 5:
        local_prices = [p['price'] for p in local_data]
        # 避免重复：API 最近一天和本地最后一天相同则跳过
        same_last_day = False
        if timestamps and chart_days > 0 and local_data:
            api_last_day = datetime.fromtimestamp(timestamps[-1] / 1000).strftime('%Y-%m-%d')
            if local_data[-1]['date'] == api_last_day:
                same_last_day = True
        if same_last_day:
            local_prices = local_prices[:-1]
        # 用本地数据覆盖或补全
        if len(prices) < len(local_prices):
            prices = local_prices
            used_local = True
            print(f"   📀 使用本地历史数据 ({len(prices)} 天) 补充 API 数据不足")

    if len(prices) < 20:
        # 把该饰品加入采集队列，确保下次有数据
        collector_skin_queue.add(skin_id)
        return {"错误": f"历史数据不足（仅{len(prices)}个数据点），已加入采集队列，请过几小时后再试"}

    if current_price <= 0:
        collector_skin_queue.add(skin_id)
        return {"错误": "当前饰品价格异常（≤0），无法进行技术分析，已加入采集队列"}

    # 检查价格是否全部相同（技术指标无法计算）
    if max(prices) - min(prices) < 0.01:
        print(f"   ⚠️ 价格序列几乎无波动 ({min(prices)}~{max(prices)})，数据可能存在问题")
        # 极端情况下仍然继续，技术指标会给出中性结果

    collector_skin_queue.add(skin_id)

    # === 1. 计算所有技术指标 ===
    sma_5 = _calc_sma(prices, 5)
    sma_20 = _calc_sma(prices, 20)
    sma_60 = _calc_sma(prices, 60)
    rsi_values = _calc_rsi(prices, 14)
    bollinger = _calc_bollinger(prices, 20)
    macd = _calc_macd(prices)
    momentum = _calc_price_momentum(prices)
    sr = _find_support_resistance(prices, 30)

    last_idx = len(prices) - 1
    current_sma_5 = sma_5[last_idx] if sma_5[last_idx] is not None else current_price
    current_sma_20 = sma_20[last_idx] if sma_20[last_idx] is not None else current_price
    current_sma_60 = sma_60[last_idx] if sma_60[last_idx] is not None else current_price
    current_rsi = rsi_values[last_idx] if rsi_values[last_idx] is not None else 50
    current_upper = bollinger["upper"][last_idx] if bollinger["upper"][last_idx] is not None else current_price * 1.1
    current_lower = bollinger["lower"][last_idx] if bollinger["lower"][last_idx] is not None else current_price * 0.9
    current_macd = macd["macd"][last_idx] if macd["macd"][last_idx] is not None else 0
    current_signal = macd["signal"][last_idx] if macd["signal"][last_idx] is not None else 0
    current_hist = macd["histogram"][last_idx] if macd["histogram"][last_idx] is not None else 0

    # === 2. 多维评分系统（-100 ~ +100）===
    trend_score = 0      # 趋势得分
    momentum_score = 0   # 动量得分
    volatility_score = 0 # 波动得分
    volume_score = 0     # 成交量得分

    # ---------- 趋势分析（权重40%） ----------
    # SMA 排列判断趋势
    if current_sma_5 > current_sma_20 > current_sma_60:
        trend_score += 40  # 多头排列，强上升趋势
    elif current_sma_5 > current_sma_20:
        trend_score += 20  # 短中期多头
    elif current_sma_5 < current_sma_20 < current_sma_60:
        trend_score -= 40  # 空头排列，强下降趋势
    elif current_sma_5 < current_sma_20:
        trend_score -= 20  # 短中期空头

    # 价格 vs SMA
    sma_5_ratio = current_price / current_sma_5 if current_sma_5 > 0 else 1
    if sma_5_ratio > 1.03:
        trend_score += 10  # 价格在 SMA5 上方，短期强势
    elif sma_5_ratio < 0.97:
        trend_score -= 10  # 价格在 SMA5 下方，短期弱势

    # SMA 金叉/死叉判断（最近5期内是否发生）
    for i in range(max(0, last_idx - 5), last_idx):
        if sma_5[i] is not None and sma_20[i] is not None:
            prev_sma_5 = sma_5[i-1] if i > 0 and sma_5[i-1] is not None else sma_5[i]
            prev_sma_20 = sma_20[i-1] if i > 0 and sma_20[i-1] is not None else sma_20[i]
            if prev_sma_5 <= prev_sma_20 and sma_5[i] > sma_20[i]:
                trend_score += 15  # 金叉
                break
            elif prev_sma_5 >= prev_sma_20 and sma_5[i] < sma_20[i]:
                trend_score -= 15  # 死叉
                break

    # ---------- 动量分析（权重30%） ----------
    # RSI 判断
    if current_rsi < 25:
        momentum_score += 40  # 严重超卖，反弹概率大
    elif current_rsi < 35:
        momentum_score += 25  # 超卖
    elif current_rsi < 45:
        momentum_score += 10  # 偏弱但接近中性
    elif current_rsi > 75:
        momentum_score -= 40  # 严重超买，回调风险大
    elif current_rsi > 65:
        momentum_score -= 25  # 超买
    elif current_rsi > 55:
        momentum_score -= 10  # 偏强但接近中性

    # MACD 判断
    if current_macd > current_signal and current_hist > 0:
        momentum_score += 20  # MACD 金叉，动量向上
    elif current_macd < current_signal and current_hist < 0:
        momentum_score -= 20  # MACD 死叉，动量向下

    # MACD 柱体变化趋势（最近3期）
    hist_trend = 0
    for i in range(max(2, last_idx - 2), last_idx + 1):
        if macd["histogram"][i] is not None and macd["histogram"][i-1] is not None:
            if macd["histogram"][i] > macd["histogram"][i-1]:
                hist_trend += 1
            elif macd["histogram"][i] < macd["histogram"][i-1]:
                hist_trend -= 1
    if hist_trend >= 2:
        momentum_score += 10  # 柱体持续放大，动量增强
    elif hist_trend <= -2:
        momentum_score -= 10  # 柱体持续缩小，动量减弱

    # 短期价格动量
    mom_7d = momentum.get("momentum_7d", 0)
    if mom_7d > 15:
        momentum_score -= 15  # 短期涨太快，回调风险
    elif mom_7d < -15:
        momentum_score += 15  # 短期跌太猛，反弹机会
    elif mom_7d > 5:
        momentum_score += 5
    elif mom_7d < -5:
        momentum_score -= 5

    # ---------- 波动分析（权重15%） ----------
    # 布林带位置
    bb_range = current_upper - current_lower
    if bb_range > 0:
        bb_position = (current_price - current_lower) / bb_range
        if bb_position > 0.95:
            volatility_score -= 20  # 触及上轨，可能回调
        elif bb_position < 0.05:
            volatility_score += 20  # 触及下轨，可能反弹
        elif bb_position > 0.8:
            volatility_score -= 10
        elif bb_position < 0.2:
            volatility_score += 10

    # 布林带宽度变化（带宽收窄→可能变盘）
    if len(prices) > 25:
        bb_width_now = (current_upper - current_lower) / current_sma_20 if current_sma_20 > 0 else 0
        prev_upper = bollinger["upper"][last_idx-5] if bollinger["upper"][last_idx-5] is not None else current_upper
        prev_lower = bollinger["lower"][last_idx-5] if bollinger["lower"][last_idx-5] is not None else current_lower
        prev_sma_20_val = sma_20[last_idx-5] if sma_20[last_idx-5] is not None else current_sma_20
        bb_width_prev = (prev_upper - prev_lower) / prev_sma_20_val if prev_sma_20_val > 0 else 0
        if bb_width_now < bb_width_prev * 0.9:
            volatility_score += 10  # 带宽收窄，蓄势待变

    # ---------- 成交量分析（权重15%） ----------
    if len(num_data) > 5 and any(n is not None for n in num_data):
        recent_volumes = [n for n in num_data[-10:] if n is not None]
        older_volumes = [n for n in num_data[-20:-10] if n is not None]
        if recent_volumes and older_volumes:
            avg_recent = sum(recent_volumes) / len(recent_volumes)
            avg_older = sum(older_volumes) / len(older_volumes)
            if avg_older > 0:
                vol_ratio = avg_recent / avg_older
                if vol_ratio > 1.5 and trend_score > 0:
                    volume_score += 15  # 放量上涨
                elif vol_ratio > 1.5 and trend_score < 0:
                    volume_score -= 15  # 放量下跌
                elif vol_ratio < 0.5 and trend_score < 0:
                    volume_score += 5   # 缩量下跌，抛压减弱

    # === 3. 综合评分 ===
    total_score = trend_score * 0.4 + momentum_score * 0.3 + volatility_score * 0.15 + volume_score * 0.15
    total_score = max(-100, min(100, total_score))

    # === 4. 预测结论 ===
    if total_score >= 35:
        if current_rsi < 30:
            prediction = "📈 看涨（强烈）"
            direction = "up_strong"
            confidence = "高"
            summary = f"RSI已经跌到 {current_rsi:.0f}，严重超卖了，这种深跌在历史上基本都跟着一波强力反弹。技术面和情绪面都在酝酿反转信号。"
        else:
            prediction = "📈 看涨"
            direction = "up"
            confidence = "中高"
            summary = "多项技术指标共振向上，短期趋势已经走出来了。跟着趋势走，不猜顶，不吃亏。"
    elif total_score >= 15:
        prediction = "↗️ 偏多"
        direction = "up_weak"
        confidence = "中"
        summary = "指标微微偏多，虽然没有形成强烈信号，但多头已经开始试探了。可以小仓位试试水温。"
    elif total_score > -15:
        prediction = "➡️ 横盘震荡"
        direction = "neutral"
        confidence = "中"
        summary = "多空力量差不多，谁也没占着便宜。这种行情最忌讳频繁交易，耐心等方向选出来再说。"
    elif total_score > -35:
        prediction = "↘️ 偏空"
        direction = "down_weak"
        confidence = "中"
        summary = "指标微微偏空，空头稍微占优。虽然没到崩盘的程度，但最好别跟趋势对着干。"
    else:
        if current_rsi > 70:
            prediction = "📉 看跌（强烈）"
            direction = "down_strong"
            confidence = "高"
            summary = f"RSI已经到了 {current_rsi:.0f}，严重超买。历史经验告诉我们，这种位置追进去的基本都是接盘侠。"
        else:
            prediction = "📉 看跌"
            direction = "down"
            confidence = "中高"
            summary = "技术指标全都指向下跌，短期趋势偏弱。老话说得好——下跌趋势里别抄底，等企稳再说。"

    # === 5. 买卖建议 ===
    buy_advice = ""
    sell_advice = ""
    action = ""

    support = sr.get("support", current_price * 0.9)
    resistance = sr.get("resistance", current_price * 1.1)
    strong_support = sr.get("strong_support", current_price * 0.85)
    strong_resistance = sr.get("strong_resistance", current_price * 1.15)

    if direction in ("up", "up_strong", "up_weak"):
        action = "🟢 买入 / 持有"
        buy_advice = f"现价 ¥{current_price:.2f} 可以建仓，如果回踩到 ¥{support:.2f} 别犹豫，直接加仓。这个位置筹码性价比很高。"
        sell_advice = f"第一目标看 ¥{resistance:.2f}，一旦放量突破，下一站就是 ¥{strong_resistance:.2f}。分批止盈最稳妥。"
        if direction == "up_strong":
            buy_advice += " 信号很强，可以适当上仓位。"
    elif direction in ("down", "down_strong", "down_weak"):
        action = "🔴 卖出 / 回避"
        buy_advice = f"别急着接飞刀，等价格回到 ¥{strong_support:.2f} 附近再考虑。现在进去就是给别人抬轿子。"
        sell_advice = f"仓位重的建议先减一些，支撑位在 ¥{support:.2f} 附近。破了就走人，别扛单。"
        if direction == "down_strong":
            sell_advice += " 强烈卖出信号，这时候犹豫就是亏钱。"
    else:
        action = "⚪ 持有观望"
        buy_advice = f"想入手的话，在 ¥{support:.2f} ~ ¥{strong_support:.2f} 之间挂单慢慢接，别一次性打完子弹。"
        sell_advice = f"想出的可以在 ¥{resistance:.2f} ~ ¥{strong_resistance:.2f} 区间挂单，分批出最稳妥。"

    # === 6. 风险等级 ===
    bb_width_pct = (current_upper - current_lower) / current_price * 100 if current_price > 0 else 0
    if bb_width_pct > 15:
        risk_level = "高"
        risk_note = f"布林带宽度 {bb_width_pct:.1f}%，波动非常大。心脏不好的别重仓，这个幅度的波动分分钟让人破防。"
    elif bb_width_pct > 8:
        risk_level = "中"
        risk_note = f"布林带宽度 {bb_width_pct:.1f}%，正常波动范围。控制好仓位，不要一次性打光子弹。"
    else:
        risk_level = "低"
        risk_note = f"布林带宽度 {bb_width_pct:.1f}%，波动很小。成交不活跃的时候，大资金进出容易被针对。"

    # === 7. 生成技术指标摘要 ===
    indicators = {
        "RSI(14)": f"{current_rsi:.1f}",
        "RSI信号": "超买" if current_rsi > 70 else ("超卖" if current_rsi < 30 else "中性"),
        "SMA(5)": f"¥{current_sma_5:.2f}",
        "SMA(20)": f"¥{current_sma_20:.2f}",
        "SMA(60)": f"¥{current_sma_60:.2f}" if sma_60[last_idx] is not None else "N/A",
        "SMA排列": "多头排列 ↑" if current_sma_5 > current_sma_20 > current_sma_60 else ("空头排列 ↓" if current_sma_5 < current_sma_20 < current_sma_60 else "交叉/整理"),
        "布林上轨": f"¥{current_upper:.2f}",
        "布林下轨": f"¥{current_lower:.2f}",
        "布林位置": "上轨附近" if current_price > current_upper * 0.95 else ("下轨附近" if current_price < current_lower * 1.05 else "中轨区域"),
        "MACD": f"{current_macd:+.2f}",
        "MACD信号": f"{current_signal:+.2f}",
        "MACD柱": f"{current_hist:+.2f}",
        "MACD状态": "金叉 ↑" if current_macd > current_signal else "死叉 ↓",
        "支撑位": f"¥{support:.2f}",
        "阻力位": f"¥{resistance:.2f}",
        "强支撑": f"¥{strong_support:.2f}",
        "强阻力": f"¥{strong_resistance:.2f}",
    }

    return {
        "名称": name,
        "当前价格": f"¥{current_price}",
        "预测": {
            "方向": prediction,
            "综合评分": f"{total_score:+.1f}/100",
            "置信度": confidence,
            "摘要": summary,
            "趋势得分": f"{trend_score:+.1f}",
            "动量得分": f"{momentum_score:+.1f}",
            "波动得分": f"{volatility_score:+.1f}",
            "量能得分": f"{volume_score:+.1f}",
        },
        "买卖建议": {
            "操作建议": action,
            "买入建议": buy_advice,
            "卖出建议": sell_advice,
            "建议止损": f"¥{support * 0.95:.2f}" if direction in ("up", "up_strong", "neutral") else f"¥{current_price * 0.95:.2f}",
            "建议止盈": f"¥{resistance:.2f}",
        },
        "技术指标": indicators,
        "价格动量": momentum,
        "风险等级": risk_level,
        "风险提示": risk_note,
        "数据来源": "本地采集数据库" if used_local else "CSQAQ API",
    }

# ==================== API 接口 ====================

@app.get("/search")
def search_skins(q: str = Query(""), page: int = Query(0), page_size: int = Query(10)):
    if not q:
        return {"结果数": 0, "数据": [], "总页数": 0, "当前页": 0}
    conn = get_db()
    # 智能分词：自动分离中英文混合输入（如"液化mp5" → ["液化","mp5"]）
    # 同时兼容空格分词（如"AK 新红" → ["AK","新红"]）
    raw_tokens = q.split()
    tokens = []
    for t in raw_tokens:
        # 将单个token中的中英文分开
        parts = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', t)
        tokens.extend(parts)
    if not tokens:
        tokens = [q]
    # 去重保留顺序
    seen = set()
    tokens = [t for t in tokens if not (t in seen or seen.add(t))]
    conditions = ' AND '.join(['name LIKE ?' for _ in tokens])
    params = [f'%{t}%' for t in tokens]
    # 先查总数
    count_c = conn.execute(f'SELECT COUNT(*) FROM skins WHERE {conditions}', params)
    total = count_c.fetchone()[0]
    # 分页查询
    offset = page * page_size
    c = conn.execute(f'SELECT id, name FROM skins WHERE {conditions} ORDER BY id LIMIT ? OFFSET ?',
                     params + [page_size + 1, offset])  # 多取1条判断是否有下一页
    rows = c.fetchall()
    has_next = len(rows) > page_size
    r = [{"ID": row['id'], "名称": row['name']} for row in rows[:page_size]]
    conn.close()
    return {
        "结果数": total,
        "数据": r,
        "总页数": max(1, (total + page_size - 1) // page_size),
        "当前页": page,
        "下一页": page + 1 if has_next else -1,
    }

@app.get("/skin/{skin_id}")
def get_skin(skin_id: int):
    url = f"https://api.csqaq.com/api/v1/info/good?id={skin_id}"
    try:
        resp = requests.get(url, headers={'ApiToken': API_TOKEN}, verify=False, timeout=8)
    except requests.Timeout:
        return {"错误": "数据源超时，请稍后重试"}
    except requests.ConnectionError:
        return {"错误": "无法连接数据源，请检查网络"}
    data = safe_json(resp)
    if data and data.get('code') == 200:
        info = data['data']['goods_info']
        if not info:
            return {"错误": "未找到该饰品"}
        v = calculate_valuation(info, skin_id)

        # LLM 估值解读
        llm_val = None
        if DEEPSEEK_API_KEY:
            try:
                alchemy = float(v['分解']['炼金基价'].lstrip('¥'))
                is_st = 'StatTrak' in info.get('name', '')
                is_mw = any(info.get('name', '').startswith(g) for g in ['AK-47', 'AWP', 'USP'])
                is_mw_m4 = any(info.get('name', '').startswith(g) for g in ['M4A1', 'M4A4'])
                price = info.get('buff_sell_price', 0) or 0
                my_v = float(v['我的估值'].lstrip('¥'))
                my_v_actual = my_v if my_v <= price * 1.5 else price * 1.3
                llm_val = _call_deepseek_valuation(
                    info.get('name', ''), info.get('rarity_localized_name', ''),
                    info.get('exterior_localized_name', ''), price, my_v_actual,
                    alchemy, 1.0, 1.0, 1.0,
                    info.get('sell_price_rate_30', 0) or 0,
                    info.get('buff_sell_num', 0) or 0, is_st, is_mw, is_mw_m4)
            except:
                pass

        return {
            "名称": info['name'], "品质": info['rarity_localized_name'],
            "磨损": info['exterior_localized_name'],
            "Buff售价": f"¥{info['buff_sell_price']}", "Steam售价": f"¥{info['steam_sell_price']}",
            "悠悠售价": f"¥{info['yyyp_sell_price']}",
            "近30天涨跌": f"{info['sell_price_rate_30']:+.2f}%",
            "在售量(Buff)": f"{info['buff_sell_num']}个",
            "图片": info.get('img', ''),
            "估值分析": v,
            "估值解读": llm_val,
        }
    msg = data.get('msg', '') if data else ''
    if 'IP' in msg:
        return {"错误": "API Token IP 未绑定，请重启服务"}
    return {"错误": "查询失败"}

@app.get("/cases/roi/list")
def cases_roi_list():
    lst = get_roi_list_merged()
    return {"总数": len(lst), "列表": lst}

@app.get("/case/{case_id}/detail")
def case_detail(case_id: int):
    return get_case_detail(case_id)

@app.get("/case/{case_id}/roi")
def case_roi(case_id: int):
    data = get_container_roi_detail(case_id)
    if data:
        return data
    return {"错误": "获取失败"}

@app.get("/analysis/{skin_id}")
def ai_analysis(skin_id: int):
    return ai_analyze_skin(skin_id)

@app.get("/skin/{skin_id}/trend")
def skin_trend(skin_id: int, period: int = Query(90)):
    return get_price_trend(skin_id, period)

@app.get("/predict/{skin_id}")
def predict_skin(skin_id: int):
    """饰品价格预测（基于技术分析）"""
    try:
        info = get_skin_detail(skin_id)
        if info:
            save_price_snapshot(skin_id, info)
    except Exception as e:
        print(f"   ⚪ 预测触发快照采集跳过: {e}")
    try:
        return predict_skin_trend(skin_id)
    except Exception as e:
        print(f"   ❌ 预测过程异常 (skin_id={skin_id}): {e}")
        traceback.print_exc()
        return {"错误": f"预测过程异常: {str(e)}"}

@app.get("/mlpredict/{skin_id}")
def ml_predict_skin(skin_id: int):
    """ML价格预测（基于爬取数据和机器学习模型）"""
    try:
        from price_predictor import predict_by_skin_id
        result = predict_by_skin_id(skin_id)
        if 'error' in result:
            return {"错误": result['error']}
        # 追加模型指标信息
        try:
            import pickle, os
            from price_predictor import MODEL_FILE
            if os.path.exists(MODEL_FILE):
                with open(MODEL_FILE, 'rb') as f:
                    md = pickle.load(f)
                result['_model_r2'] = round(md.get('r2_score', 0), 4)
                result['_direction_acc'] = round(md.get('direction_accuracy', 0), 4)
                result['_model_type'] = md.get('model_type', 'RandomForest')
        except:
            pass
        return result
    except Exception as e:
        traceback.print_exc()
        return {"错误": f"ML预测异常: {str(e)}"}

@app.get("/ml/model_info")
def ml_model_info():
    """返回ML模型的基本信息和性能指标"""
    try:
        import pickle, os
        from price_predictor import MODEL_FILE
        if not os.path.exists(MODEL_FILE):
            return {"可用": False, "信息": "模型尚未训练"}
        with open(MODEL_FILE, 'rb') as f:
            md = pickle.load(f)
        trained_at = md.get('trained_at', 0)
        from datetime import datetime
        train_time_str = datetime.fromtimestamp(trained_at).strftime('%Y-%m-%d %H:%M') if trained_at else '未知'
        market_7d = md.get('market_7d', 0)
        market_30d = md.get('market_30d', 0)
        return {
            "可用": True,
            "R²": round(md.get('r2_score', 0), 4),
            "方向准确率": round(md.get('direction_accuracy', 0), 4),
            "特征维度": len(md.get('feature_keys', [])),
            "模型类型": md.get('model_type', 'RandomForest'),
            "训练时间": train_time_str,
            "训练时大盘状态": f"7d={market_7d:+.2f}%, 30d={market_30d:+.2f}%",
            "自动重训": "每24小时自动用最新大盘数据更新",
            "说明": "该模型基于历史价格和交易数据训练，仅预测价格涨跌方向和大致幅度，准确率有限，不可作为投资决策的唯一依据。"
        }
    except Exception as e:
        return {"可用": False, "信息": str(e)}

@app.get("/ml/retrain")
def ml_retrain():
    """手动触发模型重训（用最新大盘数据）"""
    try:
        import threading
        from price_predictor import train_model
        def _train():
            print("   🔄 手动触发模型重训...")
            train_model(force=True)
            print("   ✅ 重训完成")
        threading.Thread(target=_train, daemon=True).start()
        return {"状态": "重训已启动", "说明": "重训约需1-2分钟，训练完成后自动生效"}
    except Exception as e:
        return {"错误": str(e)}

@app.get("/ml/hot_skins")
def ml_hot_skins():
    """返回热门饰品列表（按成交量从CSV实时排序）"""
    try:
        # 尝试从CSV按成交量排序
        import csv
        pairs = {}  # id -> (name, volume)
        for path in ['skins_full.csv', 'skins_raw(1).csv']:
            if not os.path.exists(path): continue
            with open(path, 'r', encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    sid = row.get('id','').strip()
                    if not sid: continue
                    name = row.get('name', '')
                    # 过滤武器箱、印花、涂鸦
                    if '武器箱' in name or 'Case' in name: continue
                    if '印花' in name or name.startswith('Sticker'): continue
                    if '涂鸦' in name or 'Graffiti' in name: continue
                    if '胶囊' in name or '钥匙' in name: continue
                    sell = float(row.get('sell_num',0) or 0)
                    buy = float(row.get('buy_num',0) or 0)
                    vol = sell + buy
                    if sid not in pairs or vol > pairs[sid][1]:
                        pairs[sid] = (name, int(vol))
        if pairs:
            # 按成交量排序取前9
            top = sorted(pairs.items(), key=lambda x: x[1][1], reverse=True)[:9]
            return [{"id": int(sid), "name": name} for sid, (name, vol) in top]
        raise Exception("CSV无数据")
    except Exception as e:
        # 降级到硬编码
        return [{"id": 21632, "name": "AK-47 新红浪潮"},
                {"id": 243, "name": "AWP 巨龙传说"},
                {"id": 19653, "name": "AK-47 传承"},
                {"id": 19558, "name": "格洛克 崩络克"},
                {"id": 21763, "name": "USP 脑洞大开"},
                {"id": 19771, "name": "沙漠之鹰 印花集"},
                {"id": 21819, "name": "AK-47 燃塔"},
                {"id": 21015, "name": "M4 龙王金刚"},
                {"id": 21497, "name": "M4A1 印花集"}]

@app.get("/collect/{skin_id}")
def collect_skin(skin_id: int):
    """手动采集一个饰品的价格快照"""
    ok = save_price_snapshot(skin_id)
    if ok:
        cnt = get_local_price_count(skin_id, 120)
        return {"状态": "成功", "饰品ID": skin_id, "本地已有天数": cnt}
    return {"错误": "采集失败，请检查饰品ID是否正确"}

@app.get("/collect/status")
def collect_status():
    """查看采集器运行状态"""
    total_skins = 0
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute('SELECT COUNT(DISTINCT skin_id) FROM price_history').fetchone()
        total_skins = row[0] if row else 0
        conn.close()
    except:
        pass
    return {
        "采集器运行中": collector_running,
        "采集队列长度": len(collector_skin_queue),
        "已采集饰品数": total_skins,
        "热门饰品": HOT_SKIN_IDS,
        "采集间隔": f"{COLLECTOR_INTERVAL//60}分钟",
    }

@app.get("/market/index")
def market_index():
    data = get_market_index()
    if data:
        return data
    return {"错误": "大盘数据暂不可用，请稍后重试"}

@app.get("/arbitrage/list")
def arbitrage_list(page: int = Query(1), res: int = Query(0), platforms: str = Query('BUFF-YYYP'),
                   sort_by: int = Query(1), min_price: float = Query(1), max_price: float = Query(5000),
                   turnover: int = Query(0), text: str = Query(''), fee_rate: float = Query(0.05)):
    items = get_arbitrage_list(page, res, platforms, sort_by, min_price, max_price, turnover, text)
    MIN_FEE_RMB = 0.14
    result = []
    for item in items:
        buff_sell = item.get('buff_sell_price', 0) or 0
        steam_buy = item.get('steam_buy_price', 0) or 0
        if steam_buy <= 0 or buff_sell <= 0:
            continue
        fee = max(steam_buy * fee_rate, MIN_FEE_RMB)
        steam_actual = round(steam_buy - fee, 2)
        ratio = round(buff_sell / steam_actual, 4) if steam_actual > 0 else 999
        profit_rate = round((steam_actual - buff_sell) / buff_sell * 100, 2)
        result.append({
            'id': item['id'], '名称': item.get('name', ''),
            'Buff售价': buff_sell, 'Steam求购价': steam_buy,
            'Steam手续费': round(fee, 2),
            'Steam到手余额': steam_actual,
            '挂刀比例': ratio,
            '收益率': f"{profit_rate}%",
            'Steam日成交量': item.get('turnover_number', 0),
        })
    return {"总数": len(result), "数据": sorted(result, key=lambda x: x['挂刀比例'])}

@app.get("/ui", response_class=HTMLResponse)
def ui():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base_dir, "index.html"), "r", encoding="utf-8") as f:
        return f.read()

@app.get("/img/zywoo.jpg")
async def zywoo_img():
    return FileResponse("zywoo.jpg", media_type="image/jpeg")

@app.get("/img/donk.jpg")
async def donk_img():
    return FileResponse("donk.jpg", media_type="image/jpeg")

if __name__ == "__main__":
    port = 8080
    for i, arg in enumerate(sys.argv):
        if arg in ("--port", "-p") and i + 1 < len(sys.argv):
            try:
                port = int(sys.argv[i + 1])
            except ValueError:
                pass

    print("👁️ CS2 ValuEyes v3.0 启动中...")
    if DEEPSEEK_API_KEY:
        print("   ✅ DEEPSEEK_API_KEY 已配置，AI 分析将使用 LLM 润色")
    else:
        print("   ⚠️ DEEPSEEK_API_KEY 未设置，AI 分析仅使用规则引擎（不影响核心功能）")
        print("     配置方式：在 .env 文件中添加 DEEPSEEK_API_KEY=你的key")
        print("     或者：export DEEPSEEK_API_KEY=你的key")
    print("   📡 绑定 IP 到 API 白名单...")
    bind_success = bind_current_ip()
    if not bind_success:
        print("   ⚠️ IP 绑定未确认，API 请求可能因 IP 校验失败")
    print("   ⏳ 预热大盘指数数据...")
    # 预热：首次获取大盘数据的API调用可能因IP绑定延迟而失败，这是正常的
    warmup_data = get_market_index()
    if warmup_data:
        print(f"   📊 大盘指数已加载")
    else:
        market_cache["time"] = 0  # 确保后续请求会重试
    print("   ⏳ 启动后台价格采集器...")
    start_background_collector()
    print("   ⏳ 预采集热门饰品价格...")
    try:
        for sid in HOT_SKIN_IDS[:5]:
            info = get_skin_detail(sid)
            if info:
                save_price_snapshot(sid, info)
            time.sleep(0.5)
        print(f"   ✅ 预采集完成，后续每{COLLECTOR_INTERVAL//60}分钟自动更新")
    except Exception as e:
        print(f"   ⚪ 预采集跳过（{e}），后台采集器将持续工作")
    print("   ⏳ 预热ML价格预测模型...")
    try:
        from price_predictor import train_model
        # 启动时用当前大盘数据重训，让模型感知最新市场态势
        import threading
        def _startup_retrain():
            print("   ⏳ 获取最新大盘数据重训ML模型...")
            train_model(force=True)
            print("   ✅ ML价格预测模型已就绪（含最新大盘态势）")
        threading.Thread(target=_startup_retrain, daemon=True).start()
    except Exception as e:
        print(f"   ⚪ ML模型加载跳过（{e}），预测降级为规则引擎")
    print(f"   {'GET':<8} /skin/{{id}}               查饰品价格+估值")
    print(f"   {'GET':<8} /search?q=                 搜索饰品")
    print(f"   {'GET':<8} /cases/roi/list            武器箱ROI排行榜")
    print(f"   {'GET':<8} /case/{{id}}/detail         武器箱详情（含内含物）")
    print(f"   {'GET':<8} /case/{{id}}/roi            回报率走势")
    print(f"   {'GET':<8} /analysis/{{id}}            AI 投资分析（核心创新）")
    print(f"   {'GET':<8} /predict/{{id}}             价格预测模型（技术分析 v1.0）")
    print(f"   {'GET':<8} /mlpredict/{{id}}           ML价格预测（机器学习 v1.0）")
    print(f"   {'GET':<8} /collect/{{id}}             手动采集价格快照")
    print(f"   {'GET':<8} /collect/status             采集器状态")
    print(f"   {'GET':<8} /skin/{{id}}/trend          价格走势（近90天）")
    print(f"   {'GET':<8} /market/index              大盘指数状态")
    print(f"   {'GET':<8} /arbitrage/list             挂刀行情（含手续费）")
    print(f"   {'GET':<8} /ui                         前端页面")
    print(f"\n   访问 http://localhost:{port}/ui")
    uvicorn.run(app, host="0.0.0.0", port=port)
