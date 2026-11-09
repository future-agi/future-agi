package server

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Uses only the parent's APIs, so a baseline failure is behavioral, not a
// compiler error from a new field. The generation ID may occur only in a prefix.
func TestRouterPrefixRetainsGenerationID(t *testing.T) {
	for _, output := range []bool{false, true} {
		chunks := make(chan models.StreamChunk, 2)
		errs := make(chan error, 1)
		chunks <- models.StreamChunk{ID: "gen-prefix", Model: "test/served", UpstreamProvider: "Synthetic"}
		if output {
			text := "synthetic output"
			chunks <- models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}
		}
		close(chunks)
		close(errs)
		chunk, _, err := waitForManagedStreamStart(context.Background(), chunks, errs, true)
		if chunk == nil || chunk.ID != "gen-prefix" || chunk.Model != "test/served" || (output && err != nil) {
			t.Fatalf("output=%t chunk=%+v error=%v", output, chunk, err)
		}
	}
}
