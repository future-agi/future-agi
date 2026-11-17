package server

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// anthropicStreamEstimates enriches only the translator's copy, after the
// accounting tee. Do not feed synthetic counts into provider evidence: they
// must neither override measured zeros nor turn estimated billing into a
// provider-reported measurement. Snapshot the prompt estimate on the handler
// goroutine; no pooled RequestContext is read by this goroutine.
func anthropicStreamEstimates(ctx context.Context, in <-chan models.StreamChunk, prompt *models.Usage) <-chan models.StreamChunk {
	if prompt == nil {
		return in
	}
	out := make(chan models.StreamChunk)
	go func() {
		defer close(out)
		var reported *models.Usage
		var output streamedOutput
		first := true
		send := func(chunk models.StreamChunk) bool {
			select {
			case out <- chunk:
				return true
			case <-ctx.Done():
				return false
			}
		}
		for chunk := range in {
			reported = models.MergeUsage(reported, chunk.Usage)
			output.observe(chunk)
			if first {
				// The start has no content yet. Output is filled at EOF, not
				// estimated from the first chunk before its deltas are emitted.
				chunk.Usage = billing.CompleteUsage(chunk.Usage, prompt)
				first = false
			}
			if !send(chunk) {
				return
			}
		}
		if !first {
			estimate := models.MeasuredUsage(prompt.PromptTokens, (output.chars+3)/4)
			estimate.Estimated = true
			// Canonical EOF seals accounting, including any trailing reports.
			// The translator consumes this before its message_delta/message_stop.
			final := billing.CompleteUsage(reported, estimate)
			// Seal both wire counters, even when a cost-only report means chat
			// deliberately keeps them at zero. Otherwise the translator would
			// retain the initial prompt estimate as if it were measured usage.
			wire := models.MergeUsage(models.MeasuredUsage(final.PromptTokens, final.CompletionTokens), final)
			send(models.StreamChunk{Usage: wire})
		}
	}()
	return out
}
