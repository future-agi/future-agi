package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A present envelope is not evidence for the token fields it omits. Dispatch
// must remain fenced unless a reported charge or complete actual metering exists.
func TestSolR2IncompleteUsageRetainsReservation(t *testing.T) {
	for _, managed := range []bool{false, true} {
		for _, runOnly := range []bool{false, true} {
			for _, usage := range []string{`{}`, `{"total_tokens":1500}`, `{"prompt_tokens":1000}`, `{"completion_tokens":500}`, `{"prompt_tokens":null,"completion_tokens":0}`, `{"prompt_tokens":0,"completion_tokens":null}`, `{"cost":null}`, `{"prompt_tokens":-1,"completion_tokens":0}`} {
				t.Run(fmt.Sprintf("managed=%v/run=%v/usage=%s", managed, runOnly, usage), func(t *testing.T) {
					g, client, prefix := capComposedGateway(t)
					cfg := g.tenants.Get("org-m")
					limit := 1.0
					headers := map[string]string{}
					if runOnly {
						cfg.Budgets.Agents = nil
						cfg.Budgets.KeyIDs = nil
						cfg.Routing.Agents.MaxRunUSD = &limit
						headers["x-agentcc-run-id"] = "incomplete-usage"
					}
					upstream, model := g.managed, "openai/gpt-6-sol"
					if !managed {
						cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true}}
						upstream, model = g.operator, "gpt-4o"
					}
					g.tenants.Set("org-m", cfg)
					answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
						w.Header().Set("Content-Type", "application/json")
						fmt.Fprintf(w, `{"id":"accepted","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"billable work with incomplete metering"},"finish_reason":"stop"}],"usage":%s}`, model, usage)
					})
					upstream.responseWith.Store(&answer)
					first := g.chat(t, chatBody(model), headers)
					if upstream.calls() != 1 {
						t.Fatalf("positive control: status=%d calls=%d body=%s", first.Code, upstream.calls(), first.Body)
					}
					state := prefix + "bhold:{org-m}:" + time.Now().UTC().Format("2006-01-02") + ":state"
					if runOnly {
						base := prefix + "abud:{org-m}:incomplete-usage:"
						t.Logf("BEFORE-NEXT first=%d held=%s spent=%s", first.Code, client.Redis().Get(context.Background(), base+"held").Val(), client.Redis().Get(context.Background(), base+"spent").Val())
					} else {
						t.Logf("BEFORE-NEXT first=%d ledger=%v", first.Code, client.Redis().HGetAll(context.Background(), state).Val())
					}
					resolve := capIncompleteResolution(t, client, prefix, runOnly)
					upstream.responseWith.Store(nil)
					second := g.chat(t, chatBody(model), headers)
					if second.Code != http.StatusServiceUnavailable || !strings.Contains(second.Body.String(), "budget_state_unavailable") {
						t.Errorf("incomplete outcome must fail closed: %d %s", second.Code, second.Body)
					}
					if runOnly {
						state = prefix + "abud:{org-m}:incomplete-usage:"
						t.Logf("first=%d second=%d calls=%d held=%s spent=%s", first.Code, second.Code, upstream.calls(), client.Redis().Get(context.Background(), state+"held").Val(), client.Redis().Get(context.Background(), state+"spent").Val())
					} else {
						t.Logf("first=%d second=%d calls=%d ledger=%v", first.Code, second.Code, upstream.calls(), client.Redis().HGetAll(context.Background(), state).Val())
					}
					if upstream.calls() != 1 {
						t.Errorf("incomplete metering reopened admission: first=%d second=%d calls=%d", first.Code, second.Code, upstream.calls())
					}
					walletBefore := g.account(t, "org-m")
					resolve() // simulate later authoritative reconciliation, not a wallet debit
					walletAfter := g.account(t, "org-m")
					if walletBefore.SpentMicros != walletAfter.SpentMicros || walletBefore.HeldMicros != walletAfter.HeldMicros {
						t.Fatal("cap reconciliation changed wallet charging")
					}
					third := g.chat(t, chatBody(model), headers)
					if third.Code != http.StatusOK || upstream.calls() != 2 {
						t.Fatalf("late authoritative actual did not reopen admission: code=%d calls=%d body=%s", third.Code, upstream.calls(), third.Body)
					}
				})
			}
		}
	}
}

// Assert the actual Redis reservation, not merely a refusal from another gate.
// Return an idempotent late-actual resolution for the originally admitted nonce.
func capIncompleteResolution(t *testing.T, client *redisstate.Client, prefix string, runOnly bool) func() {
	t.Helper()
	ctx := context.Background()
	rdb := client.Redis()
	if runOnly {
		base := prefix + "abud:{org-m}:incomplete-usage:"
		holds := rdb.HKeys(ctx, base+"holds").Val()
		if len(holds) != 1 || rdb.Get(ctx, base+"held").Val() == "0" || rdb.Get(ctx, base+"spent").Val() != "0" || rdb.SCard(ctx, base+"limit:uncertain").Val() != 1 || rdb.HLen(ctx, base+"terminal").Val() != 0 {
			t.Fatalf("incomplete run usage did not retain exactly one fenced hold: holds=%v held=%s spent=%s", holds, rdb.Get(ctx, base+"held").Val(), rdb.Get(ctx, base+"spent").Val())
		}
		return func() {
			for range 2 {
				res, err := redisstate.NewAgentBudgetStore(client, prefix).Complete(ctx, "org-m", "incomplete-usage", holds[0], 1, time.Hour)
				if err != nil || res.HeldMicros != 0 || res.SpentMicros != 1 {
					t.Fatalf("late strict actual: %+v %v", res, err)
				}
			}
		}
	}
	day := time.Now().UTC().Format("2006-01-02")
	base := prefix + "bhold:{org-m}:" + day + ":"
	holds := rdb.HGetAll(ctx, base+"holds").Val()
	if len(holds) != 1 || rdb.HLen(ctx, base+"uncertain").Val() != 1 || rdb.HLen(ctx, base+"terminal").Val() != 0 {
		t.Fatalf("incomplete daily usage lost hold or invented terminal: holds=%v", holds)
	}
	var nonce string
	var actual []redisstate.CapBucket
	for id, raw := range holds {
		nonce = id
		if err := json.Unmarshal([]byte(raw), &actual); err != nil {
			t.Fatal(err)
		}
	}
	for i, b := range actual {
		if rdb.HGet(ctx, base+"state", "held:"+b.Level).Val() == "0" || rdb.HGet(ctx, base+"state", "spent:"+b.Level).Val() != "0" || rdb.HGet(ctx, base+"state", "uncertain:"+b.Level).Val() != "1" {
			t.Fatalf("incomplete daily usage settled instead of fencing: %s", b.Level)
		}
		actual[i] = redisstate.CapBucket{Level: b.Level, Amount: 1}
	}
	return func() {
		for range 2 {
			if err := redisstate.NewCapHoldStore(client, prefix).Complete(ctx, "org-m", day, nonce, actual); err != nil {
				t.Fatal(err)
			}
		}
		for _, b := range actual {
			if rdb.HGet(ctx, base+"state", "held:"+b.Level).Val() != "0" || rdb.HGet(ctx, base+"state", "spent:"+b.Level).Val() != "1" || rdb.HGet(ctx, base+"state", "uncertain:"+b.Level).Val() != "0" {
				t.Fatalf("late daily actual did not settle exactly once: %s", b.Level)
			}
		}
	}
}

func TestSolR2UnknownMatrixAndUnsupportedStreaming(t *testing.T) {
	for _, managed := range []bool{false, true} {
		for _, mode := range []string{"missing", "lost-response", "fallback-error", "stream"} {
			t.Run(fmt.Sprintf("managed=%v/%s", managed, mode), func(t *testing.T) {
				g, _, _ := capComposedGateway(t)
				cfg := g.tenants.Get("org-m")
				upstream, model := g.managed, "openai/gpt-6-sol"
				if !managed {
					cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true}}
					upstream, model = g.operator, "gpt-4o"
				}
				if mode == "fallback-error" {
					cfg.Routing.ModelFallbacks = map[string][]string{model: {"qwen/qwen3.7-flash"}}
				}
				g.tenants.Set("org-m", cfg)
				answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
					switch mode {
					case "lost-response":
						conn, _, err := w.(http.Hijacker).Hijack()
						if err != nil {
							panic(err)
						}
						conn.Close()
					case "fallback-error":
						w.WriteHeader(500)
						fmt.Fprint(w, `{"error":{"message":"unknown outcome","type":"server_error"}}`)
					default:
						w.Header().Set("Content-Type", "application/json")
						fmt.Fprintf(w, `{"id":"accepted","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"accepted"},"finish_reason":"stop"}]}`, model)
					}
				})
				upstream.responseWith.Store(&answer)
				body := chatBody(model)
				if mode == "stream" {
					body = strings.TrimSuffix(body, "}") + `,"stream":true}`
				}
				first := g.chat(t, body, nil)
				if mode == "stream" {
					if upstream.calls() != 0 || first.Code != http.StatusPaymentRequired {
						t.Fatalf("stream dispatched: code=%d calls=%d", first.Code, upstream.calls())
					}
					return
				}
				if upstream.calls() != 1 {
					t.Fatalf("no positive control: %d %s calls=%d", first.Code, first.Body, upstream.calls())
				}
				if g.managed.calls()+g.operator.calls() != 1 {
					t.Fatal("unsupported fallback dispatched")
				}
				upstream.responseWith.Store(nil)
				second := g.chat(t, chatBody(model), nil)
				if upstream.calls() != 1 {
					t.Errorf("unknown %s reopened: first=%d second=%d calls=%d", mode, first.Code, second.Code, upstream.calls())
				}
			})
		}
	}
}

func TestSolR2ExplicitZeroAndReportedChargeRemainUsable(t *testing.T) {
	for _, managed := range []bool{false, true} {
		for _, runOnly := range []bool{false, true} {
			for _, usage := range []string{`{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}`, `{"cost":0}`, `{"cost":0.007}`} {
				t.Run(fmt.Sprintf("managed=%v/run=%v/usage=%s", managed, runOnly, usage), func(t *testing.T) {
					g, _, _ := capComposedGateway(t)
					cfg := g.tenants.Get("org-m")
					headers := map[string]string{}
					if runOnly {
						limit := 1.0
						cfg.Budgets.Agents, cfg.Budgets.KeyIDs = nil, nil
						cfg.Routing.Agents.MaxRunUSD = &limit
						headers["x-agentcc-run-id"] = "reported-usage"
					}
					upstream, model := g.managed, "openai/gpt-6-sol"
					if !managed {
						cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true}}
						upstream, model = g.operator, "gpt-4o"
					}
					g.tenants.Set("org-m", cfg)
					answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
						w.Header().Set("Content-Type", "application/json")
						fmt.Fprintf(w, `{"id":"accepted","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"reported accounting"},"finish_reason":"stop"}],"usage":%s}`, model, usage)
					})
					upstream.responseWith.Store(&answer)
					first := g.chat(t, chatBody(model), headers)
					upstream.responseWith.Store(nil)
					second := g.chat(t, chatBody(model), headers)
					if first.Code != http.StatusOK || second.Code != http.StatusOK || upstream.calls() != 2 {
						t.Fatalf("authoritative settlement refused: first=%d next=%d calls=%d", first.Code, second.Code, upstream.calls())
					}
				})
			}
		}
	}
}
