"""Small, synthetic, reproducible railway network. No commercial data."""

from app.schemas import Scenario, Section, SpeedLimit, Station, Track, Train


def demo_scenario() -> Scenario:
    stations = [
        Station(
            id=s,
            name=f"Станция {s}",
            tracks=[Track(id="1", length_m=1200), Track(id="2", length_m=1200)],
        )
        for s in "ABCDEF"
    ]
    sections = [
        Section(
            id=a + b,
            station_a=a,
            station_b=b,
            length_m=length,
            max_speed_mps=25,
            speed_limits=[SpeedLimit(start_m=1000, end_m=1400, speed_mps=15)] if a == "C" else [],
            shared_resources=["junction:C"] if a in ("B", "C") else [],
        )
        for a, b, length in zip("ABCDE", "BCDEF", (2500, 3000, 3500, 2800, 2400))
    ]
    trains = []
    releases = [0, 60, 240, 360, 660, 780, 1020, 1140]
    for i, release in enumerate(releases):
        passenger = i % 3 == 0
        route = list("ABCDEF" if i % 2 == 0 else "FEDCBA")
        trains.append(
            Train(
                id=f"{'P' if passenger else 'F'}{i + 1}",
                kind="passenger" if passenger else "freight",
                priority=3 if passenger else 1,
                route=route,
                release_s=release,
                due_s=release + (1250 if passenger else 1600),
                min_dwell_s=30,
                length_m=200 if passenger else 600,
                mass_kg=400_000 if passenger else 2_000_000,
                max_speed_mps=25 if passenger else 20,
                acceleration_mps2=0.5 if passenger else 0.25,
                braking_mps2=0.6 if passenger else 0.35,
                auxiliary_power_w=20_000 if passenger else 10_000,
            )
        )
    return Scenario(
        id="six-stations-eight-trains",
        horizon_s=14400,
        evaluation_end_s=3600,
        stations=stations,
        sections=sections,
        trains=trains,
    )
