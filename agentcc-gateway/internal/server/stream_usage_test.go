package server

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The translator returns on ctx.Done without draining its input, so on client
// disconnect or a stream error nobody reads the tee's output again. Forwarding
// would park there forever while finalize waits on the usage channel, leaking
// the handler goroutine, the tee and the provider stream.
func TestTeeStreamUsage_ConsumerAbandonsStream(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	in := make(chan models.StreamChunk)
	out, usageCh := teeStreamUsage(ctx, in)

	go func() {
		defer close(in)
		for i := 0; i < 8; i++ {
			in <- models.StreamChunk{Model: "claude-3"}
		}
		in <- models.StreamChunk{
			Model: "claude-3",
			Usage: &models.Usage{PromptTokens: 11, CompletionTokens: 23},
		}
	}()

	<-out // read one, then abandon the stream exactly as the translator does
	cancel()

	rc := &models.RequestContext{Metadata: map[string]string{}, Model: "claude-3"}
	done := make(chan struct{})
	go func() {
		applyCanonicalStreamUsage(rc, usageCh)
		close(done)
	}()

	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("deadlock: finalize blocked on the usage channel while the tee was " +
			"parked forwarding to a consumer that had gone away")
	}

	// The counts still have to arrive — the tokens were spent.
	if rc.Response == nil || rc.Response.Usage == nil {
		t.Fatal("usage lost when the consumer abandoned the stream")
	}
	if got := rc.Response.Usage.PromptTokens; got != 11 {
		t.Errorf("PromptTokens = %d, want 11", got)
	}
	if got := rc.Response.Usage.CompletionTokens; got != 23 {
		t.Errorf("CompletionTokens = %d, want 23", got)
	}
}

func TestTeeStreamUsage_ForwardsEveryChunk(t *testing.T) {
	in := make(chan models.StreamChunk)
	out, usageCh := teeStreamUsage(context.Background(), in)

	go func() {
		defer close(in)
		for i := 0; i < 3; i++ {
			in <- models.StreamChunk{Model: "m"}
		}
		in <- models.StreamChunk{Model: "resolved-m", Usage: &models.Usage{PromptTokens: 2, CompletionTokens: 4}}
	}()

	seen := 0
	for range out {
		seen++
	}
	if seen != 4 {
		t.Errorf("forwarded %d chunks, want 4", seen)
	}

	rc := &models.RequestContext{Metadata: map[string]string{}, Model: "m"}
	applyCanonicalStreamUsage(rc, usageCh)

	if rc.ResolvedModel != "resolved-m" {
		t.Errorf("ResolvedModel = %q, want %q", rc.ResolvedModel, "resolved-m")
	}
	if rc.Response == nil || rc.Response.Usage.TotalTokens != 6 {
		t.Errorf("usage not carried through: %+v", rc.Response)
	}
}

// A provider that sends no usage must leave rc.Response nil, so the cost plugin
// skips the request rather than pricing it at zero.
func TestTeeStreamUsage_NoUsageLeavesResponseNil(t *testing.T) {
	in := make(chan models.StreamChunk)
	out, usageCh := teeStreamUsage(context.Background(), in)

	go func() {
		defer close(in)
		in <- models.StreamChunk{Model: "m"}
	}()
	for range out {
	}

	rc := &models.RequestContext{Metadata: map[string]string{}, Model: "m"}
	applyCanonicalStreamUsage(rc, usageCh)

	if rc.Response != nil {
		t.Errorf("rc.Response = %+v, want nil so cost skips rather than billing zero", rc.Response)
	}
	if _, ok := rc.Metadata["input_tokens"]; ok {
		t.Error("input_tokens recorded for a stream that reported none")
	}
}
