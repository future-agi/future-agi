package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func messagesToolResultBody(model string, stream bool, content string) string {
	return fmt.Sprintf(`{"model":%q,"max_tokens":32,"stream":%v,"messages":[{"role":"assistant","content":[{"type":"tool_use","id":"call_image","name":"capture","input":{}}]},{"role":"user","content":[{"type":"tool_result","tool_use_id":"call_image","content":%s}]}]}`, model, stream, content)
}

func messagesToolResultRoute(t *testing.T, route string) (*managedTestGateway, string) {
	t.Helper()
	cfg := r7ManagedFallbackConfig(true)
	model := "org-model"
	if route == "credit_direct" {
		model = "anthropic/claude-sonnet-5.5"
	}
	if route == "byok_direct" || route == "credit_direct" {
		cfg.Routing = nil
	}
	g := newManagedTestGateway(t, cfg)
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))
	if route == "byok_credit_fallback" {
		// A byok primary falling back to credits changes billing mode: the
		// route exists only where the node allows mixed billing chains
		// (managed.mixed_billing_chains, MIXED-HOP-GUARD-1).
		allowMixedBillingChains(g)
		g.operator.failFor.Store(model, http.StatusServiceUnavailable)
	}
	return g, model
}

// The verifier's 16 counterexamples: the same image-only and mixed tool results
// must be refused on every canonical entry, before any attempt or credit hold.
func TestMessagesToolResultImagesAllCanonicalRoutes(t *testing.T) {
	for _, route := range []string{"credit_direct", "byok_direct", "byok_model_fallback", "byok_credit_fallback"} {
		for _, stream := range []bool{false, true} {
			for _, image := range []struct{ name, content string }{
				{"image_only", `[{"type":"image","source":{"type":"base64","media_type":"image/png","data":"cmV2aWV3LWltYWdl"}}]`},
				{"text_and_image", `[{"type":"text","text":"review-caption"},{"type":"image","source":{"type":"url","url":"https://image.example.invalid/review.png"}}]`},
			} {
				t.Run(fmt.Sprintf("%s/stream=%v/%s", route, stream, image.name), func(t *testing.T) {
					g, model := messagesToolResultRoute(t, route)
					before := g.account(t, "org-m")
					var attempts, reservations atomic.Int32
					g.srv.handlers.hookObserver = func(hook, _ string, _ *models.RequestContext) {
						switch hook {
						case hookBeginAttempt:
							attempts.Add(1)
						case hookReserveAdd:
							reservations.Add(1)
						}
					}
					body := messagesToolResultBody(model, stream, image.content)
					want := httptest.NewRecorder()
					refusal := messagesRoutedTranslationError([]byte(body))
					if refusal == nil || refusal.Code != "messages_routing_translation_unsupported" {
						t.Fatalf("fixture must have the existing translation refusal: %v", refusal)
					}
					writeAnthropicErrorFromError(want, refusal)
					w := postMessages(t, g.srv, "/v1/messages", managedOrgKey, body)
					if w.Code != http.StatusBadRequest || w.Body.String() != want.Body.String() || w.Header().Get("Content-Type") != want.Header().Get("Content-Type") {
						t.Errorf("want existing refusal %s; got status=%d body=%s", want.Body, w.Code, w.Body)
					}
					if g.operator.calls() != 0 || g.managed.calls() != 0 || attempts.Load() != 0 || reservations.Load() != 0 {
						t.Errorf("refused request executed: byok=%d credit=%d attempts=%d reservations=%d", g.operator.calls(), g.managed.calls(), attempts.Load(), reservations.Load())
					}
					if after := g.account(t, "org-m"); !reflect.DeepEqual(before, after) || len(g.outbox("org-m")) != 0 {
						t.Errorf("refused request moved credits: before=%+v after=%+v settlements=%v", before, after, g.outbox("org-m"))
					}
				})
			}
		}
	}
}

// Check actual rewritten attempts, not only status: text tool results and
// top-level images still reach the selected provider, including billed fallback.
func TestMessagesToolResultCanonicalPositiveControls(t *testing.T) {
	for _, route := range []string{"credit_direct", "byok_direct", "byok_model_fallback", "byok_credit_fallback"} {
		for _, stream := range []bool{false, true} {
			for _, control := range []struct{ name, content, want string }{
				{"string", `"tool result text"`, `"content":"tool result text"`},
				{"text_blocks", `[{"type":"text","text":"tool result "},{"type":"text","text":"text"}]`, `"content":"tool result text"`},
				{"top_level_image", "", "data:image/png;base64,cmV2aWV3LWltYWdl"},
			} {
				t.Run(fmt.Sprintf("%s/stream=%v/%s", route, stream, control.name), func(t *testing.T) {
					g, model := messagesToolResultRoute(t, route)
					var seen atomic.Int32
					g.srv.handlers.hookObserver = func(hook, _ string, rc *models.RequestContext) {
						if hook != hookRewriteUpstream {
							return
						}
						b, err := json.Marshal(rc.Request)
						if err != nil || !strings.Contains(string(b), control.want) {
							t.Errorf("canonical attempt lost content: %s (%v)", b, err)
						}
						seen.Add(1)
					}
					body := messagesToolResultBody(model, stream, control.content)
					if control.name == "top_level_image" {
						body = fmt.Sprintf(`{"model":%q,"max_tokens":32,"stream":%v,"messages":[{"role":"user","content":[{"type":"image","source":{"type":"base64","media_type":"image/png","data":"cmV2aWV3LWltYWdl"}}]}]}`, model, stream)
					}
					w := postMessages(t, g.srv, "/v1/messages", managedOrgKey, body)
					wantOwn, wantFunded := 1, 0
					if route == "credit_direct" {
						wantOwn, wantFunded = 0, 1
					} else if route == "byok_credit_fallback" {
						wantOwn, wantFunded = 2, 1
					}
					if w.Code != http.StatusOK || g.operator.calls() != wantOwn || g.managed.calls() != wantFunded || int(seen.Load()) != wantOwn+wantFunded {
						t.Fatalf("control failed: status=%d byok=%d credit=%d attempts=%d body=%s", w.Code, g.operator.calls(), g.managed.calls(), seen.Load(), w.Body)
					}
					wantBalance := 1.0
					if wantFunded != 0 {
						wantBalance -= upstreamCost * 1.05
					}
					if got := fmt.Sprintf("%.6f", g.ledger.BalanceUSD("org-m")); got != fmt.Sprintf("%.6f", wantBalance) {
						t.Errorf("control balance=%s want %.6f", got, wantBalance)
					}
				})
			}
		}
	}
}
