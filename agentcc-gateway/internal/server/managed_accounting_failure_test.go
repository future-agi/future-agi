package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tokenizer"
)

// The upstream accepted HTTP, but never produced usable model output.
func failedAcceptedStream(kind string) upstreamStream {
	return func(w http.ResponseWriter, r *http.Request, model string) {
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, ": accepted\n\n")
		w.(http.Flusher).Flush()
		if strings.HasPrefix(kind, "role-") {
			writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"role":"assistant"}}]`)
		}
		if strings.Contains(kind, "error") {
			fmt.Fprint(w, "data:{\"error\":{\"message\":\"overloaded\",\"code\":502}}\n\n")
			w.(http.Flusher).Flush()
		}
		if kind == "drop" {
			conn, _, err := w.(http.Hijacker).Hijack()
			if err == nil {
				conn.Close()
			}
		}
	}
}

func TestManagedAccountingFailureBeforeOutput(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, kind := range []string{"eof", "drop", "error", "role-eof", "role-error"} {
			t.Run(surface+kind, func(t *testing.T) {
				g := newManagedTestGateway(t, managedOptIn())
				g.ledger.SetMicros("org-m", 1_000_000)
				up := failedAcceptedStream(kind)
				g.managed.streamWith.Store(&up)
				status, body, _ := sendManaged(t, g, surface, decoratedModel, true, false, nil)
				spent, held := settledAccount(t, g)
				// A start whose connection drops once the upstream has it got
				// no answer at all: it is charged at its own hold.
				// A start the upstream ended with an error or a clean
				// close carries the upstream's status and stays void.
				if kind == "drop" {
					recs := g.outbox("org-m")
					if status != 502 || held != 0 || spent <= 0 || len(recs) != 1 {
						t.Fatalf("status=%d spent=%d held=%d records=%v body=%s; want a 502 charged at its hold", status, spent, held, recs, body)
					}
					if rec := recs[0]; rec["kind"] != "spend" || rec["billed_micros"] != rec["hold_micros"] || rec["billed_micros"] != fmt.Sprint(spent) || rec["cost_source"] != "catalog_max" || rec["usage_estimated"] != "1" {
						t.Errorf("failure record=%v; want one spend at the start's own hold, catalog_max", rec)
					}
					return
				}
				if status != 502 || spent != 0 || held != 0 {
					t.Fatalf("status=%d spent=%d held=%d body=%s", status, spent, held, body)
				}
				for _, rec := range g.outbox("org-m") {
					if rec["kind"] != "void" || rec["usage_estimated"] == "1" {
						t.Errorf("failure record=%v", rec)
					}
				}
			})
		}
	}
}

func TestManagedAccountingRetryBillsOnlyReportedAnswer(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.ledger.SetMicros("org-m", 1_000_000)
	var calls atomic.Int32
	up := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
		if calls.Add(1) == 1 {
			failedAcceptedStream("error")(w, r, model)
			return
		}
		textThenUsage(w, r, model)
	})
	g.managed.streamWith.Store(&up)
	status := sendManagedConfiguredRetry(t, g, decoratedModel)
	spent, held := settledAccount(t, g)
	recs := g.outbox("org-m")
	if status != 200 || calls.Load() != 2 || spent != 7350 || held != 0 || len(recs) != 1 {
		t.Fatalf("status=%d calls=%d spent=%d held=%d records=%v", status, calls.Load(), spent, held, recs)
	}
	if recs[0]["usage_estimated"] == "1" || recs[0]["cost_source"] != "provider_reported" {
		t.Fatalf("reported answer mislabeled: %v", recs)
	}
}

func TestManagedAccountingNonstreamMissingUsage(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, id := range []string{"acme/token", "acme/request"} {
			t.Run(surface+id, func(t *testing.T) {
				g := newManagedTestGateway(t, managedOptIn())
				installBillabilityModels(billabilityModels(t))(t, g)
				g.ledger.SetMicros("org-m", 1_000_000)
				up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
					w.Header().Set("Content-Type", "application/json")
					fmt.Fprintf(w, `{"id":"answer","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"hello there"},"finish_reason":"stop"}]}`, model)
				})
				g.managed.responseWith.Store(&up)
				status, _, _ := sendManaged(t, g, surface, id, false, false, nil)
				spent, held := settledAccount(t, g)
				want := int64(48)
				if id == "acme/request" {
					want = 42000
				}
				recs := g.outbox("org-m")
				if status != 200 || spent != want || held != 0 || len(recs) != 1 {
					t.Fatalf("status=%d spent=%d want=%d held=%d records=%v", status, spent, want, held, recs)
				}
				if recs[0]["usage_estimated"] != "1" || recs[0]["cost_source"] != "catalog" {
					t.Errorf("estimate not auditable: %v", recs)
				}
			})
		}
	}
}

func TestManagedAccountingInputOverheadUsesReservationEstimate(t *testing.T) {
	var req models.ChatCompletionRequest
	if err := json.Unmarshal([]byte(`{"model":"acme/token","messages":[{"role":"user","content":"hi"}],"tools":[{"type":"function","function":{"name":"lookup","description":"schema overhead","parameters":{"type":"object","properties":{"q":{"type":"string"}}}}}],"response_format":{"type":"json_schema","json_schema":{"name":"answer","schema":{"type":"object","properties":{"result":{"type":"string"}}}}}}`), &req); err != nil {
		t.Fatal(err)
	}
	tools, _ := json.Marshal(req.Tools)
	format, _ := json.Marshal(req.ResponseFormat)
	want := tokenizer.EstimateTokens(req.Messages) + (len(tools)+3)/4 + (len(format)+3)/4
	if got := billing.EstimateInputTokens(&req); got != want {
		t.Errorf("reservation input=%d want=%d", got, want)
	}
	rc := &models.RequestContext{Request: &req, Metadata: map[string]string{"billing_mode": billingManaged}}
	got := estimatedManagedStreamUsage(rc, &streamedOutput{chars: 11})
	if got == nil || got.PromptTokens != want || got.CompletionTokens != 3 {
		t.Fatalf("stream estimate=%+v want prompt=%d output=3", got, want)
	}
}

func TestManagedAccountingAccessUsesSellableEndpointPrices(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	all, err := catalog.ParseOpenRouter([]byte(`{"data":[{"id":"acme/paid","pricing":{"prompt":"0.0000001","completion":"0.0000001"}}]}`))
	if err != nil {
		t.Fatal(err)
	}
	eps, err := catalog.ParseOpenRouterEndpoints("acme/paid", []byte(`{"data":{"id":"acme/paid","endpoints":[{"tag":"missing","pricing":{}},{"tag":"good","pricing":{"prompt":"0.000002","completion":"0.000010"}}]}}`))
	if err != nil {
		t.Fatal(err)
	}
	all["acme/paid"].Endpoints = eps
	installBillabilityModels(all)(t, g)
	for _, row := range buildCatalogAccess(g.srv.handlers.catalog.Load(), managedOptIn(), 5, g.srv.handlers.catalog.Now()).Data {
		if row.ID == "acme/paid" && (row.Pricing == nil || row.Pricing.Prompt != "0.0000021" || row.Pricing.Completion != "0.0000105") {
			t.Errorf("access=%+v", row.Pricing)
		}
	}
	row := managedListedIDs(t, g)["acme/paid"]
	if row.Pricing == nil || row.Pricing.InputPerMTokens != 2.1 || row.Pricing.OutputPerMTokens != 10.5 {
		t.Errorf("list=%+v", row.Pricing)
	}
}
