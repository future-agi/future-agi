package providers_test

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/anthropic"
	"github.com/futureagi/agentcc-gateway/internal/providers/azure"
	"github.com/futureagi/agentcc-gateway/internal/providers/bedrock"
	"github.com/futureagi/agentcc-gateway/internal/providers/cohere"
	"github.com/futureagi/agentcc-gateway/internal/providers/gemini"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
	anthropicwire "github.com/futureagi/agentcc-gateway/internal/translation/anthropic"
)

func TestToolPolicyAcceptance(t *testing.T) {
	constructors := map[string]func(string, config.ProviderConfig) (providers.Provider, error){
		"anthropic": func(id string, c config.ProviderConfig) (providers.Provider, error) { return anthropic.New(id, c) },
		"gemini":    func(id string, c config.ProviderConfig) (providers.Provider, error) { return gemini.New(id, c) },
		"openai":    func(id string, c config.ProviderConfig) (providers.Provider, error) { return openai.New(id, c) },
		"azure":     func(id string, c config.ProviderConfig) (providers.Provider, error) { return azure.New(id, c) },
		"cohere":    func(id string, c config.ProviderConfig) (providers.Provider, error) { return cohere.New(id, c) },
		"bedrock":   func(id string, c config.ProviderConfig) (providers.Provider, error) { return bedrock.New(id, c) },
	}
	for provider, newProvider := range constructors {
		for _, test := range []string{"permitted", "none", "allowed-auto", "allowed-required", "parallel", "callers", "empty-callers"} {
			for _, stream := range []bool{false, true} {
				name := provider + "/" + test
				if stream {
					name += "/stream"
				}
				t.Run(name, func(t *testing.T) {
					var calls atomic.Int32
					bodies := make(chan string, 2)
					upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						calls.Add(1)
						data, _ := io.ReadAll(r.Body)
						bodies <- string(data)
						w.Header().Set("Content-Type", "application/json")
						if stream {
							w.Header().Set("Content-Type", "text/event-stream")
							io.WriteString(w, "data: [DONE]\n\n")
							return
						}
						io.WriteString(w, `{"id":"synthetic","type":"message","role":"assistant","content":[{"type":"text","text":"ok"}],"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"candidates":[{"content":{"role":"model","parts":[{"text":"ok"}]},"finishReason":"STOP"}],"message":{"role":"assistant","content":[{"type":"text","text":"ok"}]},"finish_reason":"COMPLETE","usage":{"input_tokens":1,"output_tokens":1}}`)
					}))
					defer upstream.Close()
					p, err := newProvider("synthetic", config.ProviderConfig{BaseURL: upstream.URL, APIKey: "synthetic-key", DefaultTimeout: 5 * time.Second, AWSRegion: "us-east-1", AWSAccessKeyID: "synthetic", AWSSecretAccessKey: "synthetic"})
					if err != nil {
						t.Fatal(err)
					}
					defer p.Close()
					body := `{"model":"test-model","messages":[{"role":"user","content":"synthetic"}],"tools":[{"type":"function","function":{"name":"permitted_tool","parameters":{"type":"object","properties":{}}}},{"type":"function","function":{"name":"restricted_tool","parameters":{"type":"object","properties":{}}}}]`
					switch test {
					case "none":
						body += `,"tool_choice":"none"`
					case "allowed-auto", "allowed-required":
						mode := "auto"
						if test == "allowed-required" {
							mode = "required"
						}
						body += `,"tool_choice":{"type":"allowed_tools","allowed_tools":{"mode":"` + mode + `","tools":[{"type":"function","function":{"name":"permitted_tool"}}]}}`
					case "parallel":
						body += `,"parallel_tool_calls":false`
					}
					body += "}"
					var req *models.ChatCompletionRequest
					if test == "callers" || test == "empty-callers" {
						callers := `["code_execution_20260120"]`
						if test == "empty-callers" {
							callers = `[]`
						}
						req, _, err = (&anthropicwire.Translator{}).RequestToCanonical([]byte(`{"model":"test-model","max_tokens":32,"messages":[{"role":"user","content":"synthetic"}],"tools":[{"name":"restricted_tool","allowed_callers":` + callers + `,"input_schema":{"type":"object"}}]}`))
					} else {
						req = &models.ChatCompletionRequest{}
						err = json.Unmarshal([]byte(body), req)
					}
					if err != nil {
						t.Fatal(err)
					}
					denied := (test == "parallel" && (provider == "gemini" || provider == "cohere")) || ((test == "callers" || test == "empty-callers") && provider != "anthropic") || (test == "allowed-required" && provider == "cohere")
					if test == "empty-callers" {
						denied = true
					}
					if stream {
						chunks, errs := p.StreamChatCompletion(context.Background(), req)
						for range chunks {
						}
						err = <-errs
					} else {
						_, err = p.ChatCompletion(context.Background(), req)
					}
					if denied {
						apiErr, ok := err.(*models.APIError)
						if !ok || apiErr.Status != 400 {
							t.Errorf("restriction must fail with 400; got %v", err)
						}
						if calls.Load() != 0 {
							t.Errorf("restricted request reached provider: calls=%d body=%s", calls.Load(), <-bodies)
						}
						return
					}
					if !stream && err != nil {
						t.Fatalf("permitted request failed: %v", err)
					}
					if calls.Load() != 1 {
						t.Fatalf("provider calls=%d, want 1", calls.Load())
					}
					forwarded := <-bodies
					t.Logf("forwarded=%s", forwarded)
					var wire map[string]json.RawMessage
					if err = json.Unmarshal([]byte(forwarded), &wire); err != nil {
						t.Fatal(err)
					}
					switch test {
					case "permitted":
						if !strings.Contains(forwarded, "permitted_tool") {
							t.Error("permitted tool missing")
						}
					case "none":
						if len(wire["tools"]) > 0 && !strings.Contains(string(wire["tool_choice"]), "none") && !strings.Contains(string(wire["toolConfig"]), "NONE") {
							t.Error("tool_choice none left callable tools")
						}
					case "allowed-auto", "allowed-required":
						if strings.Contains(forwarded, "restricted_tool") || !strings.Contains(forwarded, "permitted_tool") {
							t.Error("allowed subset not enforced")
						}
						if test == "allowed-required" && !strings.Contains(forwarded, "any") && !strings.Contains(forwarded, "ANY") && !strings.Contains(forwarded, "required") {
							t.Error("required mode missing")
						}
					case "parallel":
						if !strings.Contains(forwarded, `"parallel_tool_calls":false`) && !strings.Contains(forwarded, `"disable_parallel_tool_use":true`) {
							t.Error("single-call restriction missing")
						}
					case "callers":
						if !strings.Contains(forwarded, `"allowed_callers":["code_execution_20260120"]`) {
							t.Error("caller restriction missing")
						}
					}
				})
			}
		}
	}
}
