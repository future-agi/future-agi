package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/hallucination"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Characterize the known observation gap, not a complete streaming-monitoring
// guarantee. Production enables this capture only for configured body sinks.
// Turning it on here isolates the cause without enabling persistent logging.
func TestKeyPolicyIntegrationMonitorStreamObservationCaptureBound(t *testing.T) {
	parts := []string{"I made that up. This information is fabricated. ", "A fictional study. According to my training data. I should note that I may have hallucinated."}
	for _, capture := range []bool{false, true} {
		t.Run(fmt.Sprintf("capture=%t", capture), func(t *testing.T) {
			var calls atomic.Int32
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost {
					io.WriteString(w, `{"data":[]}`)
					return
				}
				calls.Add(1)
				w.Header().Set("Content-Type", "text/event-stream")
				for _, part := range parts {
					content, _ := json.Marshal(part)
					fmt.Fprintf(w, "data: {\"id\":\"capture-control\",\"object\":\"chat.completion.chunk\",\"model\":\"gpt-5.5\",\"choices\":[{\"index\":0,\"delta\":{\"content\":%s}}]}\n\n", content)
					w.(http.Flusher).Flush()
				}
				io.WriteString(w, "data: {\"id\":\"capture-control\",\"object\":\"chat.completion.chunk\",\"model\":\"gpt-5.5\",\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":5,\"completion_tokens\":3,\"total_tokens\":8}}\n\ndata: [DONE]\n\n")
			}))
			defer upstream.Close()
			cfg := &tenant.GuardrailConfig{FailOpen: false, KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy("hallucination-detection", "monitor", "block", true, nil)}}
			srv, audit := runtimePolicyServer(t, upstream.URL, cfg, map[string]guardrails.Guardrail{"hallucination-detection": hallucination.New(nil)})
			srv.handlers.SetCaptureStreamContent(capture)
			response := runtimePolicySend(srv, guardedRoutes[0], true, "a")
			if response.Code != http.StatusOK || calls.Load() != 1 {
				t.Fatalf("status=%d calls=%d; want 200/1", response.Code, calls.Load())
			}
			if response.Header().Get("x-agentcc-guardrail-triggered") != "" || !strings.Contains(response.Body.String(), "data: [DONE]") {
				t.Fatal("Monitor enforced or lost normal completion")
			}
			var actual strings.Builder
			for _, line := range strings.Split(response.Body.String(), "\n") {
				if !strings.HasPrefix(line, "data: ") || line == "data: [DONE]" {
					continue
				}
				var chunk models.StreamChunk
				if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &chunk); err != nil {
					t.Fatal(err)
				}
				for _, choice := range chunk.Choices {
					if choice.Delta.Content != nil {
						actual.WriteString(*choice.Delta.Content)
					}
				}
			}
			if actual.String() != strings.Join(parts, "") {
				t.Fatal("Monitor changed the provider text")
			}
			results, usage := audit.snapshot()
			if usage == nil || usage.TotalTokens != 8 {
				t.Fatal("Monitor lost stream accounting")
			}
			if !capture {
				if len(results) != 0 {
					t.Fatalf("capture-off characterization changed: got %d observations", len(results))
				}
				return
			}
			if len(results) != 1 || results[0].Mode != "monitor" || results[0].Outcome != "would_block" || results[0].Action != "log" {
				t.Fatalf("capture-on must observe one nonblocking violation: %+v", results)
			}
			encoded, _ := json.Marshal(results)
			if strings.Contains(string(encoded), parts[0]) || strings.Contains(string(encoded), parts[1]) {
				t.Fatal("observation leaked provider text")
			}
		})
	}
}
