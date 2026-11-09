package server

// Permanent round-five regressions through the composed gateway: a refused
// completion must leave an effective fence in both cap ledgers, so repairing
// only the malformed or lost field never reopens admission. Synthetic
// upstreams and Redis.
import (
	"context"
	"fmt"
	"net/http"
	"reflect"
	"sort"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Strict run ledger: spent becomes noncanonical between admission and
// completion. Both Complete attempts are refused, and FenceStrict must still
// mark the retained hold uncertain. The breaker stays closed, so the second
// request is refused by the ledger's fence, not by an open circuit.
func TestStrictRunMalformedSpentCompletionFencesEndToEnd(t *testing.T) {
	g, client, prefix := capComposedGateway(t)
	cfg := g.tenants.Get("org-m")
	limit := 1.0
	cfg.Budgets.Agents, cfg.Budgets.KeyIDs = nil, nil
	cfg.Routing.Agents.MaxRunUSD = &limit
	cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true}}
	g.tenants.Set("org-m", cfg)
	ctx := context.Background()
	rdb := client.Redis()
	base := prefix + "abud:{org-m}:malformed-spent:"
	answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
		// Synthetic restore drift after the strict hold, before completion.
		if err := rdb.Set(ctx, base+"spent", "0.0", 0).Err(); err != nil {
			t.Error(err)
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"run-spent","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"x"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500,"cost":0.3}}`, model)
	})
	g.operator.responseWith.Store(&answer)
	headers := map[string]string{"x-agentcc-run-id": "malformed-spent"}
	first := g.chat(t, chatBody("gpt-4o"), headers)
	if g.operator.calls() != 1 {
		t.Fatalf("not dispatched: code=%d calls=%d body=%s", first.Code, g.operator.calls(), first.Body)
	}
	holds := rdb.HKeys(ctx, base+"holds").Val()
	uncertain := rdb.SMembers(ctx, base+"limit:uncertain").Val()
	sort.Strings(holds)
	sort.Strings(uncertain)
	t.Logf("first=%d held=%q spent=%q holds=%v uncertain=%v terminal=%d available=%v", first.Code, rdb.Get(ctx, base+"held").Val(), rdb.Get(ctx, base+"spent").Val(), holds, uncertain, rdb.HLen(ctx, base+"terminal").Val(), client.Available())
	if len(holds) != 1 || rdb.HLen(ctx, base+"terminal").Val() != 0 || rdb.Get(ctx, base+"spent").Val() != "0.0" {
		t.Fatal("refused strict completion released the hold, recorded a terminal or rewrote spent")
	}
	if !reflect.DeepEqual(uncertain, holds) {
		t.Errorf("refused actual was not fenced: uncertainty=%v holds=%v", uncertain, holds)
	}
	if !client.Available() {
		t.Error("the fence failed: the breaker opened after the refused completions")
	}
	// Repairing only the counter must not reopen the scope while the actual is
	// unresolved.
	if err := rdb.Set(ctx, base+"spent", "0", 0).Err(); err != nil {
		t.Fatal(err)
	}
	g.operator.responseWith.Store(nil)
	second := g.chat(t, chatBody("gpt-4o"), headers)
	t.Logf("second=%d calls=%d body=%s", second.Code, g.operator.calls(), second.Body)
	if second.Code == http.StatusOK || g.operator.calls() != 1 {
		t.Error("counter-only repair reopened a strict run with an unresolved actual")
	}
}

// Daily ledger: after a refused completion was fenced, the bucket loses only
// its uncertainty counter while the malformed spend is repaired. The retained
// fence record must keep the bucket closed.
func TestCapDailyLostUncertaintyCounterEndToEnd(t *testing.T) {
	g, client, prefix := capComposedGateway(t)
	cfg := g.tenants.Get("org-m")
	cfg.Budgets.KeyIDs = nil
	cfg.Providers = map[string]*tenant.ProviderConfig{"operator": {APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{"gpt-4o"}, Enabled: true}}
	g.tenants.Set("org-m", cfg)
	ctx := context.Background()
	rdb := client.Redis()
	base := prefix + "bhold:{org-m}:" + time.Now().UTC().Format("2006-01-02") + ":"
	answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
		// Synthetic restore drift after the admission hold, before completion.
		if err := rdb.HSet(ctx, base+"state", "spent:agent:worker", "0.0").Err(); err != nil {
			t.Error(err)
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"daily-lost","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"x"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500,"cost":0.3}}`, model)
	})
	g.operator.responseWith.Store(&answer)
	first := g.chat(t, chatBody("gpt-4o"), nil)
	if first.Code != http.StatusOK || g.operator.calls() != 1 {
		t.Fatalf("not dispatched: code=%d calls=%d body=%s", first.Code, g.operator.calls(), first.Body)
	}
	if rdb.HLen(ctx, base+"holds").Val() != 1 || rdb.HLen(ctx, base+"uncertain").Val() != 1 || rdb.HGet(ctx, base+"state", "uncertain:agent:worker").Val() != "1" {
		t.Fatalf("control: refused completion was not fenced: state=%v fences=%v", rdb.HGetAll(ctx, base+"state").Val(), rdb.HGetAll(ctx, base+"uncertain").Val())
	}
	if err := rdb.HSet(ctx, base+"state", "spent:agent:worker", "0").Err(); err != nil {
		t.Fatal(err)
	}
	if err := rdb.HDel(ctx, base+"state", "uncertain:agent:worker").Err(); err != nil {
		t.Fatal(err)
	}
	g.operator.responseWith.Store(nil)
	second := g.chat(t, chatBody("gpt-4o"), nil)
	t.Logf("second=%d calls=%d state=%v fences=%v", second.Code, g.operator.calls(), rdb.HGetAll(ctx, base+"state").Val(), rdb.HGetAll(ctx, base+"uncertain").Val())
	if second.Code != http.StatusServiceUnavailable || g.operator.calls() != 1 {
		t.Error("lost uncertainty counter reopened a bucket with a retained fence record")
	}
	if rdb.HLen(ctx, base+"uncertain").Val() != 1 || rdb.HLen(ctx, base+"holds").Val() != 1 {
		t.Error("refused admission dropped the fence record or the reservation")
	}
}
