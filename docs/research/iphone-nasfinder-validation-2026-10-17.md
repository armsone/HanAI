# Actual iPhone NasFinder validation — 2026-10-17

Current Mac project source was used, without reverting to an earlier release. The hosted app reported NasFinder 2.12.0 build 202610170824 on iOS 27.0.1. The one selected device XCTest passed, with no skipped tests. This exercises production analysis functions inside the app; it is not a complete UI, Photos permission, or export replay.

## Photos

48 existing video-derived JPEGs were processed by the actual bundled CPU-only Core ML model and production NasFinder gate. All 48 produced finite 384-dimensional embeddings. The same previously frozen 38 labeled pairs, including 7 similar and 31 different pairs, were used without retuning the threshold.

| Fixed comparison | True similar accepted | Different pairs incorrectly accepted | Similar pairs missed |
| --- | ---: | ---: | ---: |
| Existing fingerprint predicate | 7 | 31 | 0 |
| Existing predicate AND DINO distance <= 0.06911703708590078 | 5 | 0 | 2 |

DINO distance ranking AP was 0.9097744361 and AUC 0.9447004608. Actual duration ratios increased for 34 photos and stayed equal for 14; none decreased. Input IDs and the existing allocation formula were retained.

This small retrospective cohort is biased: five of the seven similar pairs are empty scenes, and both missed similar pairs contain a person. These results do not establish general photo accuracy. Across the 38 pairs, iPhone versus Mac native cosine distances differed by at most 0.0186962310; the measured classification and AP outcomes stayed the same. Native cross-device byte or tensor equality is not claimed; the cause of that numerical difference was not isolated here.

The gate took 15.178 seconds for this 48-photo batch under hosted Debug XCTest. That includes model loading and is not a controlled release-speed or peak-memory benchmark. No peak-memory claim is made.

## Video representative frames

The same 32 uniformly selected cached clips used fixed center anchors. Both paths used a 240-pixel bound, preferred transform, and the same 64-pixel grayscale Laplacian variance proxy. The reference used the original default-tolerance generator parameters; the new path invoked the production representative-frame helper. Scores increased for 16 clips, stayed equal for 16, and decreased for none.

This proxy measures edge sharpness, not semantic importance or subjective frame quality. The important-scene expert, anchor, ranking, and export ranges were not replaced. The helper does not expose its selected actual presentation timestamp, so the test records new actual time as unknown; desktop timing diagnostics must not be attributed to this iPhone run.

## Failure handling and cleanup

A bad image and a mixed good/bad batch both returned whole-batch fallback, with the normal 48-photo model run independently confirmed available. Immediate pre-start cancellation raised CancellationError. That is not an in-flight cancellation latency measurement.

Only task-owned copies in a fixed app-cache namespace were transferred. After the result was recovered, the separate cleanup test passed with no skips and asserted that this namespace no longer exists. Its exact resolved-path guard rejects redirection to another cache folder. Original media and existing app data were not modified. Raw photos, videos, vectors, fingerprints, and private file paths are not included in this report.
