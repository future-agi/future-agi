package server

import (
	"context"
	"errors"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCanonicalAdmissionPreservesBufferedOutput(t *testing.T) {
	for i := 0; i < 100; i++ {
		chunks, errs := make(chan models.StreamChunk, 1), make(chan error, 1)
		usage := models.MeasuredUsage(3, 5)
		chunks <- models.StreamChunk{ID: "billable-first", Usage: usage}
		terminal := models.ErrUpstreamProvider(502, "synthetic terminal failure")
		errs <- terminal
		close(errs)
		close(chunks)
		first, remainingErrors, err := awaitCanonicalStreamStart(context.Background(), chunks, errs)
		if err != nil || first == nil || first.ID != "billable-first" || first.Usage != usage {
			t.Fatalf("buffered output lost to terminal error: first=%+v err=%v", first, err)
		}
		if got := <-remainingErrors; !errors.Is(got, terminal) {
			t.Fatalf("terminal error lost after startup: %v", got)
		}
	}
}

func TestCanonicalAdmissionDeadlineKeeps504(t *testing.T) {
	for _, closed := range []bool{false, true} {
		ctx, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
		chunks := make(chan models.StreamChunk)
		if closed {
			close(chunks)
		}
		first, _, err := awaitCanonicalStreamStart(ctx, chunks, nil)
		cancel()
		var apiErr *models.APIError
		if first != nil || !errors.As(err, &apiErr) || apiErr.Status != http.StatusGatewayTimeout {
			t.Fatalf("closed=%t: first=%+v err=%v; want the admission deadline's 504", closed, first, err)
		}
	}
}

func TestCanonicalAdmissionUsageSurvivesCanceledReplay(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	in := make(chan models.StreamChunk, 1)
	// Split reports must mark which dimension was actually reported, just
	// as adapters do. Unflagged literals intentionally are not evidence.
	prompt, completion := 3, 5
	in <- models.StreamChunk{Usage: models.NewUsage(nil, &completion)}
	close(in)
	first := models.StreamChunk{Usage: models.NewUsage(&prompt, nil)}
	out, usage := teeStreamUsage(ctx, in, nil, first)
	for range out {
	}
	seen := <-usage
	if seen.usage == nil || seen.usage.PromptTokens != 3 || seen.usage.CompletionTokens != 5 {
		t.Fatalf("usage lost while replaying startup chunk on cancellation: %+v", seen.usage)
	}
}
