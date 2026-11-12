package anthropic_test

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// An Anthropic model on the canonical path (served by an OpenAI-shaped
// upstream such as OpenRouter, or mapped by a provider adapter) reaches
// /v1/messages with OpenAI-convention usage: prompt_tokens include the cache
// reads (prompt_tokens_details.cached_tokens) and writes (cache_write_tokens).
// The Anthropic dialect reports input_tokens without them, and the reads and
// writes apart.

// rawOnlyUsage is usage as decoded from an upstream's JSON: the token
// details are raw, the parsed sidecars empty.
func rawOnlyUsage() *models.Usage {
	raw := json.RawMessage(`{"cached_tokens":100000,"cache_write_tokens":300,"audio_tokens":0}`)
	return &models.Usage{PromptTokens: 100400, CompletionTokens: 5, TotalTokens: 100405, PromptTokensDetails: &raw}
}

// sidecarUsage is usage as a provider adapter builds it (sidecars set, then
// merged into the raw details).
func sidecarUsage() *models.Usage {
	u := &models.Usage{PromptTokens: 100400, CompletionTokens: 5, TotalTokens: 100405}
	u.PromptDetails.CachedTokens = 100000
	u.PromptDetails.CacheWriteTokens = 300
	u.MergeDetails()
	return u
}

func checkUsage(t *testing.T, where string, usage map[string]interface{}, input, read, write, output float64) {
	t.Helper()
	for k, v := range map[string]float64{
		"input_tokens": input, "cache_read_input_tokens": read,
		"cache_creation_input_tokens": write, "output_tokens": output,
	} {
		if got, _ := usage[k].(float64); got != v {
			t.Errorf("%s: usage.%s = %v, want %v (usage %v)", where, k, usage[k], v, usage)
		}
	}
}

func TestMessagesDialectUsageExcludesCacheTokens(t *testing.T) {
	for name, usage := range map[string]func() *models.Usage{"raw details": rawOnlyUsage, "sidecars": sidecarUsage} {
		resp := &models.ChatCompletionResponse{
			ID: "chatcmpl-u", Model: "anthropic/claude-sonnet-5.5",
			Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(`"ok"`)}, FinishReason: "stop"}},
			Usage:   usage(),
		}
		for variant, out := range map[string]func() ([]byte, error){
			"ResponseFromCanonical":            func() ([]byte, error) { return tr.ResponseFromCanonical(resp) },
			"ResponseFromCanonicalWithMapping": func() ([]byte, error) { return tr.ResponseFromCanonicalWithMapping(resp, nil) },
		} {
			body, err := out()
			if err != nil {
				t.Fatalf("%s/%s: %v", name, variant, err)
			}
			var msg map[string]interface{}
			if err := json.Unmarshal(body, &msg); err != nil {
				t.Fatal(err)
			}
			u, _ := msg["usage"].(map[string]interface{})
			checkUsage(t, name+"/"+variant, u, 100, 100000, 300, 5)
		}
	}
}

// A usage whose cache tokens exceed prompt_tokens is already exclusive
// (Anthropic's own convention): input_tokens stays prompt_tokens.
func TestMessagesDialectUsageExclusiveShapeKept(t *testing.T) {
	u := &models.Usage{PromptTokens: 100, CompletionTokens: 5}
	u.PromptDetails.CachedTokens = 600
	u.PromptDetails.CacheWriteTokens = 200
	resp := &models.ChatCompletionResponse{
		ID: "chatcmpl-x", Model: "m",
		Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(`"ok"`)}, FinishReason: "stop"}},
		Usage:   u,
	}
	body, err := tr.ResponseFromCanonical(resp)
	if err != nil {
		t.Fatal(err)
	}
	var msg map[string]interface{}
	_ = json.Unmarshal(body, &msg)
	u2, _ := msg["usage"].(map[string]interface{})
	checkUsage(t, "exclusive", u2, 100, 600, 200, 5)
}

// Stream: usage on the first chunk lands in message_start, the trailing usage
// chunk in message_delta (cumulative, as Anthropic's message_delta usage is).
func TestStreamDialectUsageExcludesCacheTokens(t *testing.T) {
	chunks := []models.StreamChunk{
		{ID: "chatcmpl-s", Model: "anthropic/claude-sonnet-5.5", Usage: rawOnlyUsage(),
			Choices: []models.StreamChoice{{Delta: models.Delta{Role: "assistant"}}}},
		{ID: "chatcmpl-s", Choices: []models.StreamChoice{{Delta: models.Delta{Content: strPtr("ok")}}}},
		{ID: "chatcmpl-s", Choices: []models.StreamChoice{{FinishReason: strPtr("stop")}}},
		{ID: "chatcmpl-s", Usage: sidecarUsage()},
	}
	events, errs := tr.StreamEventsFromCanonical(context.Background(), feedChunks(chunks))
	got := collectEvents(t, events, errs)
	var sawStart, sawDelta bool
	for _, e := range got {
		switch e["_event"] {
		case "message_start":
			sawStart = true
			msg, _ := e["message"].(map[string]interface{})
			u, _ := msg["usage"].(map[string]interface{})
			checkUsage(t, "message_start", u, 100, 100000, 300, 5)
		case "message_delta":
			sawDelta = true
			u, _ := e["usage"].(map[string]interface{})
			checkUsage(t, "message_delta", u, 100, 100000, 300, 5)
		}
	}
	if !sawStart || !sawDelta {
		t.Fatalf("events %v: want message_start and message_delta", getEventNames(got))
	}
}
