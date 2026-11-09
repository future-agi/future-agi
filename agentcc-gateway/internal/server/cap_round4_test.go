package server

// Permanent round-four verifier regressions: malformed ledger state that
// appears between admission and completion. Synthetic upstreams and Redis.
import (
	"context"
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A daily completion refused for a malformed spend counter must leave the
// reservation in place so the error-path Fence actually fences it. Correcting
// only the counter must not reopen capacity for the unaccounted work.
func TestSolR4DailyCompletionFailureActuallyFences(t *testing.T) {
	g, client, prefix := capComposedGateway(t)
	cfg := g.tenants.Get("org-m")
	cfg.Budgets.KeyIDs = nil
	cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true}}
	g.tenants.Set("org-m", cfg)
	ctx := context.Background()
	base := prefix + "bhold:{org-m}:" + time.Now().UTC().Format("2006-01-02") + ":"
	answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
		// Synthetic restore drift after the admission hold, before completion.
		if err := client.Redis().HSet(ctx, base+"state", "spent:agent:worker", "300000.0").Err(); err != nil {
			t.Error(err)
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"daily-partial","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"x"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500,"cost":0.3}}`, model)
	})
	g.operator.responseWith.Store(&answer)
	first := g.chat(t, chatBody("gpt-4o"), nil)
	if first.Code != http.StatusOK || g.operator.calls() != 1 {
		t.Fatalf("not dispatched: code=%d calls=%d body=%s", first.Code, g.operator.calls(), first.Body)
	}
	rdb := client.Redis()
	holds := rdb.HLen(ctx, base+"holds").Val()
	uncertain := rdb.HLen(ctx, base+"uncertain").Val()
	terminal := rdb.HLen(ctx, base+"terminal").Val()
	t.Logf("after rejected completion: ledger=%v holds=%d uncertain=%d terminal=%d", rdb.HGetAll(ctx, base+"state").Val(), holds, uncertain, terminal)
	if holds != 1 || uncertain != 1 || terminal != 0 {
		t.Error("refused Complete lost its hold instead of fencing it")
	}
	if got := rdb.HGet(ctx, base+"state", "uncertain:agent:worker").Val(); got != "1" {
		t.Errorf("fence did not raise the bucket's uncertainty: %q", got)
	}
	// Correcting only the malformed counter must not erase unresolved work.
	if err := rdb.HSet(ctx, base+"state", "spent:agent:worker", "0").Err(); err != nil {
		t.Fatal(err)
	}
	g.operator.responseWith.Store(nil)
	second := g.chat(t, chatBody("gpt-4o"), nil)
	t.Logf("after counter-only correction: code=%d calls=%d ledger=%v", second.Code, g.operator.calls(), rdb.HGetAll(ctx, base+"state").Val())
	if second.Code != http.StatusServiceUnavailable || g.operator.calls() != 1 {
		t.Error("unaccounted billable work reopened after the failed completion and failed fence")
	}
}

// The strict run counterpart: an uncertainty set of the wrong Redis type that
// appears after admission must make completion refuse without releasing the
// hold or recording spend, and no further work may be dispatched.
func TestStrictRunWrongTypeAuxiliaryStateEndToEnd(t *testing.T) {
	g, client, prefix := capComposedGateway(t)
	cfg := g.tenants.Get("org-m")
	limit := 1.0
	cfg.Budgets.Agents, cfg.Budgets.KeyIDs = nil, nil
	cfg.Routing.Agents.MaxRunUSD = &limit
	cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true}}
	g.tenants.Set("org-m", cfg)
	ctx := context.Background()
	rdb := client.Redis()
	base := prefix + "abud:{org-m}:aux-type:"
	observed := make(chan [3]string, 1)
	answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
		// Synthetic structural corruption after the strict hold, before completion.
		select {
		case observed <- [3]string{rdb.Get(ctx, base+"held").Val(), rdb.Get(ctx, base+"spent").Val(), rdb.Get(ctx, base+"limit").Val()}:
		default:
		}
		if err := rdb.Set(ctx, base+"limit:uncertain", "4000.0", 0).Err(); err != nil {
			t.Error(err)
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"run-aux","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"x"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500,"cost":0.3}}`, model)
	})
	g.operator.responseWith.Store(&answer)
	headers := map[string]string{"x-agentcc-run-id": "aux-type"}
	first := g.chat(t, chatBody("gpt-4o"), headers)
	if g.operator.calls() != 1 {
		t.Fatalf("not dispatched: code=%d calls=%d body=%s", first.Code, g.operator.calls(), first.Body)
	}
	var atDispatch [3]string
	select {
	case atDispatch = <-observed:
	default:
		t.Fatal("upstream did not observe the strict hold")
	}
	after := [3]string{rdb.Get(ctx, base+"held").Val(), rdb.Get(ctx, base+"spent").Val(), rdb.Get(ctx, base+"limit").Val()}
	holds, terminal := rdb.HLen(ctx, base+"holds").Val(), rdb.HLen(ctx, base+"terminal").Val()
	t.Logf("first=%d at-dispatch(held,spent,limit)=%v after=%v holds=%d terminal=%d uncertainType=%s", first.Code, atDispatch, after, holds, terminal, rdb.Type(ctx, base+"limit:uncertain").Val())
	if atDispatch[0] == "" || atDispatch[0] == "0" {
		t.Fatalf("strict admission did not hold: %v", atDispatch)
	}
	if after != atDispatch || holds != 1 || terminal != 0 {
		t.Error("refused strict completion released the hold, recorded spend or changed the limit")
	}
	g.operator.responseWith.Store(nil)
	second := g.chat(t, chatBody("gpt-4o"), headers)
	t.Logf("second=%d calls=%d body=%s", second.Code, g.operator.calls(), second.Body)
	if second.Code == http.StatusOK || g.operator.calls() != 1 {
		t.Error("work dispatched over a structurally malformed strict run ledger")
	}
}
