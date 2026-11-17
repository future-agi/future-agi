package keyscope

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	ks "github.com/futureagi/agentcc-gateway/internal/keyscope"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

const (
	rawScoped   = "sk-agentcc-plugin-scoped"
	rawBudgeted = "sk-agentcc-plugin-budgeted"
	rawPlain    = "sk-agentcc-plugin-plain"
)

func newStore(t *testing.T, budgetUSD, period string) (*auth.KeyStore, map[string]*auth.APIKey) {
	t.Helper()
	md := func(extra map[string]string) map[string]string {
		m := map[string]string{"org_id": "org-1"}
		for k, v := range extra {
			m[k] = v
		}
		return m
	}
	store := auth.NewKeyStore(config.AuthConfig{Enabled: true, Keys: []config.AuthKeyConfig{
		{Name: "scoped", Key: rawScoped, Metadata: md(map[string]string{ks.MetaScopes: "mcp,models"})},
		{Name: "budgeted", Key: rawBudgeted, Metadata: md(map[string]string{
			ks.MetaScopes: "mcp,models", ks.MetaBudgetLimitUSD: budgetUSD, ks.MetaBudgetPeriod: period})},
		{Name: "plain", Key: rawPlain, Metadata: md(nil)},
	}})
	keys := map[string]*auth.APIKey{}
	for _, raw := range []string{rawScoped, rawBudgeted, rawPlain} {
		keys[raw] = store.Lookup(raw)
	}
	return store, keys
}

// admitted is the context the key gate leaves for key: its identity.
func admitted(t *testing.T, store *auth.KeyStore, raw string) context.Context {
	t.Helper()
	var ctx context.Context
	gate := middleware.KeyAuthScoped(store, true, nil, middleware.KeyScopes{MCPRoot: "/mcp"})
	gate(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) { ctx = r.Context() })).
		ServeHTTP(httptest.NewRecorder(), withKey(httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil), raw))
	if ctx == nil {
		t.Fatalf("the key gate did not admit %s on POST /v1/chat/completions", raw)
	}
	return ctx
}

func withKey(r *http.Request, raw string) *http.Request {
	r.Header.Set("Authorization", "Bearer "+raw)
	return r
}

func rcFor(key *auth.APIKey) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Metadata["auth_key_id"] = key.ID
	rc.Model = "gpt-4o"
	rc.EndpointType = "chat"
	return rc
}

func refusal(t *testing.T, err *models.APIError) (status int, code string, meta map[string]any) {
	t.Helper()
	w := httptest.NewRecorder()
	models.WriteError(w, err)
	var body struct {
		Error struct {
			Code     string         `json:"code"`
			Metadata map[string]any `json:"metadata"`
		} `json:"error"`
	}
	if jsonErr := json.Unmarshal(w.Body.Bytes(), &body); jsonErr != nil {
		t.Fatalf("refusal body: %v", jsonErr)
	}
	return w.Code, body.Error.Code, body.Error.Metadata
}

func TestScopedKeyTheGateDidNotAdmitIsRefused(t *testing.T) {
	store, keys := newStore(t, "1", "monthly")
	p := New(store, ks.NewSpend(nil))

	// /v1beta: no key gate, so no identity in the context.
	res := p.ProcessRequest(context.Background(), rcFor(keys[rawScoped]))
	if res.Error == nil {
		t.Fatal("a scoped key the key gate never saw reached the provider")
	}
	if status, code, _ := refusal(t, res.Error); status != http.StatusForbidden || code != ks.CodeScopeDenied {
		t.Fatalf("refusal %d %s, want 403 %s", status, code, ks.CodeScopeDenied)
	}

	// Admitted for another key: still refused.
	if res := p.ProcessRequest(admitted(t, store, rawPlain), rcFor(keys[rawScoped])); res.Error == nil {
		t.Fatal("a scoped key ran under another key's admission")
	}

	// Admitted for this key: through.
	if res := p.ProcessRequest(admitted(t, store, rawScoped), rcFor(keys[rawScoped])); res.Error != nil {
		t.Fatalf("an admitted scoped key was refused: %v", res.Error)
	}

	// An unscoped key needs no admission (the pipeline's own auth holds).
	if res := p.ProcessRequest(context.Background(), rcFor(keys[rawPlain])); res.Error != nil {
		t.Fatalf("an unscoped key was refused: %v", res.Error)
	}
}

func TestBudgetRefusesOnceThePeriodsSpendReachesIt(t *testing.T) {
	store, keys := newStore(t, "1.00", "monthly")
	p := New(store, ks.NewSpend(nil))
	now := time.Date(2026, 10, 21, 23, 0, 0, 0, time.UTC)
	p.now = func() time.Time { return now }
	ctx := admitted(t, store, rawBudgeted)
	key := keys[rawBudgeted]

	call := func(cost string) *models.APIError {
		rc := rcFor(key)
		if res := p.ProcessRequest(ctx, rc); res.Error != nil {
			return res.Error
		}
		rc.Metadata["cost"] = cost
		p.ProcessResponse(ctx, rc)
		return nil
	}
	if err := call("0.60"); err != nil {
		t.Fatalf("first call refused: %v", err)
	}
	if err := call("0.45"); err != nil {
		t.Fatalf("second call refused at $0.60 of $1.00: %v", err)
	}
	err := call("0.01")
	if err == nil {
		t.Fatal("a call at $1.05 of a $1.00 budget reached the provider")
	}
	status, code, meta := refusal(t, err)
	if status != http.StatusPaymentRequired || code != ks.CodeBudgetExhausted {
		t.Fatalf("refusal %d %s, want 402 %s", status, code, ks.CodeBudgetExhausted)
	}
	if meta["limit_source"] != models.LimitSourceKeyLimit || meta["scope"] != "key:"+key.ID ||
		meta["resets_at"] != "2026-11-01T00:00:00Z" || meta["available_usd"] != float64(0) {
		t.Fatalf("refusal metadata %v", meta)
	}

	// The next period starts at zero.
	now = time.Date(2026, 11, 1, 0, 0, 1, 0, time.UTC)
	if err := call("0.10"); err != nil {
		t.Fatalf("first call of the new period refused: %v", err)
	}
}

func TestCacheHitsAndUnpricedCallsSpendNothing(t *testing.T) {
	store, keys := newStore(t, "0.50", "daily")
	spend := ks.NewSpend(nil)
	p := New(store, spend)
	ctx := admitted(t, store, rawBudgeted)
	key := keys[rawBudgeted]
	b, _ := ks.ParseBudget(store.MetadataOf(key))

	hit := rcFor(key)
	hit.Flags.CacheHit = true
	hit.Metadata["cost"] = "5"
	p.ProcessResponse(ctx, hit)
	for _, cost := range []string{"", "0", "-1", "NaN", "abc"} {
		rc := rcFor(key)
		rc.Metadata["cost"] = cost
		p.ProcessResponse(ctx, rc)
	}
	if got := spend.Spent("org-1", key.ID, b, time.Now()); got != 0 {
		t.Fatalf("spent %d micros, want 0", got)
	}
}

func TestUnreadableBudgetRefusesEveryCall(t *testing.T) {
	for _, c := range []struct{ limit, period string }{{"x", "monthly"}, {"5", "yearly"}, {"-1", "daily"}, {"Inf", "weekly"}} {
		store, keys := newStore(t, c.limit, c.period)
		p := New(store, ks.NewSpend(nil))
		res := p.ProcessRequest(admitted(t, store, rawBudgeted), rcFor(keys[rawBudgeted]))
		if res.Error == nil {
			t.Fatalf("budget %q %q: a call went through an unreadable budget", c.limit, c.period)
		}
		if _, code, meta := refusal(t, res.Error); code != ks.CodeBudgetExhausted || meta["resets_at"] != nil {
			t.Fatalf("budget %q %q: code %s resets_at %v, want %s and null", c.limit, c.period, code, meta["resets_at"], ks.CodeBudgetExhausted)
		}
	}
}

// fakeShared is the shared counter store every replica reads.
type fakeShared struct{ usd map[string]float64 }

func (f *fakeShared) RecordSpend(org, level, key, period, _ string, cost float64) (float64, bool) {
	f.usd[org+"|"+level+"|"+key+"|"+period] += cost
	return f.usd[org+"|"+level+"|"+key+"|"+period], true
}
func (f *fakeShared) GetSpend(org, level, key, period string) (float64, map[string]float64, bool) {
	return f.usd[org+"|"+level+"|"+key+"|"+period], nil, true
}
func (f *fakeShared) Available() bool { return true }

func TestReplicasShareTheBudget(t *testing.T) {
	shared := &fakeShared{usd: map[string]float64{}}
	store, keys := newStore(t, "1", "monthly")
	a, b := New(store, ks.NewSpend(shared)), New(store, ks.NewSpend(shared))
	ctx := admitted(t, store, rawBudgeted)
	rc := rcFor(keys[rawBudgeted])
	rc.Metadata["cost"] = "1.00"
	a.ProcessResponse(ctx, rc)
	if res := b.ProcessRequest(ctx, rcFor(keys[rawBudgeted])); res.Error == nil {
		t.Fatal("replica b did not see replica a's spend")
	}
}
