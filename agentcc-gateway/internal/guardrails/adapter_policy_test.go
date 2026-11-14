package guardrails_test

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	g "github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/external"
	fi "github.com/futureagi/agentcc-gateway/internal/guardrails/futureagi"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// Regression coverage for execution policy. Every
// network destination below is the synthetic httptest listener on this VM.
func TestAdapterFailurePolicyScope(t *testing.T) {
	for _, adapter := range []string{"external-http-error", "evaluation-http-error", "webhook-missing-pass"} {
		for _, open := range []bool{false, true} {
			for _, threshold := range []float64{0.5, 1} {
				t.Run(fmt.Sprintf("%s/open=%v/threshold=%g", adapter, open, threshold), func(t *testing.T) {
					srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						if adapter == "webhook-missing-pass" {
							fmt.Fprint(w, `{}`)
							return
						}
						w.WriteHeader(503)
					}))
					defer srv.Close()
					var check g.Guardrail
					switch adapter {
					case "external-http-error":
						check = external.New("check", map[string]interface{}{"provider": "lakera", "endpoint": srv.URL, "api_key": "synthetic-local-only"})
					case "evaluation-http-error":
						check = fi.New("check", map[string]interface{}{"base_url": srv.URL, "api_key": "synthetic-local-only", "secret_key": "synthetic-local-only", "eval_id": "synthetic-evaluation"})
					default:
						check = webhook.New("check", map[string]interface{}{"url": srv.URL})
					}
					raw := fmt.Sprintf(`{"fail_open":%v,"checks":{"check":{"enabled":true,"action":"block","confidence_threshold":%g}}}`, open, threshold)
					p := acceptancePlugin(t, false, raw, map[string]g.Guardrail{"check": check})
					rc := acceptanceRC()
					defer rc.Release()
					result := p.ProcessRequest(context.Background(), rc)
					blocked := result.Action == pipeline.ShortCircuit
					if !open && (result.Error == nil || result.Error.Status != 503 || result.Error.Type != "guardrail" || result.Error.Code != "guardrail_unavailable") {
						t.Errorf("expected unavailable HTTP contract, got %+v", result.Error)
					}
					if blocked == open {
						t.Errorf("execution failure admission mismatch: blocked=%v fail_open=%v threshold=%g", blocked, open, threshold)
					}
					recorded := false
					for _, outcome := range rc.GuardrailResults {
						if strings.Contains(outcome.Message, "guardrail_unavailable") {
							recorded = true
						}
					}
					if !recorded {
						t.Error("execution failure was not recorded as guardrail_unavailable")
					}
				})
			}
		}
	}
}

// A summary that lists only the detectors that fired is a complete verdict:
// the omitted default detector did not trigger, so the positive toxicity
// result must block whatever the organization's failure policy is.
func TestEnkryptSummaryWithOnlyTriggeredDetectorBlocks(t *testing.T) {
	for _, open := range []bool{false, true} {
		t.Run(fmt.Sprintf("open=%v", open), func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				fmt.Fprint(w, `{"summary":{"toxicity":["toxicity","insult"]},"details":{}}`)
			}))
			defer srv.Close()
			check := external.New("check", map[string]interface{}{"provider": "enkrypt", "endpoint": srv.URL, "api_key": "synthetic-local-only"})
			raw := fmt.Sprintf(`{"fail_open":%v,"checks":{"check":{"enabled":true,"action":"block","confidence_threshold":0.5}}}`, open)
			p := acceptancePlugin(t, false, raw, map[string]g.Guardrail{"check": check})
			rc := acceptanceRC()
			defer rc.Release()
			result := p.ProcessRequest(context.Background(), rc)
			if result.Action != pipeline.ShortCircuit {
				t.Fatalf("toxicity verdict was admitted: fail_open=%v action=%v", open, result.Action)
			}
			if result.Error == nil || result.Error.Code != "content_blocked" {
				t.Fatalf("expected a content block, got %+v", result.Error)
			}
			for _, outcome := range rc.GuardrailResults {
				if strings.Contains(outcome.Message, "guardrail_unavailable") {
					t.Fatalf("content verdict recorded as unavailable: %+v", outcome)
				}
			}
		})
	}
}
