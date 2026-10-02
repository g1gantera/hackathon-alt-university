"""Authority-bounded integration shared with the era motion kernel (SI units)."""

import math


def integrate(position, speed, target, authority, acceleration, braking, dt):
    next_speed = (
        min(speed + acceleration * dt, target)
        if target >= speed
        else max(target, speed - braking * dt)
    )
    brake = braking * dt
    safe = max(
        0,
        math.sqrt(
            max(0, (brake / 2) ** 2 + 2 * braking * max(0, authority - position) - brake * speed)
        )
        - brake / 2,
    )
    next_speed = max(0, speed - brake, min(next_speed, safe))
    moving = min(dt, speed / braking) if next_speed == 0 and speed > 0 else dt
    delta = min((speed + next_speed) / 2 * moving, max(0, authority - position))
    return position + delta, next_speed
