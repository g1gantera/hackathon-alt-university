"""Shared gross traction and regenerative energy accounting for forecast/live."""

import numpy as np


def cell_energy(train, section, x, speed, times, reverse=False):
    dx = np.diff(x)
    acceleration = np.divide(np.diff(speed**2), 2 * dx, out=np.zeros_like(dx), where=dx > 0)
    if train.davis_resistance is None:
        resistance = (
            train.mass_kg * 9.81 * train.rolling_coefficient
            + train.drag_n_per_mps2 * (speed[:-1] ** 2 + speed[1:] ** 2) / 2
        )
    else:
        c = train.davis_resistance
        resistance = (
            train.mass_kg
            * 9.81
            / 1000
            * (
                c.a
                + c.b * (speed[:-1] + speed[1:]) * 3.6 / 2
                + c.c * (speed[:-1] ** 2 + speed[1:] ** 2) * 3.6**2 / 2
            )
        )
    resistance += train.mass_kg * 9.81 * section.grade_permille / 1000 * (-1 if reverse else 1)
    force = train.mass_kg * acceleration + resistance
    gross = np.maximum(force, 0) * dx / train.traction_efficiency / 3_600_000
    recovered = (
        np.maximum(-force, 0)
        * dx
        * train.regenerative_efficiency
        * train.grid_receptivity
        / 3_600_000
    )
    if train.regenerative_power_w is not None:
        recovered = np.minimum(recovered, train.regenerative_power_w * np.diff(times) / 3_600_000)
    # No export credit beyond this movement's consumed traction; report only
    # accepted regenerative energy in the conservative net consumption metric.
    if not np.any(recovered):
        return gross, recovered
    consumed = 0.0
    for i in range(len(gross)):
        consumed += gross[i]
        recovered[i] = min(recovered[i], consumed)
        consumed -= recovered[i]
    return gross, recovered
