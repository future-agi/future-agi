package server

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Unsupported Messages routing controls must not disappear at translation and
// become a billed fallback. Refuse before execution, not after taking a hold.
func TestMessagesRoutingFundedFallbackRefusesControls(t *testing.T) {
	controls := []struct {
		name, extra string
	}{
		{"models", `,"models":["never-use-this-model"]`},
		{"provider", `,"provider":{"only":["unavailable-upstream"]}`},
		{"fallbacks", `,"fallbacks":["never-use-this-model"]`},
		{"extra_body", `,"extra_body":{"provider":{"only":["unavailable-upstream"]}}`},
		{"plugins", `,"plugins":[{"id":"web"}]`},
		{"unsupported", `,"provider":{"sort":"price"}`},
	}
	for _, stream := range []bool{false, true} {
		for _, control := range controls {
			t.Run(fmt.Sprintf("%s/stream=%v", control.name, stream), func(t *testing.T) {
				g := newManagedTestGateway(t, r7ManagedFallbackConfig(true))
				g.ledger.SetMicros("org-m", auth.USDToMicros(1))
				g.operator.failFor.Store("org-model", http.StatusServiceUnavailable)
				direct := messagesRoutingCall(g.srv, managedOrgKey, "anthropic/claude-sonnet-5.5", stream, control.extra)
				if direct.Code != http.StatusBadRequest || g.managed.calls() != 0 {
					t.Fatalf("funded primary must refuse: status=%d calls=%d body=%s", direct.Code, g.managed.calls(), direct.Body)
				}
				fallback := messagesRoutingCall(g.srv, managedOrgKey, "org-model", stream, control.extra)
				if fallback.Code != direct.Code || fallback.Body.String() != direct.Body.String() || g.operator.calls() != 0 || g.managed.calls() != 0 || g.ledger.BalanceUSD("org-m") != 1 {
					t.Fatalf("BYOK-to-funded route must refuse before dispatch: status=%d own_calls=%d funded_calls=%d balance=%.6f body=%s", fallback.Code, g.operator.calls(), g.managed.calls(), g.ledger.BalanceUSD("org-m"), fallback.Body)
				}
			})
		}
	}
}

// Native-only tool-result content must either stay on the lossless native
// path or be refused before canonical routing silently extracts just its text.
func TestMessagesRoutingToolResultImageRefused(t *testing.T) {
	const image = `{"type":"image","source":{"type":"base64","media_type":"image/png","data":"aW1hZ2UtcHJvYmU="}}`
	for _, stream := range []bool{false, true} {
		for _, routed := range []bool{false, true} {
			t.Run(fmt.Sprintf("stream=%v/routed=%v", stream, routed), func(t *testing.T) {
				bodies := make(chan string, 4)
				up := &r7Upstream{Server: httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					// The registry also probes connectivity at startup; count only
					// actual Messages dispatches, never its health/model requests.
					if r.Method != http.MethodPost || r.URL.Path != "/v1/messages" {
						http.NotFound(w, r)
						return
					}
					b, _ := io.ReadAll(r.Body)
					bodies <- string(b)
					if stream {
						w.Header().Set("Content-Type", "text/event-stream")
						io.WriteString(w, "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_image\",\"type\":\"message\",\"role\":\"assistant\",\"model\":\"org-model\",\"content\":[],\"usage\":{\"input_tokens\":3,\"output_tokens\":0}}}\n\nevent: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"},\"usage\":{\"output_tokens\":1}}\n\nevent: message_stop\ndata: {\"type\":\"message_stop\"}\n\n")
						return
					}
					w.Header().Set("Content-Type", "application/json")
					io.WriteString(w, `{"id":"msg_image","type":"message","role":"assistant","model":"org-model","content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":3,"output_tokens":1}}`)
				}))}
				t.Cleanup(up.Close)
				srv := r7Gateway(t, map[string]r7Op{"alpha": {up, []string{"org-model", "org-model-2"}}}, func(cfg *config.Config) {
					p := cfg.Providers["alpha"]
					p.APIFormat = "anthropic"
					cfg.Providers["alpha"] = p
				})
				routing := `{}`
				if routed {
					routing = `{"model_fallbacks":{"org-model":["org-model-2"]}}`
				}
				r7Push(t, srv, r7OrgA, `{"providers":{"alpha":{"api_key":"org-alpha-key","api_format":"anthropic","models":["org-model","org-model-2"],"enabled":true}},"routing":`+routing+`}`)
				body := fmt.Sprintf(`{"model":"org-model","max_tokens":32,"stream":%v,"messages":[{"role":"assistant","content":[{"type":"tool_use","id":"call_image","name":"capture","input":{}}]},{"role":"user","content":[{"type":"tool_result","tool_use_id":"call_image","content":[{"type":"text","text":"look at this image"},%s]}]}]}`, stream, image)
				req := httptest.NewRequest(http.MethodPost, "/v1/messages", strings.NewReader(body))
				req.Header.Set("x-api-key", r7CustA)
				w := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(w, req)
				if routed {
					assertMessagesToolResultMediaRefusal(t, w)
					if len(bodies) != 0 {
						t.Fatalf("routed image must be refused before dispatch: calls=%d", len(bodies))
					}
					return
				}
				if w.Code != http.StatusOK || len(bodies) != 1 {
					t.Fatalf("lossless native path changed: status=%d calls=%d body=%s", w.Code, len(bodies), w.Body)
				}
				if got := <-bodies; got != body {
					t.Fatalf("native pass-through changed content: %s", got)
				}
			})
		}
	}
}

func TestMessagesRoutingToolResultCompatibility(t *testing.T) {
	for _, tc := range []struct {
		name, content string
		allowed       bool
	}{
		{"string", `"result"`, true},
		{"text", `[{"type":"text","text":"result"}]`, true},
		{"image", `[{"type":"image","source":{"type":"url","url":"https://example.invalid/image.png"}}]`, false},
		{"text_and_image", `[{"type":"text","text":"result"},{"type":"image","source":{"type":"base64","media_type":"image/png","data":"aW1hZ2U="}}]`, false},
		{"tool_use", `[{"type":"tool_use","id":"nested","name":"capture","input":{}}]`, false},
		{"tool_result", `[{"type":"tool_result","tool_use_id":"nested","content":"result"}]`, false},
		{"annotated_text", `[{"type":"text","text":"result","cache_control":{"type":"ephemeral"}}]`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := []byte(`{"model":"org-model","max_tokens":32,"messages":[{"role":"user","content":[{"type":"tool_result","tool_use_id":"call_image","content":` + tc.content + `}]}]}`)
			err := messagesRoutedTranslationError(body)
			if (err == nil) != tc.allowed {
				t.Fatalf("routable=%v want %v: %v", err == nil, tc.allowed, err)
			}
			if err != nil && err.Code != "messages_routing_translation_unsupported" {
				t.Fatalf("wrong refusal: %v", err)
			}
		})
	}
	// Top-level images are preserved by canonical translation; do not disable
	// working image input while tightening the tool-result-only check.
	body := []byte(`{"model":"org-model","max_tokens":32,"messages":[{"role":"user","content":[{"type":"image","source":{"type":"base64","media_type":"image/png","data":"aW1hZ2U="}}]}]}`)
	if err := messagesRoutedTranslationError(body); err != nil {
		t.Fatalf("top-level image rejected: %v", err)
	}
}
