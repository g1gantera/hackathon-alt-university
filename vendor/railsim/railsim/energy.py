"""Тяговые расчёты: энергия на движение по перегону и на разгон после остановки."""

G = 9.81  # м/с²


def traction_kwh(cfg, mass_t: float, v_kmh: float, length_km: float, grade_permille: float) -> float:
    """
    Механическая работа силы тяги на перегоне при постоянной скорости, кВт·ч.
    w0 = A + B*v + C*v² (Н/кН) — основное удельное сопротивление;
    уклон i (‰) добавляет i Н/кН. На спуске работа тяги не может быть < 0.
    """
    w0 = cfg.RESISTANCE_A + cfg.RESISTANCE_B * v_kmh + cfg.RESISTANCE_C * v_kmh ** 2
    w = max(0.0, w0 + grade_permille)          # Н/кН
    force_n = w * mass_t * G                   # (Н/кН) * (т * 9.81 = кН) = Н
    return force_n * length_km * 1000.0 / 3.6e6


def stop_kwh(mass_t: float, v_kmh: float) -> float:
    """Кинетическая энергия, теряемая при остановке и набираемая заново при разгоне, кВт·ч."""
    v = v_kmh / 3.6
    return 0.5 * mass_t * 1000.0 * v ** 2 / 3.6e6


def to_supply(cfg, mech_kwh: float, electrified: bool, regen_share: float = 0.0):
    """
    Пересчёт механической энергии в потребление: электроэнергия из сети (кВт·ч)
    или дизельное топливо (л). Рекуперация работает только на электротяге.
    """
    if electrified:
        return mech_kwh * (1.0 - regen_share) / cfg.ELECTRIC_EFFICIENCY, 0.0
    return 0.0, mech_kwh / (cfg.DIESEL_EFFICIENCY * cfg.DIESEL_KWH_PER_L)
