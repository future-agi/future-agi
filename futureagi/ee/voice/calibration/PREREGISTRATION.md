# TH-2094 VQI calibration study — preregistration (v1, 2026-11-06)

Written before any clip was downloaded or scored. The executable form of every
rule below is `vqi_study.py` (constants at the top of the file); stage 1 copies
them into the frozen manifest, whose SHA-256 is recorded before stage 2 runs.

## Question

Can the shipped VQI pipeline (regular DNSMOS `sig_bak_ovr.onnx` OVRL, upstream
polynomial, mean over admitted 9.01 s windows with the pinned Silero detector)
be monotonically calibrated to human overall-quality MOS (1–5) so that the PRD
r1.1 §4.4 gates hold on held-out data? The product then shows
`VQI = 5 * (g(x) - 1) / 4` (`normalized_mos_0_5_v1`) only for admitted cohorts.

## Data (rights-cleared, public, not redistributed)

NISQA Corpus, Zenodo record 4728081 (`NISQA_Corpus.zip`, 15,945,499,507 bytes),
read with HTTP range requests; only these subsets are touched:

| Subset | Use | Rights (subset readme, quoted in the manifest) |
| --- | --- | --- |
| NISQA_TRAIN_LIVE (1,020 clips, ~5 votes) | calibration / selection / locked test | LibriVox public-domain source; "may be used for commercial and/or non-commerical research" |
| NISQA_VAL_LIVE (200 clips, ~5 votes) | locked test only | same grant |
| NISQA_TEST_LIVETALK (232 clips, 24 votes, German) | external locked test only | CC BY 4.0 |

Excluded on rights grounds: NISQA_TRAIN_SIM / VAL_SIM (AusTalk and unverified
noise sources), TEST_FOR and TEST_NSC (non-commercial), TEST_P501 (ITU terms).
Audio lives only in the ephemeral sandbox; only derived numbers are kept.

## Eligibility (decided per clip before any quality model runs)

1. Product `decode_audio` must accept the file (limits, format).
2. Product `prepare_vqi_windows` with the pinned Silero detector must admit at
   least one full 9.01 s window with >= 3 s detected speech and >= 80% speech
   coverage. Short clips are never padded, repeated or concatenated.
3. At least 5 independent ratings per clip (PRD minimum); fewer ->
   `insufficient_ratings`.

Every exclusion is counted by reason; coverage loss is reported, never hidden.

## Splits (seed 2094, grouped by speaker)

- English: all eligible NISQA_VAL_LIVE clips (speakers disjoint from
  TRAIN_LIVE by corpus design) are locked test. Eligible TRAIN_LIVE LibriVox
  readers are permuted with seed 2094; 35% of readers -> locked test, of the
  rest 2/3 -> calibration, 1/3 -> selection. No reader spans two splits.
- German: all eligible LIVETALK clips are an external locked test cohort.

## Fit and selection

- `g`: isotonic (PAVA) fit of MOS on x over the calibration split, sampled at
  K calibration-quantile knots (first/last knot = calibration min/max), knot
  MOS clipped to [1, 5], monotone PCHIP between knots, no extrapolation.
- K in {4, 6, 8, 10, 12}, chosen by the lowest selection-split RMSE (ties ->
  fewer knots). The locked test sets are evaluated once, after K is fixed.
- Support = calibration x range; test clips outside it are `out_of_domain`
  (excluded from accuracy, reported as coverage loss), exactly as in product.

## Gates (PRD r1.1 §4.4, unchanged)

Per held-out cohort: MOS RMSE <= 0.5; Spearman >= 0.70; speaker-group
bootstrap (2,000 resamples) 2.5th-percentile Spearman >= 0.60; pair ranking
accuracy >= 0.90; n held-out >= 50; |mean bias| <= 0.25; cohort RMSE <= 0.65.
A cohort is admitted to the artifact only if it passes all of them. If no
cohort passes, no artifact is written and VQI stays `not_validated`.

Cohort tags: `en/sip/provider_received_track`, `de/sip/provider_received_track`
(speech received after a real telephone/VoIP transmission, recorded at the
receiving end; the corpus' fixed-line, mobile and Skype paths are pooled under
the product's `sip` transport tag).

Pair ranking substitute (`material_difference_pair_concordance_v1`): all
held-out clip pairs with |ΔMOS| >= 1.0 and non-overlapping per-clip 95% CIs
(1.96·std/√votes); accuracy = share ordered like human MOS, prediction ties
wrong. The PRD's listener-confirmed same-source paired degradations do not
exist in this corpus, so that PRD gate is reported as **not met by available
data** regardless of this number.

## Known limitations (stated in advance)

- Natural read/conversational speech, not synthesized agent voices: the PRD's
  TTS-agent cohort is not covered by any cleared public MOS corpus found.
- English LIVE clips carry ~5 crowd votes (P.808 style), so labels are noisy.
- LIVE source utterances come from the DNS-Challenge LibriVox clips, the same
  pool DNSMOS training drew noisy/clean speech from; the recordings and
  ratings are new, but source-utterance overlap cannot be excluded.
- The Silero detector is version-pinned but not validated against adjudicated
  activity labels (PRD >= 95% precision / >= 90% recall gate remains open).

## Outcome (recorded 2026-11-07, after the single locked-test evaluation)

Stage 1 (manifest sha256 `7531ecf1…ecfbd`): 1,452 clips read; 595 eligible
(English 185 calibration / 90 selection / 199 locked test; German 121). Most
exclusions are clips with no full 9.01 s window (`insufficient_voice`: 824);
29 clips had fewer than 5 ratings. Stage 2 chose K = 12 knots (selection RMSE
0.597) and evaluated the locked tests once (`studies/nisqa_live_v1/stage2-report.json`):

| Held-out cohort | n (groups) | MOS RMSE | Spearman | lb95 | pair acc | bias | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| en/sip/provider_received_track | 194 (106) | 0.502 | 0.664 | 0.566 | 0.915 | +0.065 | **fails** RMSE, Spearman, lb95 |
| de/sip/provider_received_track | 95 (8) | 0.789 | 0.432 | 0.129 | 0.765 | −0.286 | **fails** all but n |

5 English and 26 German held-out clips fell outside the calibration support
(`out_of_domain`). Normalized-index RMSE: 0.627 (en), 0.986 (de).

No cohort passed, so no artifact was written and VQI stays `not_validated`.
Gates were not changed and nothing was refitted on the locked tests. The label
diagnostic (`label-noise-diagnostic.json`, descriptive only) estimates English
label reliability ≈ 0.81 (≈5 votes/clip; correlation ceiling ≈ 0.90) and German
≈ 0.97, so the shortfall is not explained by label noise alone: regular DNSMOS
OVRL tracks these recorded telephone/VoIP paths only moderately.
