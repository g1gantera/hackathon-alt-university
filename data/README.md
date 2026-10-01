# Railway data

`kazakhstan_railways.geojson` is the versioned application dataset: 23,280
railway line features, without station features. It is included so a fresh
clone can display the map without requesting a new country-wide download.

Source: OpenStreetMap via Overpass API, snapshot `2026-10-01T06:50:05Z`.
Attribution: © OpenStreetMap contributors.
Data license: [Open Database License (ODbL)](https://www.openstreetmap.org/copyright).
Preserve attribution when displaying or redistributing this dataset.

These local files are deliberately excluded from Git:

- `overpass.json`: optional raw Overpass response, about 17 MB.
- `dispatch.sqlite` and its companion files: local simulator history.

To refresh the dataset and keep a local raw response, run from the project root:

```sh
python railway_parser.py --save-raw data/overpass.json
```

If the raw response already exists, use `--input data/overpass.json` to convert
offline. The raw file is not required to run the website or tests.

Derived demo infrastructure and the frozen initial timetable are versioned
under `scenarios/`. Rebuild them intentionally using the scripts described in
the main README; do not regenerate the baseline during ordinary startup.
