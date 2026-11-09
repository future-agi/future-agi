package server

import (
	"context"
	"encoding/json"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func retryConsumesHold(rc *models.RequestContext) bool {
	attempts := rc.AttemptsSnapshot()
	for i := len(attempts) - 1; i >= 0; i-- {
		a := attempts[i]
		if a.Outcome == models.OutcomeSkipped {
			continue
		}
		inclusive := a.Pricing != nil && a.Pricing.ReservePrice != nil && a.Pricing.ReservePrice.Inclusive != nil
		return a.CredentialType == models.CredentialManaged && (inclusive || a.PromptTokens+a.CompletionTokens > 0 || (a.Usage != nil && a.Usage.Cost != nil && *a.Usage.Cost > 0))
	}
	return false
}

// waitForManagedStreamStart does not confuse HTTP acceptance or role-only
// metadata with model output. Keep reported usage even if an upstream fails
// before output: a real usage report is billable, an invented empty one is not.
// Startup is credential-independent: BYOK error evidence must not commit a
// success and suppress the remaining fallback chain. Pricing still uses each
// attempt's credential type, so preserving BYOK usage does not add a managed fee.
// Adapters enqueue their terminal error before closing chunks. Read chunks
// first so buffered output/usage cannot lose a select race to that error.
const maxStreamPrefixChunks = 128
const maxStreamPrefixBytes = 64 * 1024

func waitForManagedStreamStart(ctx context.Context, chunks <-chan models.StreamChunk, errs <-chan error, _ bool, admitted ...models.StreamChunk) (*models.StreamChunk, []models.StreamChunk, error) {
	var pending models.StreamChunk
	var prefixes []models.StreamChunk
	toolChunks, toolBytes := 0, 0
	metadataChunks, metadataBytes := 0, 0
	for {
		var chunk models.StreamChunk
		var ok bool
		if len(admitted) > 0 {
			// A caller may already have observed admission through the shared
			// startup gate. Inspect that chunk without a forwarding goroutine.
			chunk, ok = admitted[0], true
			admitted = admitted[1:]
		} else {
			select {
			case chunk, ok = <-chunks:
			case <-ctx.Done():
				return &pending, nil, ctx.Err()
			}
		}
		if !ok {
			select {
			case err := <-errs:
				if err != nil {
					return &pending, nil, err
				}
			default:
			}
			return &pending, nil, models.ErrUpstreamProvider(http.StatusBadGateway, "upstream stream closed before model output")
		}
		if chunk.Usage != nil {
			pending.Usage = models.MergeUsage(pending.Usage, chunk.Usage)
		}
		identity := models.GenerationFromResponse(&pending)
		identity.Observe(models.GenerationFromResponse(&chunk))
		pending.ID, pending.Model, pending.UpstreamProvider = identity.ID, identity.ServedModel, identity.Provider
		pending.GenerationConflict = identity.Conflict
		var output streamedOutput
		output.observe(chunk)
		finished, failed, hasTools, hasImages := false, false, false, false
		for _, choice := range chunk.Choices {
			hasTools = hasTools || len(choice.Delta.ToolCalls) > 0
			// Generated images are output, not best-effort role metadata.
			// In particular, an image larger than the metadata prefix budget
			// must start the stream, never be silently dropped from it.
			var images []json.RawMessage
			if len(choice.Delta.Images) != 0 && json.Unmarshal(choice.Delta.Images, &images) == nil && len(images) > 0 {
				hasImages = true
			}
			if choice.FinishReason != nil && *choice.FinishReason != "" {
				if *choice.FinishReason == "error" {
					failed = true
				} else {
					finished = true
				}
			}
		}
		// A completed empty answer is still a generation, not a pre-output
		// failure to retry. Metadata followed only by EOF/error is not.
		if output.chars == 0 && !hasImages && !finished {
			if failed {
				continue
			}
			encoded, err := json.Marshal(chunk)
			if hasTools {
				if err != nil || toolChunks >= maxStreamPrefixChunks || len(encoded) > maxStreamPrefixBytes-toolBytes {
					return &pending, nil, models.ErrUpstreamProvider(http.StatusBadGateway, "upstream tool prefix exceeds stream limit")
				}
				toolChunks++
				toolBytes += len(encoded)
			} else {
				// Best-effort metadata has its own bound: a long hidden
				// reasoning phase must not crowd out required tool identity.
				if err != nil || metadataChunks >= maxStreamPrefixChunks || len(encoded) > maxStreamPrefixBytes-metadataBytes {
					continue
				}
				metadataChunks++
				metadataBytes += len(encoded)
			}
			prefixes = append(prefixes, chunk)
			continue
		}
		chunk.Usage = pending.Usage
		chunk.GenerationConflict = pending.GenerationConflict
		if chunk.ID == "" {
			chunk.ID = pending.ID
		}
		if chunk.Model == "" {
			chunk.Model = pending.Model
		}
		if chunk.UpstreamProvider == "" {
			chunk.UpstreamProvider = pending.UpstreamProvider
		}
		return &chunk, prefixes, nil
	}
}
