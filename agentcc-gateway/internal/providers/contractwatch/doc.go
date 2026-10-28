// Package contractwatch notices when a provider ships a model the contract
// tests (internal/providers/contracttest) do not cover.
//
// It has four parts, all driven by cmd/model-watch:
//
//   - known_models.json, the committed registry: every model ID each
//     provider's list-models API returns gets exactly one status, "covered"
//     (it has contract fixtures), "alias" (a snapshot or alias of a covered
//     model) or "ignored" (with a reason). Ignore rules (globs) cover whole
//     classes such as fine-tunes and moderation models.
//   - Listers, one per provider, that call the provider's official
//     list-models API with the same credentials cmd/contract-recorder uses,
//     follow its pagination, and keep the capability metadata it returns.
//   - A diff of the listing against the registry: NEW models (listed, not in
//     the registry), RETIRED ones (covered or alias, no longer listed), and
//     STALE coverage (a covered entry without fixtures, or fixtures without a
//     covered entry; this part runs offline, in go test).
//   - A template selector that picks, for each NEW model, the covered model
//     whose cases the recorder should clone (contract-recorder -from/-as):
//     one of the same kind (chat, embeddings, images, speech, transcription,
//     rerank, OCR, completions), then by family, tier, version and
//     capability.
//
// See cmd/model-watch/README.md.
package contractwatch
