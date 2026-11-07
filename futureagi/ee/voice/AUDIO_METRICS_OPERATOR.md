# Call audio metrics installation

These EE metrics analyze only a provenance-proven tested-agent track. Production
must remain **disabled by default**. Installing a model is not validation of the
Voice Quality Index (VQI); numeric release also requires the approved calibration
study, artifact and matching cohort. Never deploy test-fixture calibration values.

## Install the pinned dependencies and model

Install the locked backend dependencies including the `audio` and `ml` extras
(the EE installation uses all extras). Production requires `silero-vad==6.2.3`
and its bundled model, plus ONNX Runtime; the all-extras lock supplies both.
There is no automatic model download or alternate-detector fallback.

After reviewing the upstream terms in [third-party notices](../THIRD-PARTY-NOTICES.md),
run these commands in the deployment provisioning environment, not at worker
startup:

```sh
set -eu
MODEL_DIR=/opt/futureagi/audio-metrics
install -d -m 0755 "$MODEL_DIR"
curl --fail --location --proto '=https' --tlsv1.2 \
  https://raw.githubusercontent.com/microsoft/DNS-Challenge/591184a9fcb2cbdec02520fed81a32bbbf9d73ff/DNSMOS/DNSMOS/sig_bak_ovr.onnx \
  -o "$MODEL_DIR/sig_bak_ovr.onnx"
printf '%s  %s\n' \
  269fbebdb513aa23cddfbb593542ecc540284a91849ac50516870e1ac78f6edd \
  "$MODEL_DIR/sig_bak_ovr.onnx" | sha256sum --check --strict -
test "$(wc -c < "$MODEL_DIR/sig_bak_ovr.onnx" | tr -d ' ')" = 1157965
chmod 0444 "$MODEL_DIR/sig_bak_ovr.onnx"
```

Mount the verified file read-only at the same path on **both the scheduler
(`QUEUE_S`) and audio workers**. Set these on both process types:

```sh
VOICE_AUDIO_METRICS_DNSMOS_MODEL_PATH=/opt/futureagi/audio-metrics/sig_bak_ovr.onnx
VOICE_AUDIO_METRICS_DNSMOS_MODEL_SHA256=269fbebdb513aa23cddfbb593542ecc540284a91849ac50516870e1ac78f6edd
```

Install only a separately approved, **release-qualified** calibration JSON
read-only, verify its published exact-byte SHA-256, and set
`VOICE_AUDIO_METRICS_CALIBRATION_PATH` and `VOICE_AUDIO_METRICS_CALIBRATION_SHA256`
identically on **both** process types. This document supplies no substitute
calibration. The artifact must match the pinned DNSMOS digest,
`silero-vad:6.2.3:threshold0.5`, polynomial, resampling, window policy and scale
versions, and pass all statistical and cohort gates.

The loader also requires a `release_qualifications` object with all three literal
JSON booleans set to `true`, each backed by a non-empty evidence-reference string:

- `detector_activity_validated` and `detector_activity_evidence_ref`: adjudicated
  detector activity precision/recall validation.
- `target_domain_validated` and `target_domain_evidence_ref`: qualification on
  synthesized tested-agent voice in the admitted capture/transport cohorts.
- `paired_degradations_validated` and `paired_degradations_evidence_ref`:
  listener-confirmed, same-source paired degradation validation.

Missing, false or malformed qualifications/evidence return `not_validated`, even
when numerical gates pass. Evidence references must identify the reviewed study
records; setting flags or inventing references is not a substitute for validation.

The public-data study writes only `candidate-artifact.json` when cohort statistics
pass, with all three qualifications false and no release evidence. **Do not install
this research candidate**: the production loader deliberately refuses it.
`report.json` separates `statistical_gates_passed` from `release_qualified` (always
false for this study), and is saved before candidate output. Cohorts are admitted
independently for statistical reporting only; candidate-level gates take the worst
values across those cohorts, not a failing or excluded English cohort. A cohort
with no in-support predictions has zero coverage and fails its statistical gates.
The frozen preregistration and completed study evidence are not changed by these
release checks. Restart workers after configuration changes and retain the
approved artifact, manifest and validation evidence for audit.

Only regular `DNSMOS/DNSMOS/sig_bak_ovr.onnx` is supported. **pDNSMOS** (personalized
coefficients), **model_v8.onnx** (P.808 features/score) and **NISQA** weights are not
interchangeable. Do not change a digest merely to admit a different model.

## Controlled rollout

- `VOICE_AUDIO_METRICS_ENABLED=false` is the default and the rollback switch.
  Enable only after study/release approval, consistently on API, scheduler and
  audio workers. There is no independent VQI enable flag.
- `VOICE_AUDIO_METRICS_ORG_ALLOWLIST` is a comma-separated list of organization
  IDs. Start with explicit approved IDs. Empty means all **entitled** organizations
  when enabled, not unrestricted access; `has_voice_sim` entitlement is always
  required, and entitlement errors deny access.
- Use new qualified calls to verify rollout. Missing model/calibration paths at
  scheduling terminalize VQI; installing files does not upgrade that entry or
  automatically backfill historical calls.
- Concurrency is bounded by `VOICE_AUDIO_METRICS_WORKER_MAX_CONCURRENT_ACTIVITIES`
  (default 2) **per audio worker**. There is no per-tenant slot lock or
  `VOICE_AUDIO_METRICS_MAX_CONCURRENT_PER_ORG` setting. Size the audio-worker pool
  and observe queue latency; do not claim tenant-level concurrency isolation.

## Cohorts and troubleshooting

Cohort tags have the exact form `language/transport/capture_origin`, for example
`en/sip/provider_received_track`. Language is declared agent configuration, **not
speech-language detection**: the persisted call/execution/run version snapshot
wins over mutable agent definitions. Exactly one distinct lowercase two-letter
code is required; missing, malformed, conflicting or multilingual configuration
becomes `und`. `und` is not a wildcard. This metadata is internal provenance,
never part of the public source summary.

- Inbound/system-account Vapi customer track: `provider_received_track` (“Received
  over the call”). Outbound/client-account Vapi assistant track:
  `provider_source_track` (“Agent source audio”, source TTS before transport).
  Source TTS has no numeric VQI unless a calibration study explicitly admits its
  exact cohort. Unsupported capture paths stay `unknown`.
- `model_unavailable`: verify model path, read access and exact digest on both
  process types, ONNX Runtime, and the pinned Silero package/bundled model.
- `not_validated`: missing/unreadable calibration, digest/version mismatch or
  failed calibration gates. Look for `audio_metrics.calibration_refused` (one
  sanitized warning per process, exception class only). Never weaken the gates.
- `out_of_domain`: compare the exact tag against the artifact's cohorts, then
  check score support. `und/sip/provider_received_track`, unvalidated languages,
  `en/sip/provider_source_track`, `unknown` origin and out-of-support scores are
  not silently mapped to the English received-audio cohort or clamped.
- `insufficient_voice` / `insufficient_coverage`: at least one original 9.01 s
  window must contain 3 s speech, with at least 80% total detected-speech coverage.
  Do not repeat short clips, splice turns, remove pauses or downmix mixed speakers.

DNSMOS reference code is Microsoft MIT-licensed; repository content terms are
CC BY 4.0 and the weights are operator-supplied, not redistributed. Silero VAD
6.2.3 is MIT-licensed and distributed with bundled models. Preserve the licenses
and citations in [third-party notices](../THIRD-PARTY-NOTICES.md).
