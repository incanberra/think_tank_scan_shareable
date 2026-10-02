# Topic-classifier refinement - 2 October 2026

Work remains on `codex/jev-triage-test`. No live code, schedule, email delivery, main branch or GitHub deployment was changed.

## Implemented

- Added topic-specific examples distinguishing military strikes from economic tools of coercion, grid resilience from passing infrastructure mentions, strategic dual-use technology from generic technology coverage, and recurring supporting discussion from an isolated keyword. Jev still answers 14 separate questions independently of GLM tags/summaries.
- Added a conservative Atlantic Council boundary rule: remove the author biography and following recommendations only on that publisher, after a named person's institutional biography sufficiently late in a substantial body. The existing sample loses 3,450 non-body characters across four included articles. Raw evidence remains preserved in the earlier frozen snapshot. Other publishers and ordinary text are unaffected. Enrichment cache schema 7 forces old polluted extractions to refresh on the test checkout.
- Jev cannot silently add or remove existing GLM labels on a disagreement, including a high-confidence disagreement. Such decisions are explicitly flagged for checking. Partial bodies and failed classifications retain original tags.
- GLM checks only flagged topics and retains unreviewed agreement labels. A bounded repair request handles invalid schemas or fabricated/altered evidence quotes. All raw results, including invalid ones, are retained for diagnosis.
- GLM topic checks now request a strict schema keyed by the exact requested topic names, addressing omitted and renamed topic rows. Local validation still checks names, roles, completeness and verbatim evidence; a schema is not a substitute for validating interpretation.
- Added optional provider preferences for the pilot: prefer Together then SiliconFlow and ignore Wafer. This is opt-in and leaves ordinary scanner requests unchanged. Other providers can still serve as fallbacks. OpenRouter's [provider routing documentation](https://openrouter.ai/docs/guides/routing/provider-selection) and [structured-output documentation](https://openrouter.ai/docs/guides/features/structured-outputs) describe these controls; enforcement varies by endpoint.
- **Shadow remains the default:** even a successful evidence check records proposed changes in the audit and preserves original GLM report tags. The draft marks disputed labels as awaiting reader review. All fourteen categories have blank reader-label rows in a CSV, with inclusion/exclusion definitions and separate source evidence. No reader labels or accuracy scores are invented.

## Execution and cost

Replayed the same eleven included items from 30 September, without new source discovery, relevance scoring, or delivery-history writes. These timings are execution times, excluding development and source review.

| Test | Jev time | GLM check time | Script time including rendering | Reported API cost |
| --- | ---: | ---: | ---: | ---: |
| First refinement, fresh Jev and preferred GLM routing | 2.676s | 31.557s | 34.388s | $0.013009384 |
| Strict-schema correction, reusing all eleven Jev decisions | 0.018s cached | 61.034s | 61.227s | $0.00942339625 |
| Actual total for both refinement tests | | | 95.615s | **$0.02243278025** |

Fresh Jev classification alone cost $0.003475794. The second test's Jev time is a cache lookup, not fresh inference. Combining the measured fresh Jev stage with the final GLM stage gives an indicative cold execution of 63.710 seconds and $0.01289919025; that combination was not measured as a separate end-to-end cold run.

Forty-one actual API requests, including bounded repairs, were made across these tests. Served models were `typesafe/jev-1.13-20260917` and `z-ai/glm-5.3-flash`. Providers were TypeSafe, Together and, on one fallback, OpenInference. No Wafer requests were served. Most GLM requests completed in roughly 3-11 seconds; the OpenInference fallback took 43.902 seconds and accounts for much of the second test's 61-second check time. Provider preference improves control but does not establish a guaranteed latency.

The first refinement had three unresolved completeness/name checks after repair. The final schema test completed all eleven source-grounded checks, with four targeted repair requests. API responses succeeded; application validation necessitated those repairs. No length-exhausted response was recorded in these two tests.

## Effectiveness and limits

Compared with the previously adjudicated thirty-tag reference, refined Jev proposed 22 tags: all 22 were retained by that reference, and eight reference tags were missed. In the original pilot, 20 of 21 suggestions were retained and ten reference tags were missed. The previous coercion false addition was removed; grid infrastructure and the summit's mineral/coercion coverage improved. However, the energy event fell below the provisional material threshold, and sanctions/supporting dependencies were still missed. Exact article-set agreement was only 3/11, compared with 4/11 before: fixing selected problems did not make the overall classifier reliably complete.

These are regression checks against non-blind agent judgments, not measured reader accuracy. Examples were tuned on this same small sample, so the observed improvements must not be presented as independent benchmark performance. Important categories lack substantive positive examples in this sample. The fourteen-topic reader CSV is a starting review set; a broader independently labelled set is still needed.

The final GLM checks proposed 38 tags, covering all thirty reference tags but adding eight questionable tags. They continued to over-interpret economic coercion and infrastructure references. Verified quotations prove that text exists, not that the interpretation meets the topic's coverage threshold. Consequently, the final shadow draft retains the original 25 GLM tags, records both sets of proposed changes separately and flags ten items for reader review. All eleven articles required a model check in this sample, so conditional topic checking did not yet save article-level API calls.

The coloured-pill display works in HTML and PDF, with up to three pills and additional tags retained in the JSON audit. Original summaries, relevance tiers, dates, priority scores, URLs, inclusions, exclusions and held items are preserved.

## Next deployment gate

Keep this in shadow evaluation. Use independent reader labels to tune coverage thresholds and test a broader sample before permitting automatic topic changes. Do not run unconditional second reviews just because a model returns well-formed output. The live scanner remains unchanged until a deployment is requested and these quality issues are resolved.

Outputs in ignored `reports/jev-topic-refinement-2026-10-02/`: `shadow-draft.pdf`, `shadow-draft.html`, `shadow-analysis.json`, `refinement-assessment.json`, `reader_labels.csv`, cleaned `source_evidence.json`, both initial/final check results, and per-run token/cost/provider/latency manifests. The earlier source-reviewed design draft remains separately available in `reports/jev-topic-pilot/`.

Validation: 92 unit tests pass, including default shadow behaviour, failed/partial-evidence fallbacks, independent Jev inputs, scope boundaries, old-cache invalidation, model disagreement routing, strict topic keys, a bounded schema repair, provider/schema opt-in, original-tag preservation and PDF pill rendering. The shadow PDF was rendered and visually checked.
