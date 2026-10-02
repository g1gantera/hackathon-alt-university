"""Графики: график движения поездов (время–км) и сравнение показателей сценариев."""
import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e6e5e1"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
CLOSURE = "#52514e"
RESTRICTION = "#c3c2b7"


def _style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=TEXT_2, labelsize=8)
    ax.grid(color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def string_diagram(details, cfg, path, hours=None):
    """
    Классический график исполненного движения: по X — время, по Y — км.
    Горизонтальные участки линий — стоянки; серые блоки — закрытия перегонов,
    светлые — ограничения скорости.
    """
    hours = float(hours or 48)
    names = list(details)
    types = list(cfg.TRAIN_TYPES)
    colors = {t: SERIES[i] if i < 3 else TEXT_2 for i, t in enumerate(types)}
    fig, axes = plt.subplots(len(names), 1, figsize=(14, 4.2 * len(names)), sharex=True, facecolor=SURFACE)
    axes = np.atleast_1d(axes)
    for ax, name in zip(axes, names):
        _style(ax)
        det = details[name]
        for seg in det["segments"]:
            lo, hi = sorted((seg.km_a, seg.km_b))
            for s, e, _ in seg.closures:
                if s < hours:
                    ax.add_patch(plt.Rectangle((s, lo), min(e, hours) - s, hi - lo,
                                               color=CLOSURE, alpha=0.22, linewidth=0, hatch="//"))
            for s, e, _, _ in seg.restrictions:
                if s < hours:
                    ax.add_patch(plt.Rectangle((s, lo), min(e, hours) - s, hi - lo,
                                               color=RESTRICTION, alpha=0.35, linewidth=0))
        for tr in det["trains"]:
            pts = [(t, k) for t, k in tr.trace if t <= hours]
            if len(pts) > 1:
                t, k = zip(*pts)
                ax.plot(t, k, color=colors[tr.type_name], linewidth=1.1, alpha=0.9)
        ax.set_yticks([float(s["km"]) for s in cfg.STATIONS])
        ax.set_yticklabels([s["name"] for s in cfg.STATIONS])
        ax.set_xlim(0, hours)
        ax.set_title(f"График движения — сценарий «{name}»", loc="left", color=TEXT, fontsize=11)
    axes[-1].set_xlabel("Время от начала, ч", color=TEXT_2)
    handles = [plt.Line2D([], [], color=colors[t], linewidth=2, label=t) for t in types]
    handles += [plt.Rectangle((0, 0), 1, 1, color=CLOSURE, alpha=0.22, hatch="//", label="закрытие перегона"),
                plt.Rectangle((0, 0), 1, 1, color=RESTRICTION, alpha=0.35, label="ограничение скорости")]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.86, 0.98), frameon=False,
               fontsize=9, labelcolor=TEXT_2)
    fig.tight_layout(rect=(0, 0, 0.85, 1))
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)


KPI_PLOT = [
    ("avg_line_delay_h", "Средняя задержка, ч"),
    ("unplanned_stops", "Остановки «на красный»"),
    ("energy_cost_kzt", "Затраты на энергию"),
    ("derailments", "Сходы с рельсов"),
    ("avg_port_wait_h", "Ожидание парома, ч/поезд"),
    ("total_cost_kzt", "Итого затраты"),
]


def _short(v, money):
    if money:
        return f"{v / 1e6:,.1f} млн".replace(",", " ")
    return f"{v:,.1f}".replace(",", " ")


def kpi_bars(df, path):
    """Малые графики по каждому показателю: среднее по прогонам ± разброс (стандартное отклонение)."""
    order = list(dict.fromkeys(df["scenario"]))
    colors = {s: SERIES[i] if i < 3 else TEXT_2 for i, s in enumerate(order)}
    fig, axes = plt.subplots(2, 3, figsize=(13, 6.5), facecolor=SURFACE)
    for ax, (col, title) in zip(axes.ravel(), KPI_PLOT):
        _style(ax)
        ax.grid(axis="x", visible=False)
        g = df.groupby("scenario")[col]
        mean, std = g.mean().reindex(order), g.std().reindex(order).fillna(0)
        x = np.arange(len(order))
        money = col.endswith("_kzt")
        lower = np.minimum(std.values, mean.values)          # разброс не уходит ниже нуля
        ax.bar(x, mean.values, width=0.6, color=[colors[s] for s in order],
               edgecolor=SURFACE, linewidth=2, yerr=[lower, std.values], ecolor=TEXT_2, capsize=3)
        for xi, v, sd in zip(x, mean.values, std.values):
            ax.annotate(_short(v, money), (xi, v + sd), textcoords="offset points",
                        xytext=(0, 4), ha="center", fontsize=8, color=TEXT)
        if money:
            ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda y, _: f"{y / 1e6:,.0f}".replace(",", " ")))
            title += ", млн тг"
        ax.set_ylim(0, max(1e-9, float((mean + std).max())) * 1.18)
        ax.set_xticks(x)
        ax.set_xticklabels(order, fontsize=8, color=TEXT_2, rotation=20 if len(order) > 3 else 0)
        ax.set_title(title, loc="left", fontsize=10, color=TEXT)
    fig.text(0.01, 0.005, "Столбик — среднее по прогонам Монте-Карло, отрезок — разброс (стандартное отклонение).",
             fontsize=8, color=TEXT_2)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=130, facecolor=SURFACE)
    plt.close(fig)
