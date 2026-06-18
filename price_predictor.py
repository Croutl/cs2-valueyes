"""
CS2 饰品价格预测模型 v3.1
合并训练：skins_full.csv + skins_raw(1).csv 共约3.8万条数据
优化：缓存+超时保护，避免预测阻塞
"""
import os, csv, pickle, sqlite3, re, warnings, json, urllib3, time
import requests
import numpy as np
from collections import defaultdict
warnings.filterwarnings('ignore')
urllib3.disable_warnings()

MODEL_FILE = 'price_model.pkl'
DB_PATH = 'cs_skins.db' if os.path.exists('cs_skins.db') else None

# ⚠️ 请替换为你自己的 CSQAQ API Token，建议通过环境变量设置
CSQAQ_API_TOKEN = os.environ.get("CSQAQ_API_TOKEN", "")
if not CSQAQ_API_TOKEN:
    CSQAQ_API_TOKEN = 'DYLV71Z737M9M9M4A6M3I484'
API_TOKEN = CSQAQ_API_TOKEN
RARITY_ORDER = ['消费级','工业级','军规级','受限级','保密级','隐秘级','稀有','非凡','高级','传说']
WEAR_ORDER = ['崭新出厂','略有磨损','久经沙场','破损不堪','战痕累累']

RIFLES = {'AK-47','M4A1','M4A4','AWP','SG ','AUG','SSG ','SCAR','FAMAS','Galil'}
PISTOLS = {'USP','Glock','Desert Eagle','P250','Five-SeveN','Tec-9','CZ75','Dual','R8 '}
SMGS = {'MAC-10','MP9','PP-Bizon','P90','UMP','MP7','MP5'}
SHOTGUNS = {'XM1014','Nova','MAG-7','Sawed-Off'}
HEAVY = {'Negev','M249'}
KNIFE_PATTERN = re.compile(r'★|（★）|\(★\)|刺刀|折叠|穿肠|爪子|骷髅|猎杀|折刀|暗影|流浪')

# ====================== 大盘指数（带超时保护+长缓存） ======================

class MarketIndexFetcher:
    _cache = None
    _time = 0
    _CACHE_TTL = 600  # 10分钟缓存

    @classmethod
    def get(cls):
        now = time.time()
        if cls._cache and now - cls._time < cls._CACHE_TTL:
            return cls._cache
        try:
            url = "https://api.csqaq.com/api/v1/sub/kline?id=1&type=1day"
            resp = requests.get(url, headers={'ApiToken': API_TOKEN}, verify=False, timeout=5)
            data = resp.json()
            if data.get('code') == 200:
                kline = data.get('data', [])
                if kline and len(kline) >= 8:
                    today = kline[-1]['c']
                    week_ago = kline[-8]['c']
                    month_ago = kline[-31]['c'] if len(kline) >= 31 else kline[-8]['c']
                    cls._cache = {
                        'market_change_7d': round((today - week_ago) / week_ago * 100, 2) if week_ago > 0 else 0,
                        'market_change_30d': round((today - month_ago) / month_ago * 100, 2) if month_ago > 0 else 0,
                    }
                    cls._time = now
                    return cls._cache
        except:
            pass
        # 有缓存就用旧数据，没有就返回0
        if cls._cache:
            print("   ⚠️ 大盘指数获取失败，使用缓存数据")
            return cls._cache
        return {'market_change_7d': 0, 'market_change_30d': 0}

# ====================== 合并数据加载（skins_full.csv + skins_raw(1).csv） ======================

def get_csv_paths():
    """返回存在的CSV文件列表"""
    paths = []
    if os.path.exists('skins_full.csv'):
        paths.append('skins_full.csv')
    if os.path.exists('skins_raw(1).csv'):
        paths.append('skins_raw(1).csv')
    return paths

def load_merged_data():
    """合并加载两个CSV，去重（按id），skins_raw 没有 inventory 字段则补0"""
    seen_ids = set()
    merged = []
    for path in get_csv_paths():
        if not os.path.exists(path):
            continue
        with open(path, 'r', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                sid = row.get('id', '').strip()
                if not sid or sid in seen_ids:
                    continue
                seen_ids.add(sid)
                # 统一字段名（去掉BOM）
                clean = {}
                for k, v in row.items():
                    k = k.strip().lstrip('\ufeff')
                    clean[k] = v.strip() if v else ''
                # inventory字段不存在则补0
                if 'inventory' not in clean or not clean['inventory']:
                    clean['inventory'] = '0'
                merged.append(clean)
    return merged

# ====================== 特征工程 ======================

_data_cache = {"rows": None, "peer": None, "time": 0}

def get_wear(name):
    for w in WEAR_ORDER:
        if f'({w})' in name: return w
    return ''

def classify_weapon(name):
    if '印花' in name or name.startswith('Sticker'): return 'sticker'
    if '武器箱' in name or 'Case' in name: return 'case'
    if '涂鸦' in name or 'Graffiti' in name: return 'graffiti'
    if KNIFE_PATTERN.search(name) or name.startswith('★'): return 'knife'
    for group, names in [('rifle', RIFLES), ('pistol', PISTOLS), ('smg', SMGS),
                          ('shotgun', SHOTGUNS), ('heavy', HEAVY)]:
        for w in names:
            if w in name: return group
    return 'other'

def extract_history_features(db_path, skin_id):
    if not db_path: return {}
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            'SELECT price, source, collected_at FROM price_history WHERE skin_id=? ORDER BY collected_at',
            (skin_id,)).fetchall()
        conn.close()
    except:
        return {}
    if len(rows) < 2: return {}
    buff = [r[0] for r in rows if r[1] == 'buff_sell' and r[0] > 0]
    bbuy = [r[0] for r in rows if r[1] == 'buff_buy' and r[0] > 0]
    times = sorted(set(r[2][:10] for r in rows))
    feat = {}
    if len(buff) >= 3:
        mp = np.mean(buff)
        feat['hist_volatility'] = np.std(buff) / max(mp, 0.01)
        x = np.arange(len(buff))
        feat['hist_price_slope'] = np.polyfit(x, buff, 1)[0] / max(mp, 0.01) if np.std(x) > 0 else 0
    else:
        feat['hist_volatility'] = 0; feat['hist_price_slope'] = 0
    if len(buff) >= 2:
        feat['hist_short_term'] = (buff[-1] - buff[0]) / max(buff[0], 0.01) * 100
    else:
        feat['hist_short_term'] = 0
    feat['hist_buy_volatility'] = np.std(bbuy) / max(np.mean(bbuy), 0.01) if len(bbuy) >= 3 else 0
    feat['hist_data_days'] = min(len(times), 30)
    return feat

def extract_features(r, peer_median_price=0, hist=None, market=None):
    price = float(r.get('price', 0) or 0)
    steam = float(r.get('steam_price', 0) or 0)
    sell = float(r.get('sell_num', 0) or 0)
    buy = float(r.get('buy_num', 0) or 0)
    inv = float(r.get('inventory', 0) or 0)
    r7 = float(r.get('rate_7', 0) or 0)
    name = r.get('name', '')
    feat = {}
    feat['log_price'] = np.log(max(price, 0.01))
    feat['buy_sell_ratio'] = min(buy / max(sell, 1), 100)
    feat['total_volume'] = np.log(max(sell + buy, 1))
    feat['log_inventory'] = np.log(max(inv, 1))
    feat['has_inventory'] = 1 if inv > 0 else 0
    feat['rate_7'] = max(min(r7, 100), -100)
    feat['steam_premium'] = min((steam - price) / price, 5) if price > 0 and steam > 0 else 0
    rarity = r.get('rarity', '')
    feat['rarity_idx'] = RARITY_ORDER.index(rarity) if rarity in RARITY_ORDER else -1
    wear = r.get('wear', '')
    feat['wear_idx'] = WEAR_ORDER.index(wear) if wear in WEAR_ORDER else -1
    feat['relative_to_peers'] = min(price / peer_median_price, 10) if peer_median_price > 0 and price > 0 else 0
    wtype = classify_weapon(name)
    for t in ['rifle','pistol','smg','shotgun','knife','sticker','case','other']:
        feat[f'w_{t}'] = 1 if wtype == t else 0
    # === 交互特征 ===
    feat['wear_rarity_interact'] = feat['wear_idx'] * feat['rarity_idx']
    feat['price_volume_interact'] = feat['log_price'] * feat['buy_sell_ratio']
    m7 = market.get('market_change_7d', 0) if market else 0
    m30 = market.get('market_change_30d', 0) if market else 0
    # 关键实时特征：个品 vs 大盘的相对强弱（预测时使用实时大盘）
    feat['momentum_vs_market'] = max(min(feat['rate_7'] - m7, 100), -100)
    # 市场态势编码：熊市=0 震荡=1 牛市=2（虽训练时同值，但给模型市场背景）
    if abs(m7) < 2:
        feat['market_regime'] = 1  # 震荡
    elif m7 > 0:
        feat['market_regime'] = 2  # 牛市
    else:
        feat['market_regime'] = 0  # 熊市
    # 市场态势 × 物品类型交互（不同品类在不同市态下表现不同）
    wtype = classify_weapon(name)
    for t in ['rifle','pistol','knife','case']:
        feat[f'regime_{t}'] = feat['market_regime'] * (1 if wtype == t else 0)
    # 大盘30天趋势 × 个品7天动量交互
    feat['market_30d_momentum'] = m30 * feat['rate_7'] / 100
    feat['inventory_turnover'] = min(sell / max(inv, 1), 100) if inv > 0 else 0
    feat['buy_pressure'] = min(buy / max(inv, 1), 100) if inv > 0 else 0
    feat['steam_buff_ratio'] = min(steam / max(price, 0.01), 10) if price > 0 and steam > 0 else 1
    # === 物品特征 ===
    feat['is_stattrak'] = 1 if 'StatTrak' in name else 0
    feat['is_souvenir'] = 1 if '纪念品' in name else 0
    feat['ln_sell_num'] = np.log(max(sell, 1))
    feat['ln_buy_num'] = np.log(max(buy, 1))
    feat['sell_buy_imbalance'] = max(min((sell - buy) / max(sell + buy, 1), 1), -1)
    if hist:
        for k, v in hist.items(): feat[k] = v
    else:
        for k in ['hist_volatility','hist_price_slope','hist_short_term','hist_buy_volatility','hist_data_days']:
            feat[k] = 0
    # 不再将 market_change 作为直接特征（训练时是常量，无信息量）
    return feat

KEYS = [
    'log_price','buy_sell_ratio','total_volume','log_inventory','has_inventory',
    'rate_7','steam_premium','rarity_idx','wear_idx','relative_to_peers',
    'w_rifle','w_pistol','w_smg','w_shotgun','w_knife','w_sticker','w_case','w_other',
    'hist_volatility','hist_price_slope','hist_short_term','hist_buy_volatility','hist_data_days',
    'momentum_vs_market','market_regime',
    'regime_rifle','regime_pistol','regime_knife','regime_case',
    'market_30d_momentum',
    'wear_rarity_interact','price_volume_interact',
    'inventory_turnover','buy_pressure','steam_buff_ratio',
    'is_stattrak','is_souvenir','ln_sell_num','ln_buy_num','sell_buy_imbalance',
    'is_fallback',
]

# ====================== 数据加载（5分钟缓存） ======================

def load_data(force=False):
    now = time.time()
    if not force and _data_cache["rows"] and now - _data_cache["time"] < 300:
        return _data_cache["rows"]
    rows = load_merged_data()
    _data_cache["rows"] = rows
    _data_cache["time"] = now
    _data_cache["peer"] = None
    print(f"   📊 加载合并数据: {len(rows)} 条")
    return rows

def build_peer_stats(rows):
    if _data_cache.get("peer"):
        return _data_cache["peer"]
    groups = defaultdict(list)
    for r in rows:
        p = float(r.get('price', 0) or 0)
        if p > 1:
            groups[(r.get('rarity',''), r.get('wear',''))].append(p)
    peer = {k: sorted(v)[len(v)//2] for k, v in groups.items() if v}
    _data_cache["peer"] = peer
    return peer

def prepare_training_data(rows, market=None):
    peer_median = build_peer_stats(rows)
    X, y = [], []
    skipped = {'no_target': 0, 'bad_rate30': 0, 'extreme': 0, 'low_price': 0}
    fallback_used = 0
    for r in rows:
        # 优先用 rate_30，没有则回退到 rate_7
        target_str = r.get('rate_30', '').strip().lstrip('+')
        is_fallback = 0
        if not target_str:
            target_str = r.get('rate_7', '').strip().lstrip('+')
            if target_str:
                is_fallback = 1
        if not target_str:
            skipped['no_target'] += 1
            continue
        try:
            target = float(target_str)
        except ValueError:
            skipped['bad_rate30'] += 1
            continue
        if abs(target) > 200:
            skipped['extreme'] += 1
            continue
        target = max(min(target, 100), -100)
        if float(r.get('price', 0) or 0) <= 0.1:
            skipped['low_price'] += 1
            continue

        pid = int(r.get('id', 0))
        h = extract_history_features(DB_PATH, pid)
        pm = peer_median.get((r.get('rarity',''), r.get('wear','')), 0)
        f = extract_features(r, pm, h, market)
        f['is_fallback'] = is_fallback
        X.append([f[k] for k in KEYS])
        y.append(target)
        if is_fallback:
            fallback_used += 1

    total_skipped = sum(skipped.values())
    if total_skipped:
        print(f"   ⏭ 跳过 {total_skipped} 条: 无目标值={skipped['no_target']} 格式错误={skipped['bad_rate30']} 极值={skipped['extreme']} 低价={skipped['low_price']}")
    if fallback_used:
        print(f"   🔄 用 rate_7 替代 rate_30: {fallback_used} 条")
    return np.nan_to_num(np.array(X), nan=0), np.array(y), peer_median

# ====================== 训练 ======================

def train_model(force=False):
    if os.path.exists(MODEL_FILE) and not force:
        return
    from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import r2_score

    rows = load_data(force=True)
    if not rows:
        print("   ❌ 无数据文件，跳过训练")
        return
    print('⏳ 获取大盘数据...')
    market = MarketIndexFetcher.get()
    print(f'   大盘: 7d={market.get("market_change_7d",0):+.2f}%  30d={market.get("market_change_30d",0):+.2f}%')
    print('⏳ 准备训练数据...')
    t0 = time.time()
    X, y, peer_median = prepare_training_data(rows, market)
    if len(X) < 500:
        print(f"   ❌ 训练样本不足: {len(X)}")
        return
    print(f'⏳ 训练样本: {len(X)} 特征维度: {X.shape[1]} (耗时 {time.time()-t0:.1f}s)')

    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.1, random_state=42)

    w_train = np.ones(len(y_train))
    w_train[y_train > 0] = 1.8

    # 训练两个模型，选最优
    models = []

    # 1) RandomForest
    rf = RandomForestRegressor(
        n_estimators=800, max_depth=15, min_samples_leaf=3,
        min_samples_split=2, random_state=42, n_jobs=-1
    )
    rf.fit(X_train, y_train, sample_weight=w_train)
    rf_pred = rf.predict(X_val)
    rf_r2 = r2_score(y_val, rf_pred)
    rf_dir = np.mean((y_val > 0) == (rf_pred > 0))
    models.append(('RandomForest', rf, rf_r2, rf_dir))
    print(f'   RandomForest:  R²={rf_r2:.4f}  方向准确率={rf_dir:.2%}')

    # 2) GradientBoosting（通常对结构化数据更好）
    gb = GradientBoostingRegressor(
        n_estimators=500, max_depth=5, min_samples_leaf=5,
        learning_rate=0.05, subsample=0.8, random_state=42
    )
    gb.fit(X_train, y_train, sample_weight=w_train)
    gb_pred = gb.predict(X_val)
    gb_r2 = r2_score(y_val, gb_pred)
    gb_dir = np.mean((y_val > 0) == (gb_pred > 0))
    models.append(('GradientBoosting', gb, gb_r2, gb_dir))
    print(f'   GradientBoosting:  R²={gb_r2:.4f}  方向准确率={gb_dir:.2%}')

    # 选最优
    models.sort(key=lambda m: m[2], reverse=True)
    best_name, best_model, best_r2, best_dir = models[0]
    print(f'\n✅ 选用 {best_name}:  R²={best_r2:.4f}  方向准确率={best_dir:.2%}')

    with open(MODEL_FILE, 'wb') as f:
        pickle.dump({
            'model': best_model, 'feature_keys': KEYS,
            'r2_score': best_r2, 'direction_accuracy': best_dir,
            'model_type': best_name,
            'trained_at': time.time(),
            'market_7d': market.get('market_change_7d', 0),
            'market_30d': market.get('market_change_30d', 0),
            'rarity_order': RARITY_ORDER, 'wear_order': WEAR_ORDER,
        }, f)
    print('✅ 模型已保存')

def load_model(allow_retrain=True):
    RETRAIN_INTERVAL = 86400  # 24小时后自动重训（获取新的大盘数据）
    if not os.path.exists(MODEL_FILE):
        if allow_retrain:
            train_model()
        else:
            return None
    if os.path.exists(MODEL_FILE):
        # 检查是否需要自动重训
        if allow_retrain:
            try:
                with open(MODEL_FILE, 'rb') as f:
                    md = pickle.load(f)
                trained_at = md.get('trained_at', 0)
                # 如果模型超过24小时，自动重训
                if trained_at and (time.time() - trained_at > RETRAIN_INTERVAL):
                    print('   🔄 模型已过期(>24h)，自动用最新大盘数据重训...')
                    train_model(force=True)
                    with open(MODEL_FILE, 'rb') as f:
                        return pickle.load(f)
                return md
            except:
                pass
        with open(MODEL_FILE, 'rb') as f:
            return pickle.load(f)
    return None

# ====================== 预测 ======================

def predict_by_skin_id(skin_id):
    t_start = time.time()
    rows = load_data()
    if not rows:
        return {'error': '数据文件不存在'}

    target = None
    for r in rows:
        if str(r.get('id','')).strip() == str(skin_id).strip():
            target = r
            break
    if not target:
        return {'error': f'未找到ID {skin_id} 的饰品'}

    price = float(target.get('price', 0) or 0)
    rate_7_str = target.get('rate_7', '0').strip().lstrip('+')
    rate_30_str = target.get('rate_30', '0').strip().lstrip('+')
    try:
        rate_7 = float(rate_7_str) if rate_7_str else 0
        rate_30 = float(rate_30_str) if rate_30_str else 0
    except ValueError:
        rate_7 = 0
        rate_30 = 0

    rarity = target.get('rarity', '')
    wear = target.get('wear', '')
    sell = float(target.get('sell_num', 0) or 0)
    buy = float(target.get('buy_num', 0) or 0)
    inv_str = target.get('inventory', '0')
    try:
        inv = float(inv_str) if inv_str else 0
    except ValueError:
        inv = 0
    name = target.get('name', '')

    # 大盘（带缓存，不会阻塞）
    market = MarketIndexFetcher.get()
    peer_median = build_peer_stats(rows)
    pm = peer_median.get((rarity, wear), 0)
    h = extract_history_features(DB_PATH, int(skin_id))
    f = extract_features(target, pm, h, market)
    f['is_fallback'] = 0
    X = np.nan_to_num(np.array([[f[k] for k in KEYS]]), nan=0)

    model_data = load_model()
    if model_data and price > 1:
        model = model_data['model']
        predicted_rate = float(model.predict(X)[0])
        r2 = model_data.get('r2_score', 0)
    else:
        predicted_rate = rate_7 * 0.5 + rate_30 * 0.3
        r2 = 0

    # 综合评分
    score = 50
    signals = []
    if predicted_rate > 10:
        signals.append(f"模型预测未来30天上涨 {predicted_rate:+.1f}%，趋势向好"); score += 20
    elif predicted_rate > 3:
        signals.append(f"模型预测温和上涨 {predicted_rate:+.1f}%"); score += 10
    elif predicted_rate < -10:
        signals.append(f"模型预测未来30天下跌 {predicted_rate:+.1f}%，趋势偏弱"); score -= 20
    elif predicted_rate < -3:
        signals.append(f"模型预测小幅下跌 {predicted_rate:+.1f}%"); score -= 10
    else:
        signals.append(f"模型预测横盘震荡 {predicted_rate:+.1f}%")

    buy_sell = buy / max(sell, 1)
    if buy_sell > 0.5:
        signals.append(f"求购/在售比 {buy_sell:.2f}，买盘强劲"); score += 15
    elif buy_sell < 0.05:
        signals.append(f"求购/在售比 {buy_sell:.2f}，无人求购"); score -= 15
    elif buy_sell < 0.15:
        signals.append(f"求购/在售比 {buy_sell:.2f}，买盘偏弱"); score -= 5
    else:
        signals.append(f"求购/在售比 {buy_sell:.2f}，供需平衡"); score += 5

    if inv > 0:
        if inv < 100: signals.append(f"存世仅 {int(inv)} 件，极度稀缺"); score += 20
        elif inv < 500: signals.append(f"存世 {int(inv)} 件，较为稀缺"); score += 10
        elif inv > 5000: signals.append(f"存世 {int(inv)} 件，流通量较大"); score -= 5

    m7 = market.get('market_change_7d', 0)
    vol = h.get('hist_volatility', 0)
    if m7 > rate_7 + 8:
        signals.append(f"大盘涨 {m7:+.1f}% 个品仅涨 {rate_7:+.1f}%，跑输大盘有补涨潜力"); score += 10
    elif m7 < rate_7 - 8:
        signals.append(f"个品涨 {rate_7:+.1f}% 远超大盘 {m7:+.1f}%，独立行情持续性存疑"); score -= 10

    if pm and price > 0:
        ratio = price / pm
        if ratio < 0.7: signals.append(f"价格 ¥{price:.0f} < 同类中位数 ¥{pm:.0f}，相对低估"); score += 15
        elif ratio > 1.5: signals.append(f"价格 ¥{price:.0f} > 同类中位数 ¥{pm:.0f}，相对高估"); score -= 10

    if 0 < vol < 0.02:
        signals.append(f"近期价格波动率 {vol:.2%}，走势稳定"); score += 5

    score = max(0, min(100, score))

    # 置信度：基于 R² 和评分的综合判断
    confidence = max(min(int(r2 * 100 + abs(predicted_rate) / 5), 99), 1)
    if r2 > 0.3:
        if score >= 70 or score <= 25:
            conf_label = f"{confidence}% (较高)"
        else:
            conf_label = f"{confidence}% (中等)"
    elif r2 > 0.2:
        conf_label = f"{confidence}% (一般)"
    else:
        conf_label = f"{confidence}% (偏低)"

    if score >= 70:
        direction = "📈 强烈看涨"; action_advice = "建议买入"
        buy_tip = f"现价 ¥{price:.2f} 性价比不错，可建仓。"
        sell_tip = "目标看涨，持有为主。"
    elif score >= 55:
        direction = "📈 看涨"; action_advice = "建议关注"
        buy_tip = f"现价 ¥{price:.2f} 可小仓位试探。"
        sell_tip = "短期持有，设好止损。"
    elif score >= 40:
        direction = "➡️ 中性"; action_advice = "建议观望"
        buy_tip = "等价格回到更安全的位置。"
        sell_tip = "已有持仓可继续持有。"
    elif score >= 25:
        direction = "📉 看跌"; action_advice = "建议回避"
        buy_tip = "下行风险较大，不建议入场。"
        sell_tip = "建议减仓控制风险。"
    else:
        direction = "📉 强烈看跌"; action_advice = "建议卖出"
        buy_tip = "明显高估，不要追高。"
        sell_tip = "建议及时止损出局。"

    elapsed = round(time.time() - t_start, 2)
    return {
        '名称': name, '当前价格': f'¥{price:.2f}', '品质': rarity, '磨损': wear,
        '预测方向': direction, '综合评分': f'{score}/100',
        '置信度': conf_label, '置信度说明': f'模型R²={r2:.3f}，结合评分{score}分综合判断',
        '预测摘要': f"综合评分 {score}/100，{action_advice}。模型预测 {predicted_rate:+.2f}%",
        '操作建议': action_advice,
        '买入建议': buy_tip, '卖出建议': sell_tip,
        '信号明细': signals,
        '关键数据': {
            '模型预测涨跌幅': f'{predicted_rate:+.2f}%',
            '近7天涨跌': f'{rate_7:+.2f}%',
            '求购/在售比': f'{buy_sell:.3f}',
            '存世量': f'{int(inv)}' if inv > 0 else '暂无',
            '大盘近7天': f'{m7:+.2f}%',
            '历史波动率': f'{vol:.2%}' if vol else '暂无',
            '同类中位数价格': f'¥{pm:.0f}' if pm else '暂无',
        },
        '技术说明': f'model v3.1 合并训练 | R²={r2:.3f} | 耗时{elapsed}s',
    }
