import pytest

from app.schemas import Scenario, Section, Station, Track, Train


@pytest.fixture
def small():
    return Scenario(
        id="opposing",
        horizon_s=4000,
        evaluation_end_s=1000,
        stations=[
            Station(id=s, name=s, tracks=[Track(id="1", length_m=800), Track(id="2", length_m=800)])
            for s in "AB"
        ],
        sections=[
            Section(
                id="AB", station_a="A", station_b="B", length_m=1000, max_speed_mps=20, headway_s=10
            )
        ],
        trains=[
            Train(
                id="P1",
                kind="passenger",
                priority=3,
                route=["A", "B"],
                release_s=0,
                due_s=150,
                length_m=100,
                mass_kg=300000,
                max_speed_mps=20,
                min_dwell_s=20,
            ),
            Train(
                id="F1",
                kind="freight",
                priority=1,
                route=["B", "A"],
                release_s=0,
                due_s=180,
                length_m=500,
                mass_kg=1000000,
                max_speed_mps=15,
                min_dwell_s=20,
            ),
        ],
    )
