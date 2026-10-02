"""
Оптимизационные модули (без симуляции времени):
1) распределение порожних вагонов по заявкам на погрузку: «как сейчас»
   (жадно, ближайший свободный по очереди заявок) против оптимума (ЛП);
2) обмен вагонами между странами СНГ: излишек одной страны → дефицит другой.
"""
import numpy as np
from scipy.optimize import linprog


def _distance(cfg, a, b):
    if cfg.WM_DISTANCES_KM:
        if (a, b) in cfg.WM_DISTANCES_KM:
            return float(cfg.WM_DISTANCES_KM[(a, b)])
        if (b, a) in cfg.WM_DISTANCES_KM:
            return float(cfg.WM_DISTANCES_KM[(b, a)])
        raise KeyError(f"Нет расстояния между {a} и {b} в WM_DISTANCES_KM")
    km = {s["name"]: float(s["km"]) for s in cfg.STATIONS}
    return abs(km[a] - km[b])


def empty_wagon_distribution(cfg) -> dict:
    supply = {k: int(v) for k, v in cfg.WM_EMPTY_SUPPLY.items()}
    demand = {k: int(v) for k, v in cfg.WM_LOADING_DEMAND.items()}
    src, dst = list(supply), list(demand)
    dist = np.array([[_distance(cfg, s, d) for d in dst] for s in src])
    cost_km = float(cfg.WM_EMPTY_RUN_COST_KZT_WAGON_KM)

    # «как сейчас»: заявки обслуживаются по очереди ближайшими свободными вагонами
    left = dict(supply)
    greedy_km, greedy_unmet = 0.0, 0
    for j, d in enumerate(dst):
        need = demand[d]
        for i in np.argsort(dist[:, j]):
            if need == 0:
                break
            take = min(need, left[src[i]])
            left[src[i]] -= take
            need -= take
            greedy_km += take * dist[i, j]
        greedy_unmet += need

    # оптимум: минимум вагоно-км; невыполненные заявки сильно штрафуются
    ns, nd = len(src), len(dst)
    big = (dist.max() if dist.size else 1.0) * 1000 + 1
    c = np.concatenate([dist.ravel(), np.full(nd, big)])
    a_ub = np.zeros((ns, ns * nd + nd))
    for i in range(ns):
        a_ub[i, i * nd:(i + 1) * nd] = 1
    b_ub = np.array([supply[s] for s in src])
    a_eq = np.zeros((nd, ns * nd + nd))
    for j in range(nd):
        a_eq[j, j:ns * nd:nd] = 1
        a_eq[j, ns * nd + j] = 1
    b_eq = np.array([demand[d] for d in dst])
    res = linprog(c, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=(0, None), method="highs")
    x = res.x[:ns * nd].reshape(ns, nd)
    opt_km = float((x * dist).sum())
    opt_unmet = float(res.x[ns * nd:].sum())

    plan = [(src[i], dst[j], round(x[i, j])) for i in range(ns) for j in range(nd) if x[i, j] > 0.5]
    return {
        "Порожний пробег «как сейчас», вагоно-км": greedy_km,
        "Порожний пробег оптимум, вагоно-км": opt_km,
        "Экономия, вагоно-км": greedy_km - opt_km,
        "Экономия, тг": (greedy_km - opt_km) * cost_km,
        "Невыполненные заявки (как сейчас / оптимум)": (greedy_unmet, round(opt_unmet)),
        "План оптимума (откуда, куда, вагонов)": plan,
    }


def cis_exchange(cfg) -> dict:
    bal = {k: int(v) for k, v in cfg.CIS_WAGON_BALANCE.items()}
    surplus = [k for k, v in bal.items() if v > 0]
    deficit = [k for k, v in bal.items() if v < 0]
    horizon = float(cfg.CIS_HORIZON_DAYS)
    idle = float(cfg.CIS_IDLE_COST_KZT_WAGON_DAY) * horizon
    loss = float(cfg.CIS_DEFICIT_LOSS_KZT_WAGON_DAY) * horizon
    baseline = sum(bal[s] for s in surplus) * idle + sum(-bal[d] for d in deficit) * loss

    pairs = [(s, d) for s in surplus for d in deficit if (s, d) in cfg.CIS_TRANSFER_COST_KZT]
    if not pairs:
        return {"Затраты без обмена, тг": baseline, "Затраты с обменом, тг": baseline,
                "Экономия, тг": 0.0, "План передачи (из, в, вагонов)": []}
    # выгода от передачи одного вагона: не платим за простой + закрываем дефицит − стоимость передачи
    c = np.array([float(cfg.CIS_TRANSFER_COST_KZT[p]) - idle - loss for p in pairs])
    a_ub, b_ub = [], []
    for s in surplus:
        a_ub.append([1.0 if p[0] == s else 0.0 for p in pairs])
        b_ub.append(bal[s])
    for d in deficit:
        a_ub.append([1.0 if p[1] == d else 0.0 for p in pairs])
        b_ub.append(-bal[d])
    res = linprog(c, A_ub=np.array(a_ub), b_ub=np.array(b_ub), bounds=(0, None), method="highs")
    x = res.x
    after = baseline + float(c @ x)
    return {
        "Затраты без обмена, тг": baseline,
        "Затраты с обменом, тг": after,
        "Экономия, тг": baseline - after,
        "План передачи (из, в, вагонов)": [(p[0], p[1], round(v)) for p, v in zip(pairs, x) if v > 0.5],
    }
