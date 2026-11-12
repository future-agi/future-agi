package server

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func byokEvidence(t *testing.T) models.StreamChunk {
	t.Helper()
	var chunk models.StreamChunk
	if err := json.Unmarshal([]byte(`{"model":"owned/bridge","provider":"BYOK endpoint","usage":{"prompt_tokens":8,"completion_tokens":3,"cost":0.2}}`), &chunk); err != nil {
		t.Fatal(err)
	}
	return chunk
}

// Both channels are ready, as after an adapter consumes an early SSE error.
// Selecting the error first loses billing evidence; selecting its metadata as
// output commits a false success. Neither is an acceptable stream start.
func TestBYOKStreamStartKeepsFailureEvidence(t *testing.T) {
	for _, prefix := range []string{"identity", "usage", "role-and-tool", "error-finish"} {
		t.Run(prefix, func(t *testing.T) {
			chunks := make(chan models.StreamChunk, 3)
			evidence := byokEvidence(t)
			switch prefix {
			case "identity":
				chunks <- models.StreamChunk{Model: evidence.Model, UpstreamProvider: evidence.UpstreamProvider}
			case "usage":
				chunks <- models.StreamChunk{Usage: evidence.Usage}
			case "role-and-tool":
				var chunk models.StreamChunk
				if err := json.Unmarshal([]byte(`{"choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_pending","type":"function"}]}}]}`), &chunk); err != nil {
					t.Fatal(err)
				}
				chunks <- chunk
			case "error-finish":
				reason := "error"
				chunks <- models.StreamChunk{Choices: []models.StreamChoice{{FinishReason: &reason}}}
			}
			chunks <- evidence
			wantErr := errors.New("BYOK failed before output")
			errs := make(chan error, 1)
			errs <- wantErr
			close(errs)
			close(chunks)
			got, prefixes, err := waitForManagedStreamStart(context.Background(), chunks, errs, false)
			if !errors.Is(err, wantErr) || got == nil || len(prefixes) != 0 {
				t.Fatalf("false start or lost error: chunk=%+v prefixes=%v err=%v", got, prefixes, err)
			}
			if got.Model != evidence.Model || got.UpstreamProvider != evidence.UpstreamProvider || got.Usage == nil || got.Usage.PromptTokens != 8 || got.Usage.CompletionTokens != 3 || got.Usage.Cost == nil || *got.Usage.Cost != 0.2 {
				t.Fatalf("failed hop lost its identity/usage: %+v", got)
			}
			if len(errs) != 0 {
				t.Fatal("terminal error not consumed before fallback")
			}
		})
	}
}

func TestBYOKStreamStartCommitsOutputImmediately(t *testing.T) {
	for _, body := range []string{
		`{"choices":[{"delta":{"content":"answer"}}]}`,
		`{"choices":[{"delta":{"reasoning_content":"reason"}}]}`,
		`{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"lookup"}}]}}]}`,
		`{"choices":[{"delta":{},"finish_reason":"stop"}]}`,
	} {
		t.Run(body, func(t *testing.T) {
			for _, queuedError := range []bool{false, true} {
				chunks := make(chan models.StreamChunk, 2)
				chunks <- byokEvidence(t)
				var output models.StreamChunk
				if err := json.Unmarshal([]byte(body), &output); err != nil {
					t.Fatal(err)
				}
				chunks <- output
				errs := make(chan error, 1)
				if queuedError {
					errs <- errors.New("failure after output")
				}
				// Leave channels open: genuine output must not wait for EOF or
				// even the next chunk, regardless of credential type.
				ctx, cancel := context.WithTimeout(context.Background(), time.Second)
				got, prefixes, err := waitForManagedStreamStart(ctx, chunks, errs, false)
				cancel()
				if err != nil || got == nil || len(got.Choices) == 0 || len(prefixes) != 1 || got.Usage == nil || got.Usage.Cost == nil || *got.Usage.Cost != 0.2 {
					t.Fatalf("output not committed with replayable metadata: queuedError=%t chunk=%+v prefixes=%v err=%v", queuedError, got, prefixes, err)
				}
				if queuedError && len(errs) != 1 {
					t.Fatal("post-output error was consumed by the start gate")
				}
			}
		})
	}
}

func TestBYOKStreamStartMetadataEOFIsNotOutput(t *testing.T) {
	chunks := make(chan models.StreamChunk, 1)
	chunks <- byokEvidence(t)
	close(chunks)
	// An auxiliary error channel may remain open after chunks close.
	errs := make(chan error)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	got, _, err := waitForManagedStreamStart(ctx, chunks, errs, false)
	if err == nil || errors.Is(err, context.DeadlineExceeded) || got == nil || got.Usage == nil {
		t.Fatalf("metadata EOF must fail promptly with evidence: chunk=%+v err=%v", got, err)
	}
}
