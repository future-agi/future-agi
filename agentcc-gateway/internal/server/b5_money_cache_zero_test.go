package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// These tests require TEST_REDIS_ADDR (and optionally TEST_REDIS_DB), through
// r7AgentRedis's isolated-prefix fixture. A SKIP is not settlement evidence.
// They exercise the HTTP handler and a local HTTP upstream, not the production
// wallet/drain. Redis account/outbox evidence does not prove downstream billing
// ingestion or log-webhook delivery; neither is wired by this fixture.
func TestB5MoneyCacheZeroRedisSettlement(t *testing.T) {
	t.Run("cache_miss_then_hit", func(t *testing.T) {
		g, client, prefix := b5MoneyCacheGateway(t, true)
		// Deliberately different from the catalogue's 1000*2 + 500*10 =
		// 7000 micros: the upstream reports 6000, plus 5% = 6300 billed.
		reported := 0.006
		g.managed.reportedCost.Store(&reported)
		body := `{"model":"acme/token","temperature":0,"messages":[{"role":"user","content":"synthetic cache money probe"}]}`
		before := b5MoneyCacheSnapshot(t, g, client, prefix, "org-m")
		b5MoneyCacheAccount(t, before, 0, 0)
		if len(before.records) != 0 {
			t.Fatalf("fresh outbox has %d records", len(before.records))
		}

		miss := g.chat(t, body, nil)
		missResponse := b5MoneyCacheResponse(t, miss)
		if miss.Header().Get("x-agentcc-cache") != "miss" || g.managed.calls() != 1 {
			t.Fatalf("first request: cache=%q calls=%d; want miss and one upstream call", miss.Header().Get("x-agentcc-cache"), g.managed.calls())
		}
		if miss.Header().Get("x-agentcc-cost") != "0.006300" || missResponse.Usage.Cost == nil || *missResponse.Usage.Cost != 0.0063 {
			t.Errorf("first response must price the provider's 6000 micros plus 300 markup: header=%q body=%s", miss.Header().Get("x-agentcc-cost"), miss.Body.String())
		}
		beforeHit := b5MoneyCacheSnapshot(t, g, client, prefix, "org-m")
		b5MoneyCacheAccount(t, beforeHit, 6300, 0)
		if len(beforeHit.records) != 1 {
			t.Fatalf("miss settlements=%v; want exactly one", beforeHit.records)
		}
		b5MoneyCacheRecord(t, beforeHit.records[0], "provider_reported", 6000, 300, 6300)
		if beforeHit.records[0]["kind"] != "spend" || b5MoneyCacheMicros(t, beforeHit.records[0], "hold_micros") <= 0 {
			t.Errorf("paid miss must settle a positive hold: %v", beforeHit.records[0])
		}

		// No cache insertion or CacheHit flag assignment: the first HTTP
		// response must populate the actual plugin's cache.
		hit := g.chat(t, body, nil)
		hitResponse := b5MoneyCacheResponse(t, hit)
		if hit.Header().Get("x-agentcc-cache") != "hit_exact" || g.managed.calls() != 1 {
			t.Fatalf("second request: cache=%q calls=%d; want hit_exact without another upstream call", hit.Header().Get("x-agentcc-cache"), g.managed.calls())
		}
		if _, present := hit.Header()[http.CanonicalHeaderKey("x-agentcc-cost")]; present || hitResponse.Usage.Cost != nil {
			t.Errorf("cache hit leaked the stored charge: header=%q body=%s", hit.Header().Get("x-agentcc-cost"), hit.Body.String())
		}
		if hitResponse.Model != missResponse.Model || !reflect.DeepEqual(hitResponse.Choices, missResponse.Choices) {
			t.Errorf("cache hit changed the stored answer: miss=%s hit=%s", miss.Body.String(), hit.Body.String())
		}
		afterHit := b5MoneyCacheSnapshot(t, g, client, prefix, "org-m")
		b5MoneyCacheAccount(t, afterHit, 6300, 0)
		if afterHit.account.SpentMicros-beforeHit.account.SpentMicros != 0 || afterHit.account.AvailableMicros() != beforeHit.account.AvailableMicros() {
			t.Errorf("hit moved money: before=%+v after=%+v", beforeHit.account, afterHit.account)
		}
		// A hit can short-circuit before reservation (no settlement), or
		// release a reserved hold (one zero void). Neither may emit a spend
		// or rewrite the first call's settlement. If there is no record,
		// the HTTP fixture exposes no cache cost_source or component fields;
		// the omitted client cost is NOT treated as proof of their values.
		if len(afterHit.records) < 1 || len(afterHit.records) > 2 {
			t.Fatalf("hit settlements=%v; want the original spend and at most one void", afterHit.records)
		}
		if !reflect.DeepEqual(afterHit.records[0], beforeHit.records[0]) {
			t.Errorf("hit changed the miss settlement: before=%v after=%v", beforeHit.records[0], afterHit.records[0])
		}
		if len(afterHit.records) == 2 {
			rec := afterHit.records[1]
			b5MoneyCacheRecord(t, rec, "cache_hit", 0, 0, 0)
			if rec["kind"] != "void" || rec["request_id"] == beforeHit.records[0]["request_id"] {
				t.Errorf("hit must void its own request, never duplicate the miss: %v", rec)
			}
		} else {
			t.Log("evidence boundary: no cache-hit settlement; cache_hit source and zero cost components are not exposed by this HTTP fixture")
		}
	})

	t.Run("provider_reported_zero_with_priced_catalog", func(t *testing.T) {
		g, client, prefix := b5MoneyCacheGateway(t, false)
		// This model has positive, known catalogue pricing. A positive
		// provider-cost control proves this path settles reported cost,
		// rather than simply charging nothing for every response.
		reported := 0.006
		g.managed.reportedCost.Store(&reported)
		positive := g.chat(t, chatBody("acme/token"), nil)
		positiveResponse := b5MoneyCacheResponse(t, positive)
		if g.managed.calls() != 1 || positiveResponse.Usage.Cost == nil || *positiveResponse.Usage.Cost != 0.0063 {
			t.Fatalf("positive control: calls=%d body=%s; want one priced call", g.managed.calls(), positive.Body.String())
		}
		beforeZero := b5MoneyCacheSnapshot(t, g, client, prefix, "org-m")
		b5MoneyCacheAccount(t, beforeZero, 6300, 0)
		if len(beforeZero.records) != 1 {
			t.Fatalf("positive control settlements=%v; want one", beforeZero.records)
		}
		b5MoneyCacheRecord(t, beforeZero.records[0], "provider_reported", 6000, 300, 6300)
		if beforeZero.records[0]["kind"] != "spend" {
			t.Errorf("positive control must spend: %v", beforeZero.records[0])
		}

		zero := 0.0
		g.managed.reportedCost.Store(&zero)
		response := g.chat(t, chatBody("acme/token"), nil)
		zeroResponse := b5MoneyCacheResponse(t, response)
		if g.managed.calls() != 2 {
			t.Fatalf("reported-zero response did not make its own provider call: calls=%d", g.managed.calls())
		}
		if zeroResponse.Usage.Cost == nil || *zeroResponse.Usage.Cost != 0 || response.Header().Get("x-agentcc-cost") != "0.000000" {
			t.Errorf("explicit provider zero must remain a known zero, not missing cost or catalogue fallback: header=%q body=%s", response.Header().Get("x-agentcc-cost"), response.Body.String())
		}
		afterZero := b5MoneyCacheSnapshot(t, g, client, prefix, "org-m")
		b5MoneyCacheAccount(t, afterZero, 6300, 0)
		if len(afterZero.records) != 2 {
			t.Fatalf("reported-zero settlements=%v; want the positive control plus one zero settlement", afterZero.records)
		}
		if !reflect.DeepEqual(beforeZero.records[0], afterZero.records[0]) {
			t.Errorf("reported zero rewrote the positive control: before=%v after=%v", beforeZero.records[0], afterZero.records[0])
		}
		rec := afterZero.records[1]
		b5MoneyCacheRecord(t, rec, "provider_reported", 0, 0, 0)
		// Legacy zero settlements may void; inclusive settlements spend
		// an explicit zero. This assertion does not change that policy.
		if rec["kind"] != "spend" && rec["kind"] != "void" {
			t.Errorf("reported zero has no terminal settlement kind: %v", rec)
		}
		if b5MoneyCacheMicros(t, rec, "hold_micros") <= 0 || rec["request_id"] == beforeZero.records[0]["request_id"] || rec["catalog_snapshot_id"] == "" || rec["usage_estimated"] != "0" {
			t.Errorf("reported zero must release its own positive hold with measured usage and the admitted priced catalogue: %v", rec)
		}
		if afterZero.account.SpentMicros-beforeZero.account.SpentMicros != 0 || afterZero.account.AvailableMicros() != beforeZero.account.AvailableMicros() {
			t.Errorf("reported zero moved money: before=%+v after=%+v", beforeZero.account, afterZero.account)
		}

		// The same upstream zero override cannot make an unknown zero-price
		// model callable. This control must fail admission, not silently
		// turn an unpriced request into a successful provider-reported zero.
		unknown := g.chat(t, chatBody("acme/unknown-zero"), nil)
		if unknown.Code != http.StatusForbidden || g.managed.calls() != 2 {
			t.Errorf("unknown-price control: status=%d calls=%d body=%s; want 403 before dispatch", unknown.Code, g.managed.calls(), unknown.Body.String())
		}
		afterUnknown := b5MoneyCacheSnapshot(t, g, client, prefix, "org-m")
		if !reflect.DeepEqual(afterZero, afterUnknown) {
			t.Errorf("unknown-price refusal moved money or emitted a settlement: before=%+v after=%+v", afterZero, afterUnknown)
		}
	})
}

type b5MoneyCacheState struct {
	account billing.Account
	records []map[string]string
}

// This only composes the existing managed HTTP and isolated Redis fixtures;
// the real cache plugin, cost plugin and credit hooks perform the work.
func b5MoneyCacheGateway(t *testing.T, enableCache bool) (*managedTestGateway, *redisstate.Client, string) {
	t.Helper()
	prefix := fmt.Sprintf("test-b5-money-cache-%d:", time.Now().UnixNano())
	client := r7AgentRedis(t, prefix)
	org := managedOptIn()
	opts := managedGatewayOptions{}
	if enableCache {
		org.Cache = &tenant.CacheConfig{Enabled: true}
		opts.extraPlugins = []pipeline.Plugin{cacheplugin.NewFromStore(cache.NewStore(10), time.Minute)}
	}
	g := newManagedTestGatewayWith(t, org, opts)
	installBillabilityModels(billabilityModels(t))(t, g)
	ledger := creditsplugin.NewOrgLedger(redisstate.NewManagedCreditStore(client, prefix))
	g.ledger = ledger
	g.credits.SetOrgLedger(ledger, true)
	g.srv.EnableManaged(ManagedSettings{Catalog: g.srv.handlers.catalog, Ledger: ledger, MarkupPercent: 5, RequireCredits: true})
	for _, id := range []string{"org-m", "org-b5-unrelated"} {
		if _, err := ledger.Grant(context.Background(), id, billing.GrantPush{GrantedTotalMicros: 1_000_000, Version: 1}); err != nil {
			t.Fatal(err)
		}
	}
	other := b5MoneyCacheSnapshot(t, g, client, prefix, "org-b5-unrelated")
	b5MoneyCacheAccount(t, other, 0, 0)
	t.Cleanup(func() {
		after := b5MoneyCacheSnapshot(t, g, client, prefix, "org-b5-unrelated")
		if !reflect.DeepEqual(other, after) {
			t.Errorf("another org changed: before=%+v after=%+v", other, after)
		}
		if g.operator.calls() != 0 {
			t.Errorf("unexpected operator upstream calls: %d", g.operator.calls())
		}
	})
	return g, client, prefix
}

func b5MoneyCacheSnapshot(t *testing.T, g *managedTestGateway, client *redisstate.Client, prefix, org string) b5MoneyCacheState {
	t.Helper()
	state := b5MoneyCacheState{account: g.account(t, org)}
	records, err := client.Redis().XRange(context.Background(), prefix+"mc:{"+org+"}:outbox", "-", "+").Result()
	if err != nil {
		t.Fatal(err)
	}
	for _, record := range records {
		values := make(map[string]string, len(record.Values))
		for key, value := range record.Values {
			values[key] = fmt.Sprint(value)
		}
		state.records = append(state.records, values)
	}
	return state
}

func b5MoneyCacheAccount(t *testing.T, state b5MoneyCacheState, spent, held int64) {
	t.Helper()
	if state.account.SpentMicros != spent || state.account.HeldMicros != held || state.account.AvailableMicros() != 1_000_000-spent-held {
		t.Errorf("account=%+v; want spent=%d held=%d available=%d", state.account, spent, held, 1_000_000-spent-held)
	}
	var billed int64
	for _, rec := range state.records {
		billed += b5MoneyCacheMicros(t, rec, "billed_micros")
	}
	if billed != spent || billed != state.account.SpentMicros {
		t.Errorf("independent outbox sum=%d account spent=%d; want %d", billed, state.account.SpentMicros, spent)
	}
}

func b5MoneyCacheMicros(t *testing.T, rec map[string]string, field string) int64 {
	t.Helper()
	n, err := strconv.ParseInt(rec[field], 10, 64)
	if err != nil {
		t.Fatalf("missing/invalid %s=%q in settlement %v: %v", field, rec[field], rec, err)
	}
	return n
}

func b5MoneyCacheRecord(t *testing.T, rec map[string]string, source string, upstream, markup, billed int64) {
	t.Helper()
	if rec["org_id"] != "org-m" || rec["request_id"] == "" || rec["cost_source"] != source {
		t.Errorf("settlement identity/source=%v; want org-m, a request ID and %s", rec, source)
	}
	for field, want := range map[string]int64{"upstream_micros": upstream, "markup_micros": markup, "billed_micros": billed} {
		if got := b5MoneyCacheMicros(t, rec, field); got != want {
			t.Errorf("%s=%d; want %d in %v", field, got, want, rec)
		}
	}
	if upstream+markup != billed {
		t.Fatal("invalid test expectation: upstream plus markup must equal billed")
	}
}

func b5MoneyCacheResponse(t *testing.T, w *httptest.ResponseRecorder) models.ChatCompletionResponse {
	t.Helper()
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-billing-mode") != "managed" {
		t.Fatalf("status=%d billing=%q body=%s; want successful managed response", w.Code, w.Header().Get("x-agentcc-billing-mode"), w.Body.String())
	}
	var response models.ChatCompletionResponse
	if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if response.Usage == nil || response.Usage.PromptTokens != 1000 || response.Usage.CompletionTokens != 500 || response.Usage.TotalTokens != 1500 || len(response.Choices) != 1 || response.Choices[0].FinishReason != "stop" {
		t.Fatalf("missing successful fixture answer or measured usage: %s", w.Body.String())
	}
	return response
}
