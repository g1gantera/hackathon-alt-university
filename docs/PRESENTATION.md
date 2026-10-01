# Rebuilding the presentation

The delivered deck is [Railflow-Hackathon-final.pptx](../artifacts/Railflow-Hackathon-final.pptx). The browser companion is [presentation.html](../artifacts/presentation.html), also served at `/presentation` by the application.

Rebuilding the deck is optional. **The application requires no Node build.** The presentation source, [scripts/build_presentation.mjs](../scripts/build_presentation.mjs), uses the bundled Codex presentation runtime with `@oai/artifact-tool` and the presentation skill's validators. These are authoring dependencies, separate from the Python application requirements.

Run from the repository root after setting the supplied runtime paths:

```bash
export RAIL_NODE=/absolute/path/to/runtime/node/bin/node
export RUNTIME_NODE_MODULES=/absolute/path/to/runtime/node/node_modules
export RUNTIME_PYTHON=/absolute/path/to/runtime/python/bin/python
export RAIL_PRESENTATION_SKILL=/absolute/path/to/presentations/skills/presentations
export RAIL_DECK_NAME=Railflow-Hackathon-revision-02.pptx

"$RAIL_NODE" scripts/build_presentation.mjs
```

`RAIL_NODE` selects the supplied Node executable. The script requires the three `RUNTIME_NODE_MODULES`, `RUNTIME_PYTHON`, and `RAIL_PRESENTATION_SKILL` environment variables. `RAIL_DECK_NAME` selects the output filename inside `artifacts/`; its default is `Railflow-Hackathon.pptx`. Keep it a filename, not a path.

The script reads these existing evidence files without changing them:

- `artifacts/backend-results.json`: scenario outcomes and incident benchmarks.
- `artifacts/browser-results.json`: browser measurements and reconnect/replay/export results.
- `artifacts/test-results.json`: the actual passing test count in the `passed` field.
- `artifacts/map-demo.png`: a focused application map screenshot with attribution.

Refresh the evidence through the verification procedures in [VERIFICATION.md](VERIFICATION.md) before presenting new results. Do not substitute estimated measurements or test counts.

The finalizer requires a **fresh deck filename and fresh receipt filename**. It refuses to overwrite either. The receipt name follows the deck automatically, for example `.build/deck/Railflow-Hackathon-revision-02.pptx.validation.json`. Choose a new `RAIL_DECK_NAME` for each revision rather than replacing a delivered deck.

A successful build writes the 12-slide PPTX, updates `artifacts/presentation.html`, and renders `.build/deck/slide-01.png` through `slide-12.png`. Inspect every rendered slide for readability, wrapping and overlap. The private JSON receipt records package, layout, font and import checks; those checks do not replace visual review or checking the evidence behind the slide claims.
