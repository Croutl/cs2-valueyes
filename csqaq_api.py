from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import requests
import json
import sqlite3
import uvicorn
import math
import os
import urllib3
import sys
from datetime import datetime, timedelta
urllib3.disable_warnings()

app = FastAPI(title="CS2 ValuEyes", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 可前往 https://csqaq.com/ 注册获取自己的 API Token
API_TOKEN = 'MVNYS1S7S2V3R7Q3E8T707H5'
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
    resp = requests.post(url, headers={'ApiToken': API_TOKEN, 'Content-Type': 'application/json'},
                         json=payload, verify=False, timeout=8)
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
def get_price_trend(skin_id: int):
    info = get_skin_detail(skin_id)
    if not info:
        return {"错误": "饰品不存在"}

    current_price = info.get('buff_sell_price', 0)
    chart_data = get_chart_data(skin_id, 'sell_price', 1, 90)

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
        # 最多30个点
        if len(price_chart) > 30:
            step = len(price_chart) // 30
            price_chart = price_chart[::step]
            if price_chart[-1] != daily[sorted(daily.keys())[-1]]:
                last_key = sorted(daily.keys())[-1]
                last_day = daily[last_key]
                price_chart.append({
                    '时间': last_key, '价格': last_day[-1]['price'],
                    '最高价': max(d['price'] for d in last_day),
                    '最低价': min(d['price'] for d in last_day),
                    '在售量': last_day[-1]['num'],
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
        print(f"   ⚠️ 大盘数据获取失败：{msg}")
        # 不清理缓存，下次请求自动重试
        market_cache["time"] = 0
    except requests.Timeout:
        print("   ⚠️ 大盘数据请求超时")
        market_cache["time"] = 0
    except requests.ConnectionError:
        print("   ⚠️ 大盘数据连接失败，请检查网络")
        market_cache["time"] = 0
    except Exception as e:
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
        signals.append(f"🔴 近7天涨幅 {rate_7:+.1f}%，超过30%阈值，短期过热，有回调风险")
        score -= 20
    elif rate_7 > 15:
        signals.append(f"🟡 近7天涨幅 {rate_7:+.1f}%，涨幅较大，注意追高风险")
        score -= 10
    elif rate_7 < -20:
        signals.append(f"🟢 近7天跌幅 {rate_7:+.1f}%，深度回调，可能存在低估机会")
        score += 15
    else:
        signals.append(f"⚪ 近7天涨跌 {rate_7:+.1f}%，处于正常波动范围")
        score += 5

    # 规则2：大盘 vs 个品
    if market_rate_7 > rate_7 + 10:
        signals.append(f"🟢 大盘近7天涨 {market_rate_7:+.1f}%，跑赢该饰品 {market_rate_7-rate_7:+.1f}%，可能有补涨空间")
        score += 15
    elif market_rate_7 < rate_7 - 10:
        signals.append(f"🟡 大盘仅涨 {market_rate_7:+.1f}%，该饰品涨 {rate_7:+.1f}%，跑赢大盘，注意获利回吐")
        score -= 10
    else:
        signals.append(f"⚪ 大盘近7天 {market_rate_7:+.1f}%，与个品走势基本同步")

    # 规则3：30天趋势判断
    if rate_30 < -25:
        signals.append(f"🟢 近30天跌 {rate_30:+.1f}%，中期超跌，反弹概率较大")
        score += 10
    elif rate_30 > 50:
        signals.append(f"🔴 近30天涨 {rate_30:+.1f}%，中期涨幅过大，泡沫风险较高")
        score -= 15
    else:
        signals.append(f"⚪ 近30天涨跌 {rate_30:+.1f}%，中期趋势平稳")

    # 规则4：流动性判断
    if sell_num > 1000:
        signals.append(f"⚪ Buff在售 {sell_num}件，流动性充足，买卖方便")
        score += 5
    elif sell_num > 100:
        signals.append(f"⚪ Buff在售 {sell_num}件，流动性一般")
    else:
        signals.append(f"🟡 Buff在售仅 {sell_num}件，流动性较差，变现可能较慢")
        score -= 5

    # 规则5：挂刀价值判断
    if steam_price > 0 and price > 0:
        arbitrage_ratio = round(price / steam_price, 3)
        if arbitrage_ratio < 0.8:
            signals.append(f"🟢 挂刀比例 {arbitrage_ratio}，低于0.8，有挂刀套利空间")
            score += 10
        elif arbitrage_ratio < 0.95:
            signals.append(f"⚪ 挂刀比例 {arbitrage_ratio}，处于合理范围")
        else:
            signals.append(f"🔴 挂刀比例 {arbitrage_ratio}，高于0.95，不适合挂刀")
            score -= 5

    # 规则6：30天大盘 vs 个品
    if market_rate_30 > rate_30 + 15:
        signals.append(f"🟢 大盘近30天涨 {market_rate_30:+.1f}%，该饰品仅涨 {rate_30:+.1f}%，大幅跑输大盘，补涨潜力大")
        score += 15
    elif market_rate_30 < rate_30 - 15:
        signals.append(f"🟡 大盘近30天跌 {market_rate_30:+.1f}%，但该饰品逆势涨 {rate_30:+.1f}%，独立行情持续性存疑")
        score -= 10
    elif market_rate_30 < -10 and rate_30 > -5:
        signals.append(f"🟡 大盘近30天跌 {market_rate_30:+.1f}%，该品相对抗跌，关注后续补跌风险")
        score -= 5
    else:
        signals.append(f"⚪ 大盘近30天 {market_rate_30:+.1f}%，与个品 {rate_30:+.1f}% 走势基本同步")

    # 综合评分 → 结论
    if score >= 70:
        verdict = "🟢 推荐买入"
        conclusion = "综合各项指标，该饰品目前被低估或处于合理买点，可以考虑入手。"
    elif score >= 50:
        verdict = "🟡 观望"
        conclusion = "综合各项指标，该饰品估值中性，建议观望等待更好的入场时机。"
    elif score >= 30:
        verdict = "🟠 谨慎"
        conclusion = "综合各项指标，该饰品存在一定高估风险，不建议现在买入。"
    else:
        verdict = "🔴 不推荐"
        conclusion = "综合各项指标，该饰品明显高估或趋势不佳，建议避开。"

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
                "content": """你是资深 CS2 饰品市场分析师，擅长用数据说话。你的分析报告要像专业投资顾问写给客户看的。

输出要求：
1. 先给出明确的结论判断（看涨/看跌/观望）
2. 然后从短期走势、中期趋势、大盘对比、流动性、性价比 5 个角度展开分析
3. 最后给出 1-2 条具体建议

写作风格：
- 数据要有依据，不要编造
- 语气专业但易懂，像是资深玩家在给朋友分析
- 控制在 300 字以内
- 每段 1-3 句话，不要长篇大论

核心规则必须遵守：
- 近7天涨幅超过30% → 必须提示短期过热有回调风险
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

请写一份简洁的饰品投资分析报告。"""
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
                "content": """你是 CS2 饰品估值分析师。你的任务是用通俗语言解释一个估值模型的结果，让玩家理解"为什么这个饰品值这个价"。

估值模型由四个维度组成：
1. 炼金基价：基于品质和磨损的基础价值
2. 存世稀缺：存世量越少系数越高
3. 市场深度：市场需求越旺盛系数越高
4. 趋势健康：超跌时溢价，暴涨时折价

输出要求：
- 先说结论：估值是高估还是低估了
- 简单解释哪个维度起了主要作用
- 控制在 100 字以内
- 语气像资深玩家聊天"""
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

请用通俗语言解释这个估值结果。"""
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

# ==================== API 接口 ====================

@app.get("/search")
def search_skins(q: str = Query("")):
    if not q:
        return {"结果数": 0, "数据": []}
    conn = get_db()
    c = conn.execute('SELECT id, name FROM skins WHERE name LIKE ? LIMIT 20', (f'%{q}%',))
    r = [{"ID": row['id'], "名称": row['name']} for row in c.fetchall()]
    conn.close()
    return {"结果数": len(r), "数据": r}

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
def skin_trend(skin_id: int):
    return get_price_trend(skin_id)

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
        print("     设置方式：set DEEPSEEK_API_KEY=你的key")
    print("   📡 绑定 IP 到 API 白名单...")
    bind_success = bind_current_ip()
    if not bind_success:
        print("   ⚠️ IP 绑定未确认，API 请求可能因 IP 校验失败")
    print("   ⏳ 预热大盘指数数据...")
    get_market_index()
    print(f"   {'GET':<8} /skin/{{id}}               查饰品价格+估值")
    print(f"   {'GET':<8} /search?q=                 搜索饰品")
    print(f"   {'GET':<8} /cases/roi/list            武器箱ROI排行榜")
    print(f"   {'GET':<8} /case/{{id}}/detail         武器箱详情（含内含物）")
    print(f"   {'GET':<8} /case/{{id}}/roi            回报率走势")
    print(f"   {'GET':<8} /analysis/{{id}}            AI 投资分析（核心创新）")
    print(f"   {'GET':<8} /skin/{{id}}/trend          价格走势（近90天）")
    print(f"   {'GET':<8} /market/index              大盘指数状态")
    print(f"   {'GET':<8} /arbitrage/list             挂刀行情（含手续费）")
    print(f"   {'GET':<8} /ui                         前端页面")
    print(f"\n   访问 http://localhost:{port}/ui")
    uvicorn.run(app, host="0.0.0.0", port=port)
