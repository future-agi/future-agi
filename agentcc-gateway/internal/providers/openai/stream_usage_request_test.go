package openai

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	geminitrans "github.com/futureagi/agentcc-gateway/internal/translation/gemini"
)

type syntheticUsageTransport func(*http.Request) (*http.Response, error)

func (f syntheticUsageTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

// These are the same completion and GenAI translations used by their handlers.
// No network is involved: the transport inspects the actual serialized body.
func TestStreamUsageAlwaysRequestedWithoutChangingClientPreference(t *testing.T) {
	for _, dialect := range []string{"completion", "genai"} {
		for _, preference := range []string{"omitted", "false", "true"} {
			t.Run(dialect+"/"+preference, func(t *testing.T) {
				var req *models.ChatCompletionRequest
				if dialect == "completion" {
					completion := &models.CompletionRequest{Model: "synthetic-stream-model", Prompt: json.RawMessage(`"synthetic prompt"`), Stream: true}
					if preference != "omitted" {
						completion.StreamOptions = &models.StreamOptions{IncludeUsage: preference == "true"}
					}
					req = completion.ToChatRequest()
				} else {
					var err error
					req, _, err = geminitrans.New().RequestToCanonical([]byte(`{"contents":[{"role":"user","parts":[{"text":"synthetic prompt"}]}]}`))
					if err != nil {
						t.Fatal(err)
					}
					req.Model, req.Stream = "synthetic-stream-model", true
					if preference != "omitted" {
						req.StreamOptions = &models.StreamOptions{IncludeUsage: preference == "true"}
					}
				}
				originalOptions := req.StreamOptions
				before, err := json.Marshal(req)
				if err != nil {
					t.Fatal(err)
				}
				p, err := New("synthetic-provider", config.ProviderConfig{BaseURL: "https://synthetic-provider.invalid"})
				if err != nil {
					t.Fatal(err)
				}
				defer p.Close()
				calls := 0
				p.httpClient.Transport = syntheticUsageTransport(func(r *http.Request) (*http.Response, error) {
					calls++
					var outbound models.ChatCompletionRequest
					if err := json.NewDecoder(r.Body).Decode(&outbound); err != nil {
						t.Error(err)
					}
					if !outbound.Stream || outbound.StreamOptions == nil || !outbound.StreamOptions.IncludeUsage {
						t.Errorf("outbound usage request missing: %+v", outbound.StreamOptions)
					}
					return &http.Response{StatusCode: http.StatusOK, Header: make(http.Header), Body: io.NopCloser(strings.NewReader("data: {\"choices\":[],\"usage\":{\"prompt_tokens\":17,\"completion_tokens\":3}}\n\ndata: [DONE]\n\n"))}, nil
				})
				chunks, errs := p.StreamChatCompletion(context.Background(), req)
				var usage *models.Usage
				for chunk := range chunks {
					usage = models.MergeUsage(usage, chunk.Usage)
				}
				for err := range errs {
					if err != nil {
						t.Fatal(err)
					}
				}
				if calls != 1 || usage.NeedsTokenEstimate() || usage.PromptTokens != 17 || usage.CompletionTokens != 3 {
					t.Fatalf("calls=%d usage=%+v", calls, usage)
				}
				after, err := json.Marshal(req)
				if err != nil {
					t.Fatal(err)
				}
				if string(before) != string(after) || req.StreamOptions != originalOptions {
					t.Fatal("adapter changed the client-visible request or stream preference")
				}
			})
		}
	}
}
