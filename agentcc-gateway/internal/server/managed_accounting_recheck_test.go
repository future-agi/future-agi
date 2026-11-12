package server

// Synthetic probes, retained as product
// regressions.
import (
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestRecheckFailureEnvelope(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, kind := range []string{"empty-eof", "role-eof", "error-nospace", "error-space", "role-error", "reported-error", "partial-error"} {
			t.Run(surface+"/"+kind, func(t *testing.T) {
				g := newManagedTestGateway(t, managedOptIn())
				installBillabilityModels(billabilityModels(t))(t, g)
				// Exactly one request-fee hold; a reported failure cannot fund a retry.
				g.ledger.SetMicros("org-m", 42000)
				up := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
					w.Header().Set("Content-Type", "text/event-stream")
					fmt.Fprint(w, ": accepted\n\n")
					w.(http.Flusher).Flush()
					if strings.HasPrefix(kind, "role-") {
						writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"role":"assistant"}}]`)
					}
					if kind == "partial-error" {
						writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"content":"hello there"}}]`)
					}
					if kind == "reported-error" {
						fmt.Fprint(w, `data:{"error":{"code":502,"message":"fixture failure"},"usage":{"prompt_tokens":0,"completion_tokens":0,"cost":0.04}}`+"\n\n")
					} else if strings.Contains(kind, "error") {
						space := ""
						if kind == "error-space" {
							space = " "
						}
						fmt.Fprint(w, "data:"+space+`{"error":{"code":502,"message":"fixture failure"}}`+"\n\n")
					}
				})
				g.managed.streamWith.Store(&up)
				status, body, _ := sendManaged(t, g, surface, "acme/request", true, false, nil)
				spent, held := settledAccount(t, g)
				wantStatus, wantSpent := 502, int64(0)
				// No retry is configured: Messages follows chat's retry policy.
				wantCalls := 1
				if kind == "partial-error" {
					wantStatus, wantSpent, wantCalls = 200, 42000, 1
				}
				if kind == "reported-error" {
					wantSpent, wantCalls = 42000, 1
				}
				// Do not print settlement records: they carry credential fingerprints.
				t.Logf("status=%d calls=%d spent=%d held=%d record_count=%d", status, g.managed.calls(), spent, held, len(g.outbox("org-m")))
				if status != wantStatus || spent != wantSpent || held != 0 || g.managed.calls() != wantCalls {
					t.Fatalf("status=%d/%d calls=%d/%d spent=%d/%d held=%d body=%s", status, wantStatus, g.managed.calls(), wantCalls, spent, wantSpent, held, body)
				}
			})
		}
	}
}

func TestRecheckNonstreamEstimates(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, tc := range []struct {
			name, message string
			want          int64
		}{
			{"text", `{"role":"assistant","content":"hello there"}`, 48},
			{"tool", `{"role":"assistant","content":null,"tool_calls":[{"id":"call_fixture","type":"function","function":{"name":"lookup","arguments":"{}"}}]}`, 38},
			{"reasoning", `{"role":"assistant","content":"","reasoning_content":"hello there"}`, 48},
		} {
			t.Run(surface+"/"+tc.name, func(t *testing.T) {
				g := newManagedTestGateway(t, managedOptIn())
				installBillabilityModels(billabilityModels(t))(t, g)
				g.ledger.SetMicros("org-m", 1_000_000)
				up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
					w.Header().Set("Content-Type", "application/json")
					fmt.Fprintf(w, `{"model":%q,"choices":[{"index":0,"message":%s,"finish_reason":"stop"}]}`, model, tc.message)
				})
				g.managed.responseWith.Store(&up)
				status, _, _ := sendManaged(t, g, surface, "acme/token", false, false, nil)
				spent, held := settledAccount(t, g)
				recs := g.outbox("org-m")
				t.Logf("status=%d spent=%d held=%d record_count=%d", status, spent, held, len(recs))
				if status != 200 || spent != tc.want || held != 0 || len(recs) != 1 || recs[0]["usage_estimated"] != "1" {
					t.Fatalf("status=%d spent=%d want=%d held=%d record_count=%d; expected one estimated settlement", status, spent, tc.want, held, len(recs))
				}
			})
		}
	}
}

func TestRecheckEmptyUsageObject(t *testing.T) {
	for _, surface := range managedSurfaces {
		t.Run(surface, func(t *testing.T) {
			g := newManagedTestGateway(t, managedOptIn())
			installBillabilityModels(billabilityModels(t))(t, g)
			g.ledger.SetMicros("org-m", 1_000_000)
			up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
				w.Header().Set("Content-Type", "application/json")
				fmt.Fprintf(w, `{"model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"hello there"},"finish_reason":"stop"}],"usage":{}}`, model)
			})
			g.managed.responseWith.Store(&up)
			status, _, _ := sendManaged(t, g, surface, "acme/token", false, false, nil)
			spent, held := settledAccount(t, g)
			t.Logf("status=%d spent=%d held=%d record_count=%d", status, spent, held, len(g.outbox("org-m")))
			if status == 200 && spent == 0 {
				t.Error("answer with no reported counters or cost became a free call")
			}
		})
	}
}

func TestRecheckStreamPrefixIdentity(t *testing.T) {
	for _, surface := range managedSurfaces {
		t.Run(surface, func(t *testing.T) {
			g := newManagedTestGateway(t, managedOptIn())
			installBillabilityModels(billabilityModels(t))(t, g)
			g.ledger.SetMicros("org-m", 1_000_000)
			up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
				w.Header().Set("Content-Type", "text/event-stream")
				writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_prefix_identity","type":"function","function":{"name":"","arguments":""}}]}}]`)
				writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"name":"lookup","arguments":"{}"}}]}}]`)
				writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":8,"completion_tokens":2}`)
				fmt.Fprint(w, "data: [DONE]\n\n")
			})
			g.managed.streamWith.Store(&up)
			status, body, _ := sendManaged(t, g, surface, "acme/token", true, false, nil)
			spent, held := settledAccount(t, g)
			t.Logf("status=%d spent=%d held=%d body=%s", status, spent, held, body)
			if status != 200 || !strings.Contains(body, "call_prefix_identity") {
				t.Error("successful stream dropped the upstream tool call identity before the first usable content")
			}
		})
	}
}

func TestRecheckFailedServedEndpoint(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	all := billabilityModels(t)
	eps, err := catalog.ParseOpenRouterEndpoints("acme/token", []byte(`{"data":{"id":"acme/token","endpoints":[{"tag":"cheap","provider_name":"Cheap Compute","pricing":{"prompt":"0.000002","completion":"0.000010"}},{"tag":"expensive","provider_name":"Expensive Compute","pricing":{"prompt":"0.000004","completion":"0.000020"}}]}}`))
	if err != nil {
		t.Fatal(err)
	}
	all["acme/token"].Endpoints = eps
	installBillabilityModels(all)(t, g)
	g.ledger.SetMicros("org-m", 1_000_000)
	up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
		w.Header().Set("Content-Type", "text/event-stream")
		writeManagedChunk(w, model, `"provider":"Cheap Compute","choices":[],"usage":{"prompt_tokens":8,"completion_tokens":3}`)
		fmt.Fprint(w, "data: {\"error\":{\"code\":502,\"message\":\"fixture failure\"}}\n\n")
	})
	g.managed.streamWith.Store(&up)
	status, _, _ := sendManaged(t, g, "/v1/chat/completions", "acme/token", true, false, nil)
	spent, held := settledAccount(t, g)
	t.Logf("status=%d spent=%d held=%d record_count=%d", status, spent, held, len(g.outbox("org-m")))
	if status != 502 || g.managed.calls() != 1 || spent != 48 || held != 0 {
		t.Errorf("named failed endpoint should bill its own 48 micros, got status=%d calls=%d spent=%d held=%d", status, g.managed.calls(), spent, held)
	}
}

func TestRecheckPolicyCallableSet(t *testing.T) {
	off, on := false, true
	for _, tc := range []struct {
		name string
		p    tenant.ManagedModelsConfig
		want map[string]bool
	}{
		{"default", tenant.ManagedModelsConfig{}, map[string]bool{"acme/token": true, "acme/token:nitro": true, "acme/listed:free": true}},
		{"explicit-free", tenant.ManagedModelsConfig{AllowFree: &on}, map[string]bool{"acme/token": true, "acme/token:nitro": true, "acme/listed:free": true}},
		{"no-free", tenant.ManagedModelsConfig{AllowFree: &off}, map[string]bool{"acme/token": true, "acme/token:nitro": true}},
		{"deny-base", tenant.ManagedModelsConfig{DenyModels: []string{"acme/token"}}, map[string]bool{"acme/listed:free": true}},
		{"allow-base", tenant.ManagedModelsConfig{AllowModels: []string{"acme/token"}}, map[string]bool{"acme/token": true}},
		{"allow-glob-deny-base", tenant.ManagedModelsConfig{AllowModels: []string{"acme/*"}, DenyModels: []string{"acme/token"}}, map[string]bool{"acme/listed:free": true}},
		{"allow-variant", tenant.ManagedModelsConfig{AllowModels: []string{"acme/token:nitro"}}, map[string]bool{"acme/token:nitro": true}},
		{"deny-vendor", tenant.ManagedModelsConfig{DenyVendors: []string{"acme"}}, map[string]bool{}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			tc.p.Enabled = true
			org := &tenant.OrgConfig{ManagedModels: &tc.p}
			g := newManagedTestGateway(t, org)
			all := billabilityModels(t)
			small := map[string]*catalog.Model{}
			for _, id := range []string{"acme/token", "acme/token:nitro", "acme/listed:free"} {
				small[id] = all[id]
			}
			installBillabilityModels(small)(t, g)
			g.ledger.SetMicros("org-m", 100_000_000)
			list := managedListedIDs(t, g)
			for _, id := range []string{"acme/token", "acme/token:nitro", "acme/listed:free"} {
				_, listed := list[id]
				if listed != tc.want[id] {
					t.Errorf("listed %s=%t want=%t", id, listed, tc.want[id])
				}
				for _, surface := range managedSurfaces {
					for _, stream := range []bool{false, true} {
						for _, prefix := range []string{"", "openrouter/"} {
							before := g.managed.calls()
							status, _, _ := sendManaged(t, g, surface, prefix+id, stream, false, nil)
							want := 403
							if tc.want[id] {
								want = 200
							}
							if status != want {
								t.Errorf("%s %s stream=%t status=%d want=%d", surface, prefix+id, stream, status, want)
							}
							if want == 403 && g.managed.calls() != before {
								t.Error("denied call dispatched")
							}
						}
					}
				}
			}
			for _, row := range buildCatalogAccess(g.srv.handlers.catalog.Load(), org, 5, time.Now()).Data {
				if !row.Managed {
					continue
				}
				if !tc.want[row.ID] {
					t.Errorf("access offers uncallable %s", row.ID)
				}
				for _, surface := range managedSurfaces {
					status, _, _ := sendManaged(t, g, surface, row.ID, false, false, nil)
					if status != 200 {
						t.Errorf("access id=%s surface=%s status=%d", row.ID, surface, status)
					}
				}
			}
		})
	}
}
