package server

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/translation"
)

// cacheUsageProvider is a canonical-path provider (not AnthropicNativeProvider,
// like Bedrock Claude) whose usage is the inclusive mapping of 100
// uncached, 100,000 cache-read and 300 cache-write input tokens.
type cacheUsageProvider struct{}

func cacheUsage() *models.Usage {
	u := &models.Usage{PromptTokens: 100400, CompletionTokens: 5, TotalTokens: 100405}
	u.PromptDetails.CachedTokens = 100000
	u.PromptDetails.CacheWriteTokens = 300
	u.MergeDetails()
	return u
}

func (cacheUsageProvider) ID() string { return "bedrock" }
func (cacheUsageProvider) ChatCompletion(context.Context, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	return &models.ChatCompletionResponse{ID: "msg_c", Model: "anthropic.claude",
		Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(`"ok"`)}, FinishReason: "stop"}},
		Usage:   cacheUsage()}, nil
}
func (cacheUsageProvider) StreamChatCompletion(context.Context, *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	stop, text := "stop", "ok"
	chunks := make(chan models.StreamChunk, 3)
	chunks <- models.StreamChunk{ID: "msg_s", Model: "anthropic.claude", Choices: []models.StreamChoice{{Delta: models.Delta{Role: "assistant", Content: &text}}}}
	chunks <- models.StreamChunk{ID: "msg_s", Choices: []models.StreamChoice{{FinishReason: &stop}}}
	chunks <- models.StreamChunk{ID: "msg_s", Usage: cacheUsage()}
	close(chunks)
	errs := make(chan error)
	close(errs)
	return chunks, errs
}
func (cacheUsageProvider) ListModels(context.Context) ([]models.ModelObject, error) { return nil, nil }
func (cacheUsageProvider) Close() error                                             { return nil }

func checkMessagesUsage(t *testing.T, where string, usage map[string]any) {
	t.Helper()
	for k, want := range map[string]float64{"input_tokens": 100, "cache_read_input_tokens": 100000,
		"cache_creation_input_tokens": 300, "output_tokens": 5} {
		if got, _ := usage[k].(float64); got != want {
			t.Errorf("%s: usage.%s = %v, want %v (usage %v)", where, k, usage[k], want, usage)
		}
	}
}

// TestMessagesCanonicalPathUsageDialect: /v1/messages through the canonical
// path (a Bedrock-Claude-like provider) hands an Anthropic-SDK client
// Anthropic's usage: input_tokens without the cache tokens, the cache reads
// and writes apart, non-stream and stream.
func TestMessagesCanonicalPathUsageDialect(t *testing.T) {
	translator, ok := translation.InboundFor("anthropic")
	if !ok {
		t.Fatal("anthropic translator not registered")
	}
	h := &Handlers{engine: pipeline.NewEngine()}

	t.Run("non-stream", func(t *testing.T) {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		w := httptest.NewRecorder()
		h.handleAnthropicNonStreamViaCanonical(context.Background(), w, rc, cacheUsageProvider{}, translator,
			&models.ChatCompletionRequest{Model: "anthropic.claude"})
		var msg struct {
			Usage map[string]any `json:"usage"`
		}
		if err := json.Unmarshal(w.Body.Bytes(), &msg); err != nil {
			t.Fatalf("status %d body %s: %v", w.Code, w.Body, err)
		}
		checkMessagesUsage(t, "non-stream", msg.Usage)
	})

	t.Run("stream", func(t *testing.T) {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		w := httptest.NewRecorder()
		h.handleAnthropicStreamViaCanonical(context.Background(), w, rc, cacheUsageProvider{}, translator,
			&models.ChatCompletionRequest{Model: "anthropic.claude", Stream: true})
		var delta map[string]any
		for _, frame := range strings.Split(w.Body.String(), "\n\n") {
			if !strings.HasPrefix(frame, "event: message_delta\n") {
				continue
			}
			data := strings.TrimPrefix(frame[strings.Index(frame, "\ndata: ")+1:], "data: ")
			if err := json.Unmarshal([]byte(data), &delta); err != nil {
				t.Fatalf("message_delta %q: %v", data, err)
			}
		}
		if delta == nil {
			t.Fatalf("no message_delta in %s", w.Body)
		}
		usage, _ := delta["usage"].(map[string]any)
		checkMessagesUsage(t, "stream message_delta", usage)
	})
}
