package server

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A /v1/responses stream is priced from the usage its last event reports,
// and the gateway reads that event within a line limit
// (maxResponsesUsageLineBytes, 10 MiB). A last event over the limit (a
// response.completed carrying base64 images can be) cannot be read. On a
// request paid from prepaid credit (a managed key's balance) the stream is
// then charged what a hold for it is, never 0: its prompt as the gateway
// estimates it, and the output up to its max_output_tokens or, without one,
// the most a hold assumes (the model's maximum output, at most
// managed.reserve_default_output_tokens), priced at the model's card as the
// cost plugin prices any usage, with usage_estimated and the reason
// (usage_unreadable: final_event_over_limit) in the log.

func (g *responsesBillingGateway) sendBody(t *testing.T, key, body string) (int, string) {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/responses", bytes.NewBufferString(body))
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")
	ctx, cancel := context.WithTimeout(req.Context(), 20*time.Second)
	defer cancel()
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req.WithContext(ctx))
	b, _ := io.ReadAll(w.Result().Body)
	return w.Code, string(b)
}

// heldOutput is the output a hold assumes for gpt-4o with no
// max_output_tokens: the model's maximum output, at most the configured
// managed.reserve_default_output_tokens.
func heldOutput(t *testing.T) int {
	t.Helper()
	info, ok := testModelDBPtr().Load().Get("gpt-4o")
	if !ok {
		t.Fatal("set-up: gpt-4o is not in the bundled model database")
	}
	return billing.DefaultMaxOut(info.MaxOutputTokens, config.DefaultConfig().Managed.ReserveDefaultOutputTokens)
}

func TestResponsesStream_AFinalUsageLineOverTheLimitIsChargedTheHold(t *testing.T) {
	for _, tc := range []struct {
		name, extra string
		orgEstimate int // the org's managed_models.default_max_tokens_estimate, when set
		out         func(t *testing.T) int
	}{
		{"max_output_tokens 300", `,"max_output_tokens":300`, 0, func(*testing.T) int { return 300 }},
		{"no max_output_tokens", ``, 0, heldOutput},
		// 2000, not below billing.MinOrgDefaultMaxOut (1024), which would hold the floor.
		{"the org's default_max_tokens_estimate", ``, 2000, func(*testing.T) int { return 2000 }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newResponsesBillingGateway(t)
			if tc.orgEstimate > 0 {
				n := tc.orgEstimate
				org := *g.srv.handlers.tenantStore.Get(respOrg)
				org.ManagedModels = &tenant.ManagedModelsConfig{DefaultMaxTokensEstimate: &n}
				g.srv.handlers.tenantStore.Set(respOrg, &org)
			}
			body := fmt.Sprintf(`{"model":"gpt-4o","stream":true,"input":"Describe a %s, please."%s}`, respHugeMarker, tc.extra)
			chat, reason := requestview.Responses([]byte(body)).ChatRequest(context.Background())
			if reason != "" {
				t.Fatal(reason)
			}
			wantIn, wantOut := billing.EstimateInputTokens(chat), tc.out(t)

			before := g.balance(t)
			code, resp := g.sendBody(t, respManagedKey, body)
			if code != http.StatusOK || !strings.Contains(resp, "response.completed") {
				t.Fatalf("status %d, want 200 with the whole stream relayed (%d bytes)", code, len(resp))
			}
			run := g.probe.last(t, 1)
			debit := before - g.balance(t)
			t.Logf("debit %d micros, cost %q, usage %+v, usage_estimated %q, usage_unreadable %q",
				debit, run.cost, run.usage, run.usageEstimated, run.usageUnreadable)
			if debit <= 0 {
				t.Fatalf("the managed key was debited %d micros for a stream whose last event the gateway could not read; want the hold", debit)
			}
			if run.usage == nil || run.usage.PromptTokens != wantIn || run.usage.CompletionTokens != wantOut || run.usage.TotalTokens != wantIn+wantOut {
				t.Errorf("priced on usage %+v, want the hold's: %d in, %d out", run.usage, wantIn, wantOut)
			}
			cost, err := strconv.ParseFloat(run.cost, 64)
			if err != nil || auth.USDToMicros(cost) != debit {
				t.Errorf("the run's cost %q is not the %d micros debited: the log must show what was charged", run.cost, debit)
			}
			if run.usageEstimated != "true" || run.usageUnreadable != "final_event_over_limit" {
				t.Errorf("usage_estimated %q, usage_unreadable %q; want true and final_event_over_limit", run.usageEstimated, run.usageUnreadable)
			}
		})
	}
}

// Controls for TestResponsesStream_AFinalUsageLineOverTheLimitIsChargedTheHold, the test above.
func TestResponsesStream_TheHoldIsChargedOnlyForAnUnreadableLastEventOnPrepaidCredit(t *testing.T) {
	for _, tc := range []struct {
		name, key, input string
	}{
		{"managed key, cut off", respManagedKey, respCutOffMarker},
		{"BYOK key, last event over the limit", respByokKey, respHugeMarker},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newResponsesBillingGateway(t)
			before := g.balance(t)
			code, _ := g.sendBody(t, tc.key, fmt.Sprintf(`{"model":"gpt-4o","stream":true,"input":%q}`, tc.input))
			if code != http.StatusOK {
				t.Fatalf("status %d, want 200", code)
			}
			run := g.probe.last(t, 1)
			if debit := before - g.balance(t); debit != 0 || run.cost != "" || run.usage != nil || run.usageUnreadable != "" {
				t.Errorf("debit %d, cost %q, usage %+v, usage_unreadable %q; want none", debit, run.cost, run.usage, run.usageUnreadable)
			}
		})
	}
}

// heldResponsesUsage reads the body when no check read it before, in one
// pass that reads no value nested more than 64 levels deep, and under
// a context the client's hang-up does not end: the stream was served, so the
// prompt it is charged does not depend on when the client went away. A body
// holding values nested 9000 deep is estimated from what is read above
// them.
func TestHeldResponsesUsage_ReadsTheBodyInOnePassWhateverTheClient(t *testing.T) {
	g := newResponsesBillingGateway(t)
	held := func(ctx context.Context, body string) int {
		rc := models.AcquireRequestContext()
		defer rc.Release()
		rc.Model = "gpt-4o"
		rc.PassThrough = requestview.Responses([]byte(body))
		return g.srv.handlers.heldResponsesUsage(ctx, rc).PromptTokens
	}
	gone, cancel := context.WithCancel(context.Background())
	cancel()

	body := `{"model":"gpt-4o","stream":true,"input":"Describe a cat, please, in a few short words."}`
	want := held(context.Background(), body)
	if want == 0 {
		t.Fatal("set-up: no prompt estimated")
	}
	if got := held(gone, body); got != want {
		t.Errorf("after the client went away the prompt is estimated at %d tokens, want %d", got, want)
	}

	deep := strings.Repeat(`{"a":`, 9000) + `"x"` + strings.Repeat("}", 9000)
	items := strings.TrimSuffix(strings.Repeat(`{"type":"zz_unknown","x":`+deep+`},`, 16), ",")
	start := time.Now()
	got := held(gone, `{"model":"gpt-4o","stream":true,"input":[{"role":"user","content":"Describe a cat, please, in a few short words."},`+items+`]}`)
	t.Logf("16 values 9000 deep: estimated in %v", time.Since(start))
	if got == 0 {
		t.Error("nothing estimated for a body with values nested 9000 deep, want the text above them")
	}
}
