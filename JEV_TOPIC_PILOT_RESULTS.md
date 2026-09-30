# Jev topic-label pilot - 30 September 2026

Implemented on `codex/jev-triage-test`; the scheduled scanner, main branch and delivery history remain unchanged. No email was sent. This is a topic-label replay, not a new discovery or relevance run.

## Method

Used all 11 included articles/events/podcasts in the latest frozen 30-candidate editorial replay. Jev received source body evidence, not GLM tags, summaries or keyword hints. All bodies fitted within the 60,000-character packet limit. One independent Choice question per each of the existing 14 economic-security topics, with its existing inclusion/exclusion definition. The options distinguish central, substantive supporting, incidental, absent and insufficient coverage.

The provisional material threshold was 0.80 for central + supporting probability, with central/supporting the winning option and insufficient probability below 0.10. Settled absence required 0.90 incidental + absent probability. These are routing rules, not calibrated accuracy. Model response shape, distributions, answer IDs, evidence adequacy and cache identity are checked. Failed/partial decisions retain existing GLM labels; ambiguous existing tags are preserved in the initial draft.

Pills use a fixed colour registry, show at most three topics with a `+N more` indicator, and retain every tag and decision in JSON. The same registry drives HTML and rounded PDF pills. The live scanner does not call this topic module automatically.

## Measured results

| Stage | Wall time | Actual API requests | Reported USD cost |
| --- | ---: | ---: | ---: |
| Fresh Jev classification, no cache hits | 3.353 seconds | 11 | $0.003086076 |
| Additional GLM evidence checks, reusing Jev cache | 130.020 seconds | 14 including retries | $0.016222560 |
| Combined inference stages | 133.373 seconds | 25 | $0.019308636 |

Initial Jev run including report rendering: 3.411 seconds. GLM-check run including cached Jev lookup and rendering: 130.088 seconds. Combined measured script wall times: 133.499 seconds. Development, source adjudication and visual inspection are additional working time; the figures above are execution times, not total elapsed project work. An earlier sandbox-blocked network attempt returned no model answers and has no reported charge; it is retained separately in the audit.

Jev served `typesafe/jev-1.13-20260917`, generated 154 judgments, used 73,478 input and 9,463 output tokens, and had no API/schema failure or retry in the successful fresh run. It proposed 21 material tags and marked 20 of 154 judgments uncertain. Only 2 of 11 article topic sets exactly matched the original GLM sets (25 original tags). Disagreement with GLM does not itself establish error.

GLM served `z-ai/glm-5.3-flash` with low reasoning. Three requests routed through Wafer exhausted the 3,500-token output allowance, largely/all in reasoning, taking approximately 80.5, 80.7 and 112.0 seconds. Their reported cost was $0.008394750. Together/SiliconFlow completed other requests in approximately 4-11 seconds. Three automatic transport/schema retries recovered responses, but four article checks still failed topic completeness or grounded-quote validation. One further check produced no material tag under an overly narrow interpretation. The initial confirmation draft preserves original GLM tags in these cases. Raw invalid results will also be retained by the updated review adapter in future evaluations.

OpenRouter supports controlling provider selection with `order`, `ignore`, and `sort`: https://openrouter.ai/docs/guides/routing/provider-selection . These observations concern the routes served in this pilot, not a universal ranking of model providers. No production provider setting was changed.

## Source-based adjudication

Reviewed all 11 source bodies and the competing results. The reviewed draft has 30 tags: 20 of 21 raw Jev suggestions were retained; one was removed, and 10 additional substantive tags were identified. Raw Jev exactly matched the adjudicated topic set on 4 of 11 articles. These are provisional agent editorial judgments made after viewing model outputs, not a blind benchmark or reader-labelled accuracy.

Useful differences:

- Jev correctly added infrastructure vulnerability to Europe-Gulf trade/connectivity and emerging technology to the inexpensive-munitions analysis.
- The original GLM mineral tag in EU-China relations was only a brief example; removing it improves precision.
- Jev missed sanctions as substantive supporting coverage in the energy-truce article and weakly rated the central infrastructure subject of the grid-modernisation transcript.
- The long Trump-Xi transcript has several separate exchanges about rare-earth restrictions, alternative supplies and economic leverage; Jev under-tagged those supporting topics.
- Jev treated military attacks on energy infrastructure as economic coercion. The existing definition concerns economic tools; the reviewed draft retains energy, infrastructure and sanctions tags instead.
- Some Atlantic Council bodies still include related-article question cards after the author biography. They can inflate topic coverage; the source review disregarded those cards. Tightening source-body extraction should precede stronger reliance on automatic classification.
- GLM also made questionable interpretations: its focused check rejected both dual-use guidance/manufacturing and industrial-base vulnerabilities in the munitions article. Its source quotes established other newly proposed topics only weakly. A grounded quote establishes that words exist; it does not prove substantive coverage or correct interpretation.

The three-pill limit affects presentation only. The grid and Trump-Xi entries show `+2 more`; all five tags remain in the reviewed JSON. No included article, summary, date, priority or URL was changed by this topic experiment. An empty event publication-date field is displayed as unverified in the PDF.

## Recommendation

The coloured-pill design is ready for reader review. Jev is inexpensive and fast as an additional topic signal, but this pilot does not support replacing GLM or removing its tags solely on Jev's confidence. Keep the classifier in shadow testing. Compare Jev and GLM's already-generated tags; refer disagreements and uncertain labels to an evidence check, with original labels retained and explicitly flagged when the check fails. Avoid an unconditional second review of every article: this assessment deliberately checked all 11, which adds unnecessary routine latency.

Before deployment, tighten extraction around related-story cards; make the economic-tools/kinetic-attacks boundary and infrastructure scope explicit in topic examples; calibrate on a reader-labelled set covering all 14 topics; audit both false additions and missed supporting tags. The 11-article sample is too small and uneven across categories to claim a reliable precision/recall improvement. Provider controls and a bounded reasoning budget deserve a separate targeted GLM test following the demonstrated Wafer failures.

Artifacts under ignored `reports/jev-topic-pilot/`: original questions, raw Jev results, comparisons, GLM confirmations, individual run manifests with provider/token/cost/latency records, `analyst_review.json`, `assessment.json`, `reviewed-analysis.json`, and reviewed HTML/PDF. Source evidence remains in `reports/jev-pilot-latest-low/frozen_candidates.json`. Automated initial drafts are kept separate from the manually adjudicated reviewed draft.

Validation: 83 unit tests pass, including the existing scanner reliability checks, multi-topic distribution/schema validation, no leakage of GLM labels into Jev inputs, thin/partial-evidence fallbacks, cache invalidation, grounded confirmation, HTML escaping, pill wrapping and PDF text. Six-page reviewed PDF rendered and inspected; pills are readable and the full audit remains available.
