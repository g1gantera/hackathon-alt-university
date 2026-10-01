# Low-poly models on era

Open the existing dashboard, sign in, and choose **3D** above the map. **2D** restores the original map. Use the existing scenario, Start, Pause, speed, train selection, incident and replay controls. **Follow train**, **Whole train**, the mouse wheel, and the station search control the camera only.

The transferred model kit is byte-identical to `M_part` commit `5522f5cf3b631c41a6f886686e44928c65e70f3b`, `frontend/src/scene3d/assets.ts` (Git blob `7da14bf4b9b660ed61cf713ecdf6ee9cff15cfbe`). It supplies the locomotives, passenger coaches, freight wagons, station buildings, stops, signals, switch markers and decorative trees. Its teal/ivory/amber palette and shared low-poly geometry are retained.

The scene is a presentation-only iframe. It uses the existing dashboard's `state`, `focus`, `layers`, `map-ready`, `map-rendered` and `select-train` messages. It sends no API commands, calculates no routes, and owns no simulation clock. `static/app.js`, `static/map-overlay.js`, every original backend module and every original graph/map file remain unchanged. The default map remains 2D.

Geometry comes from an exact gzip copy of **era's own** `network.json`; the build records its SHA-256 in `static/scene3d/network-source.json`. Directed arcs and exact edge lengths determine vehicle placement. All supplied conventional railway tracks, including their sidings, yards and spurs, remain available as geometry. Synthetic gap connections and the graph's non-conventional rail categories are excluded consistently with era's existing allowed-track rules. Nothing from M_part's old six-station simulation is imported.

Train heads interpolate only between received observations; they never extrapolate past the latest chainage. Each body and each bogie follows its own place on the supplied route, so coaches turn progressively. Pause/arrival settles within 500 ms; resets, replay seeks and incompatible route changes discard the old visual interpolation. Coincident staged trains use one visible representative to avoid overlapping meshes; all services remain selectable through the existing table.

The models are stylized representations. Era does not supply surveyed building footprints, platform layouts or rolling-stock classes. Buildings are drawn beside the existing station graph anchors; the passenger/freight appearance is a cosmetic mass-based choice. The actual train length comes from `spec.length_m`, with a bounded number of displayed vehicles. Neither model dimensions nor visual class feed back into dispatch. Signals use the existing simulation's aspects and positions; they retain era's assumption that the block layout is simulated rather than surveyed.

Rails, ballast and sleepers use three instanced draws with allocation limits. Close detail is culled around the camera; national views use compact track lines and station dots. Cached model resources, a capped device pixel ratio, depth-buffer settings, stable sleeper spacing and demand-driven rendering limit GPU work. Per-snapshot authority buffers are disposed when replaced. Browser performance still depends on the device and scene.

The generated `static/scene3d` files are included so the normal Python run command needs no Node tooling. To rebuild after changing visual sources or `network.json`:

```bash
pnpm --dir scripts install --frozen-lockfile
pnpm --dir scripts run check:3d
pnpm --dir scripts run test:3d
pnpm --dir scripts run build:3d
```

The preservation tests pin the original era dispatch/map files to commit `d0a56951f87e96b82d4e60721b9f3be2e5707048`. Adapter tests compare sampling with backend-generated fixtures, including reverse arcs, bends and reroutes. The browser smoke in `scripts/scene3d-tests/browser-smoke.mjs` requires an explicitly isolated test server on port 8002 and writes screenshots under `.build/scene3d-browser`; it changes the scenario only in that disposable test instance.

Three.js is bundled under the MIT license in `static/scene3d/THREE-LICENSE.txt`. Railway geometry is the existing OpenStreetMap-derived project data; its attribution remains visible in the scene.
