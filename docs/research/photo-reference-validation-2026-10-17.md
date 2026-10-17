# Reference-image search and ground-truth correction

Verified on 2026-10-17, Asia/Seoul. This update adds **review-only reference-image search to the portable media tools**. It does not change native apps' automatic grouping, photo duration allocation, image selection, or video importance experts. Model weights and the native Swift/JVM SDK remain unchanged.

## Correction to the earlier two missed pairs

Full-resolution re-review found that the previous truth file contradicted its own rule. Pair 0–24 has different visible clothing/appearance despite a similar golf address pose. Pair 31–34 has a foreground person versus an empty bay. Neither is a valid near-duplicate positive under the recorded subject-preservation criterion.

The original labels and measured outputs remain preserved. A separately versioned correction changes only those two labels. Recounting the existing actual iPhone results yields 5 true similar pairs accepted, 0 different pairs accepted, 33 different pairs rejected, and 0 labeled similar pairs missed. **This is a label correction, not an improvement in model recall.** It is TM visual review, not independent human consensus or a new holdout. See the correction in [the original device report](iphone-nasfinder-validation-2026-10-17.md).

Related-image ranking and automatic duplicate grouping have separate purposes. On the recorded native vectors, 0→24 ranks second among 47 other images; 34→31 ranks ninth, while the reverse 31→34 ranks 34th. These are directional retrieval results, not evidence of the same person or a duplicate. They are not claimed to be a fresh execution of the Python decoder on iPhone.

## Shipped behavior

- `images --reference-image` ranks successful candidates by reference distance, then original input index. The reference uses the selected candidate backend and namespace.
- `--reference-limit` limits the returned candidates independently of selection count. Results explicitly carry `reviewOnly: true`.
- Optional `--verify-reference-copies` returns numerical pixel-agreement evidence, including local/global structure and the maximum channel difference. It does **not** output a copy verdict or authorize merging/deleting a photo. Color is not ICC-calibrated.
- Invalid references fail explicitly. Candidate failures preserve original indices; unavailable pixel evidence is reported as unknown. There are no implicit model downloads or transmissions of media.
- Existing selection inputs/results and the native automatic gates remain unchanged. The new functionality is in the portable analysis tool; this report does not claim a new native app search screen.

## Diverse-source comparison

Eleven public source images cover people, animals, food, text, textures, a clock, and astronomy. They were obtained with explicit public-domain/CC0 provenance and verified against the pinned upstream hashes. Six source groups were allocated to calibration and five to evaluation before scoring. JPEG recompression, resizing, gamma adjustment, and horizontal flipping produced four queries per source; derivatives stayed with their source group.

| Reference ranking on five evaluation source groups | Correct source ranked first | Correct source within first three |
| --- | ---: | ---: |
| Existing basic descriptor used as the retrieval baseline | 18/20 | 20/20 |
| DINO reference retrieval | 20/20 | 20/20 |

The gallery contains only five source images in this evaluation. The 20 transformed queries are **not 20 independently captured photographs**. The result demonstrates this controlled retrieval improvement; it is not a general accuracy estimate for a large personal library.

## Rejected automatic rules and further stress checks

A frozen structural/pixel rule recovered a recompressed clock image missed by the DINO threshold: on the first evaluation split, the experimental union changed 19/20 accepted copies to 20/20, with zero accepted negatives among ten different-source pairs. However, the same structural rule accepted 5/12 deliberately localized edits. **That union was rejected for automatic use.** The clock result must not be advertised as a deployed automatic-recall improvement.

A maximum-channel-residual measurement was added because an average can hide small changes. Its experimental cutoff was selected using development data, then frozen before examining a fresh source set. Of three fresh licensed source images, the silhouette and phase-microscopy image were scored; the four-channel 16-bit plant TIFF was unsupported and was excluded as unknown. These images do not represent ordinary natural photographs.

| Fresh scored set: 8 controlled copies and 13 negatives | True copies accepted | Negatives accepted | Copies missed |
| --- | ---: | ---: | ---: |
| Structural/pixel rule alone | 5 | 2 | 3 |
| Same rule with maximum-residual guard | 5 | 0 | 3 |
| DINO threshold alone, diagnostic only | 8 | 9 | 0 |

Combining the last two rules by union still accepted nine negatives, so it was also rejected for automatic use. The shipped tool exposes measurements for review, not these experimental classifiers. The DINO-only row is not a replay of the native app's complete fingerprint-and-DINO gate. Neither the nine diagnostic false positives nor the zero guarded false positives should be attributed to the whole app.

The first evaluation set became development evidence when the localized-edit failure was discovered. The fresh source set was not used to retune its cutoff. One exact-data replay was necessary to correct a label-string accounting bug; it was not a new independent experiment. Original records are retained alongside corrections.

## Video and compatibility checks

Eight fixed timestamps were sampled from one separately sourced Big Buck Bunny animation. Each same-frame recompressed reference returned its own frame first in the actual CLI: **8/8**. TM reviewed the eight frames and confirmed materially different content/poses for the 28 cross-frame pairs. The eight frames are not eight independent videos, and this is not a video-event importance benchmark. Existing golf-derived device evidence remains a separate cohort.

The actual existing JVM selection engine was invoked in three modes: without a reference, with reference ranking, and with pixel evidence. All exited successfully and returned identical existing candidate fields and selection results. Targeted argument, ordering, failure, privacy, and optional-evidence checks also passed. This is local Mac CPU evidence; a new Windows, Android, or iPhone execution of this portable tool is not claimed.

## Public sources and reproducibility

- [Pinned scikit-image image provenance and individual permissions](https://github.com/scikit-image/scikit-image/blob/v0.25.2/skimage/data/_fetchers.py), [pinned upstream hash registry](https://github.com/scikit-image/scikit-image/blob/v0.25.2/skimage/data/_registry.py).
- [DINOv2 author documentation](https://github.com/facebookresearch/dinov2) and [author retrieval/matching demonstrations](https://dinov2.metademolab.com/). Retrieval features are not a guarantee of duplicate identity.
- [Big Buck Bunny official permission and credit](https://peach.blender.org/about/), [official source archive](https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_320x180.mp4.zip). Credit: (c) 2008 Blender Foundation / www.bigbuckbunny.org, CC BY 3.0; logo/trademark exceptions apply.

Task-local records retain source hashes, fixed splits, transformation labels, model namespace, measured rows, CLI results, and corrections. Private originals, vectors, and filesystem paths are not included in this public report. Public test code uses temporary mock inputs; acquired media and private evaluation artifacts are not shipped in the release archive.
