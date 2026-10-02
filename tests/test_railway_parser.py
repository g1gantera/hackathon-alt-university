import unittest
from copy import deepcopy

from parser.railway_parser import ACTIVE_TYPES, build_query, to_geojson


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.way = {"type": "way", "id": 12, "nodes": [1, 2],
                    "tags": {"railway": "rail", "name": "Қазақстан", "gauge": "1520"},
                    "geometry": [{"lat": 48, "lon": 67}, {"lat": 49, "lon": 68}]}

    def test_excludes_stations_platforms_nodes_and_areas(self):
        elements = [self.way, deepcopy(self.way)]
        for kind in ("station", "platform", "halt"):
            element = deepcopy(self.way)
            element.update(id=len(elements), tags={"railway": kind})
            elements.append(element)
        elements.append({"type": "node", "id": 1, "tags": {"railway": "rail"}})
        area = deepcopy(self.way)
        area.update(id=99, tags={"railway": "rail", "area": "yes"})
        elements.append(area)
        features = to_geojson({"elements": elements}, ACTIVE_TYPES)["features"]
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["geometry"]["coordinates"], [[67, 48], [68, 49]])
        self.assertEqual(features[0]["properties"]["name"], "Қазақстан")
        self.assertEqual(features[0]["properties"]["osm_nodes"], [1, 2])

    def test_rejects_partial_or_invalid_results(self):
        for payload in ({"elements": [self.way], "remark": "timeout"},
                        {}, {"elements": []}):
            with self.assertRaises(ValueError):
                to_geojson(payload, ACTIVE_TYPES)
        for geometry in ([], [None, None], [{"lat": 95, "lon": 67}] * 2,
                         [{"lat": 48, "lon": float("nan")}] * 2):
            with self.assertRaises(ValueError):
                to_geojson({"elements": [{**self.way, "geometry": geometry}]}, ACTIVE_TYPES)

    def test_rail_only_excludes_trams(self):
        tram = {**self.way, "id": 13, "tags": {"railway": "tram"}}
        result = to_geojson({"elements": [self.way, tram]}, ("rail",))
        self.assertEqual(len(result["features"]), 1)

    def test_query_uses_country_and_only_ways(self):
        query = build_query(("rail",))
        self.assertIn('"ISO3166-1"="KZ"', query)
        self.assertIn('way(area.country)["railway"~"^(rail)$"]', query)
        self.assertIn("out body geom;", query)
        with self.assertRaises(ValueError):
            build_query(("station",))


if __name__ == "__main__":
    unittest.main()
