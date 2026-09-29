# Jev test branch: results and recommendation

30 September 2026. Branch: `codex/jev-triage-test`, based on `13be146`.
The scheduled scanner, live settings, delivered-item history and email delivery
were not changed. This is a local test implementation, not a deployment.

## Recommendation

The extraction fixes and clearer relevance policy are useful. Jev's API works,
is fast and inexpensive, but this pilot does **not** demonstrate enough reduction
in GLM work to recommend automatic production exclusions. Keep Jev off, or run
it in shadow mode after reviewing the proposed report selections. Test the optional
GLM low-reasoning setting further: it showed a more substantial speed benefit.
It remains opt-in, and model agreement is not a substitute for reader labels.

## Implemented

- Separate Jev Decisions API client using the existing OpenRouter key; four typed
  questions per article, conservative routing, bounded retries and concurrency.
- Off/shadow/active modes, validated distributions, error fallback to GLM,
  separate versioned cache, full served-model/usage/latency audits.
- Jev can read the complete stored body up to 60,000 characters. Truncated
  evidence, including bodies reaching the storage ceiling, cannot cause an
  automatic exclusion. GLM keeps the smaller existing evidence packet.
- A policy requiring a concrete economic-security mechanism, with main-brief
  and further-reading tiers in HTML, PDF, Markdown and report JSON.
- Literal body-quote and topic validation, one targeted repair of a defective
  candidate response, and an audit of failed validations. Topic hints no longer
  replace invalid positive topic labels.
- Public Lowy React Flight body extraction, removal of misleading structured
  descriptions as body substitutes, protection against related JSON-LD articles,
  and suppression of duplicated nested publisher paragraphs.
- Conservative article evidence floor of 600 characters, with the existing
  300-character floor for substantive event/podcast descriptions. These are
  screening rules, not guarantees that a full body was retrieved.
- Cancelled-event gate; preserved publication-date and delivered-history gates.
- An isolated replay tool and an optional audited GLM reasoning setting.

## Actual Jev measurements

Both tests used the same stratified sample of 100 distinct saved candidates,
drawn from recent completed scanner runs. Their original model outcomes are
comparison data, **not independently labelled relevance ground truth**.

| Measurement | Short packets | Full stored bodies |
| --- | ---: | ---: |
| Sampled candidates | 100 | 100 |
| Held locally for thin evidence | 8 | 8 |
| Successful Jev decisions | 92 | 92 |
| Failed Jev requests/fallbacks | 0 | 0 |
| Conservative automatic-exclusion proposals | 0 | 1 |
| Wall time for the Jev evaluation | 20.642 seconds | 24.017 seconds |
| Reported inference cost, USD | $0.011384 | $0.016930 |

Actual served model: `typesafe/jev-1.13-20260917`. The configured model family is
`typesafe/jev-1.13`; this does not guarantee an immutable served snapshot.

The single full-body exclusion was *The Discipline of Refusal: Constitutional
Concerns About Lawful Orders in the U.S. Military*. It also matched the old
scanner's exclusion. The pilot therefore avoided only one potential GLM review
out of 92 assessed articles. This falls well short of the proposed 25% efficiency
target. Confidence thresholds have not been calibrated against reader labels.
They were not relaxed to manufacture a better benchmark result.

## This morning's frozen 30-candidate replay

The complete Jev-plus-GLM cold test, with GLM's optional `low` reasoning setting,
took **155.882 seconds** and reported **US$0.023628**. That first build exposed
quote-formatting failures. Corrections and cached replays then produced the final
draft. The four paid completed replays together reported US$0.027681; the final
render-only replay made no requests. Cached replay timing is not a fresh-run speed
measurement. The earlier slow provider-default replay was stopped; its complete
time and cost were not captured and it is not used as a benchmark.

| Outcome | Original morning review | Corrected test draft |
| --- | ---: | ---: |
| Included, without editorial tiers | 15 | — |
| Main brief | — | 6 |
| Further reading | — | 5 |
| Excluded | 11 | 15 |
| Held for verification | 4 | 4 |
| Total candidates | 30 | 30 |

The test sends 28 eligible candidates to Jev: one cancelled event and one thin
article are blocked locally. Jev excludes one; GLM assesses the remaining 27.
Four original inclusions become exclusions under the tighter policy:

- *Three principles to guide European-Gulf defense cooperation*.
- *Don't ignore the “dead rats” of global disorder*.
- *Speed over judgement is winning the AI arms race*.
- *The Manhattan Project Mindset: How Nuclear Analogies Are Steering AI Policy Off Course*.

These are policy judgments about sustained economic mechanisms, not claims that
the articles lack value for other readers. Clear rare-earths, EU–China trade,
sanctions-enabler and energy analysis remains in the draft. The sanctions-enabler
article is classified as further reading; its priority is a useful reader-review
case because its sanctions sections may deserve main-brief treatment.

Three items remain held for unverified publication dates, including RAND's critical
minerals item. A Lowy migration teaser is held for missing substantive evidence.
Dates were not invented to increase the inclusion count. The cancelled CSIS event
is excluded rather than left in the verification queue.

The substantial speed difference should not be attributed to Jev. It skipped one
candidate. The revised policy, optional lower reasoning effort, provider routing
and changed evidence also differ from the original review. This pilot does not
separate all those effects or establish comparative summary accuracy.

## Defects found during testing

The Lowy AI article's visible HTML yielded about 415 characters; its public
embedded body yields about 4,600. With the body available, the model can establish
that it focuses on tactical military AI rather than infer relevance from a teaser.

Nested War on the Rocks paragraphs duplicated article text. A fresh fetch of the
Manhattan Project article changed from the saved 40,120-character extraction to
20,466 characters after suppressing repeated nested paragraphs. This reduces
evidence duplication; it does not establish a token-saving rate for all sources.

Valid quotations were initially held because of smart apostrophes, enclosing quote
marks and spaces inserted around punctuation by webpage links. Validation now
normalises these typography differences while rejecting word substitutions and
discontiguous paraphrases with inserted ellipses. The final draft retains the clear
sanctions article rather than losing it to a formatting mismatch.

## Checks and remaining limits

All **72 offline tests pass**. Checks cover typed response validation, invalid
probabilities, authentication fallback, shadow routing, conflicting/partial
evidence, cache invalidation, selective repairs, quote grounding, dates, delivered
history, cancelled events and extraction defects. The six-page PDF was checked
for both editorial sections, 11 unique included URLs and reconciliation of all
30 candidates. No test email was sent.

The original evidence was replayed; this was not a fresh scan of every source.
Existing source-discovery gaps remain. No reader-labelled holdout accuracy,
90% useful-item rate, or reliable reduction in missed relevant articles has been
established. Jev cannot repair source coverage or prove publication dates.

Next steps: review the eleven draft inclusions and four removed items; label a
representative held-out set; compare improved GLM alone with Jev plus that same
GLM policy; then observe five shadow runs before considering live exclusions.
Do not change thresholds merely to increase the number of filtered articles.

## Reproduction and outputs

Use the commands in README. All mutable replay state is restricted to the test
checkout's ignored `reports` directory. The resulting draft is:
`reports/jev-pilot-latest-low/draft.pdf` (also `draft.html`). Final decisions and
full triage distributions are in the same directory. The standalone full-body
benchmark is in `reports/jev-pilot-full-100`.

The API contract and optional reasoning setting were checked against
[OpenRouter's Decisions API](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request)
and [reasoning documentation](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).
Interpret confidence using [TypeSafe's explanation](https://docs.typesafe.ai/confidence),
not as a measured probability that a local relevance decision is correct.
