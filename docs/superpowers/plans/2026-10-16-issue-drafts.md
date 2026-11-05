# Road to 1.0 — milestones and issue drafts (FILED 2026-10-16)

Everything in this document was filed on GitHub on 2026-10-16; it is kept only as the mapping record. The live plan detail is in `2026-10-16-road-to-1.0.md`.

## What was filed

**geoparquet-io** (geoparquet/geoparquet-io):
- #1307 refactor(core): embeddable core — lazy CLI export, function-body duckdb imports
- #1308 feat(convert): one-step filtered conversion (--bbox/--geometry/--where)
- #1309 feat(core): file-name extent decoding as a partition-pruning fallback
- #1310 feat(api): query-plan preview (row groups, est. rows/bytes)
- #1311 design: shared GeoParquet core tracker (condenses ~/Downloads/a-shared-geoparquet-core.md)

**qgis_plugin_gpq_downloader** (cholmes/qgis_plugin_gpq_downloader):
- Milestones: v0.9.x — Safety net · v0.10 — Re-architecture · v0.11 — 1.0 features · v1.0
- #194 characterization tests (v0.9.x)
- #195 re-architecture around the vendored gpio core (v0.10)
- #196 donate partition pruning upstream + relicensing record (v0.10)
- #197 download manager: queue, QgsTask, QgsNetworkAccessManager, real progress (v0.11)
- #198 1.0 release checklist (v1.0)
- #199 Road to 1.0 (pinned tracker)
- Milestoned: #111→v0.9.x; #1, #84, #81, #93, #3, #16, #80, #68→v0.11; #13, #70→v1.0
- Closed with comments: #56 (out of scope), #10 (shipped), #9 (→#84), #47 and #11 (→#197)
- Commented: #17 (ongoing perf tracker), #57 (deferred, coverage metadata), #162 (retest on v0.9.1)
