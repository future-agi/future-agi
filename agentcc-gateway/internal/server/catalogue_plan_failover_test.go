package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The provider-failover dimension of TestCAT2PlanDispatchMatrix. A request for
// cat2FailoverAlias is routed to its first target's model_override
// (cat2FailoverFirst). That target fails, and provider failover moves the
// request to the second target, whose own model_override (cat2FailoverLast)
// rewrites the model the request is sent as, before the request's fallback
// plan is resolved. The plan must still be the one admission judged for the
// request the caller made: its own models[] less the alias and the first hop,
// else a configured chain by presence under the alias, else under the first
// hop, and never a chain under the failover target's model.
//
// Only a key that may use the gateway's providers runs provider failover over
// targets with their own model_override (useProviderFailover: an internal
// key), and such a key is exempt from the mixed-catalogue fence
// (mixedCataloguePlan), so no cell here is refused at admission. Every cell
// instead checks the org's whole charge ledger after the request against the
// ledger before it (cat2ChargeLedger), served or not, and that no hold is
// left.
const (
	cat2FailoverAlias    = "cat2-failover/alias"
	cat2FailoverFirst    = "cat2-failover/first"
	cat2FailoverLast     = "cat2-failover/last"
	cat2FailoverSpare    = "cat2-failover/spare"
	cat2FailoverConflict = "cat2-failover/conflict"
	cat2FailoverBearer   = "sk-cat2-failover-synthetic"
)

// cat2FailoverCell is one provider-failover cell. key names, for the org and
// node sources, the identity the configured chain is keyed by: requested (the
// alias), hop (the first target's model, which a missing requested entry
// inherits), target (the failover target's model, never the request's own
// identity) or explicit-empty (an empty entry under the alias, over a chain
// under the first hop). A request-source cell declares models[] and keeps a
// conflicting node chain under both of its identities, which the declaration
// replaces.
type cat2FailoverCell struct {
	source   string
	key      string
	declared []string
	stream   bool
}

func (c cat2FailoverCell) name() string {
	return fmt.Sprintf("source=%s/failover=model-override/key=%s/declared=%s/stream=%t", c.source, c.key, strings.Join(c.declared, "+"), c.stream)
}

// cat2FailoverCells crosses the plan source with the chain key (or the
// declared list) and the streaming mode. The declared lists are the first hop
// then a spare, a spare alone, the alias then a spare, and the first hop
// alone, which leaves the request no plan at all.
func cat2FailoverCells() []cat2FailoverCell {
	var cells []cat2FailoverCell
	for _, stream := range []bool{false, true} {
		for _, declared := range [][]string{
			{cat2FailoverFirst, cat2FailoverSpare},
			{cat2FailoverSpare},
			{cat2FailoverAlias, cat2FailoverSpare},
			{cat2FailoverFirst},
		} {
			cells = append(cells, cat2FailoverCell{source: cat2SourceRequest, key: "declared", declared: declared, stream: stream})
		}
		for _, source := range []string{cat2SourceOrg, cat2SourceNode} {
			for _, key := range []string{"requested", "hop", "target", "explicit-empty"} {
				cells = append(cells, cat2FailoverCell{source: source, key: key, stream: stream})
			}
		}
	}
	return cells
}

// configured is the keyed chains the cell installs: on the org for the org
// source, else on the node.
func (c cat2FailoverCell) configured() map[string][]string {
	spare := []string{cat2FailoverSpare}
	switch c.key {
	case "requested":
		return map[string][]string{cat2FailoverAlias: spare}
	case "hop":
		return map[string][]string{cat2FailoverFirst: spare}
	case "target":
		return map[string][]string{cat2FailoverLast: spare}
	case "explicit-empty":
		return map[string][]string{cat2FailoverAlias: {}, cat2FailoverFirst: spare}
	}
	conflict := []string{cat2FailoverConflict}
	return map[string][]string{cat2FailoverAlias: conflict, cat2FailoverFirst: conflict}
}

// expected is the cell's independent oracle, not a call to the resolver: the
// models the upstream receives in order, and the response. Provider failover
// tries the first target, then the second (both fail); the plan then runs
// until a model answers. The first hop answers any later call, so a plan that
// resurrected it would be served by it, visibly.
func (c cat2FailoverCell) expected() (sent []string, status int, code string) {
	sent = []string{cat2FailoverFirst, cat2FailoverLast}
	var plan []string
	if c.source == cat2SourceRequest {
		for _, m := range c.declared {
			if m != cat2FailoverAlias && m != cat2FailoverFirst {
				plan = append(plan, m)
			}
		}
	} else if chain, present := c.configured()[cat2FailoverAlias]; present {
		plan = chain
	} else {
		plan = c.configured()[cat2FailoverFirst]
	}
	for _, m := range plan {
		sent = append(sent, m)
		if m != cat2FailoverLast {
			return sent, http.StatusOK, ""
		}
	}
	return sent, http.StatusBadGateway, "provider_503"
}

// errCat2NonLoopback is the refusal of cat2LoopbackDialer.
var errCat2NonLoopback = errors.New("cat2 failover cell: non-loopback dial refused")

// cat2LoopbackDialer opens a cell's provider connections to loopback
// addresses only. Any other address is refused before a packet is sent, and
// counted (708 add.59): a cell's providers reach only the cell's own upstream.
func cat2LoopbackDialer(dialed, refused *atomic.Int64) func(context.Context, string, string) (net.Conn, error) {
	return func(ctx context.Context, network, addr string) (net.Conn, error) {
		host, _, err := net.SplitHostPort(addr)
		if ip := net.ParseIP(host); err != nil || ip == nil || !ip.IsLoopback() {
			refused.Add(1)
			return nil, errCat2NonLoopback
		}
		dialed.Add(1)
		var d net.Dialer
		return d.DialContext(ctx, network, addr)
	}
}

// The failover cells' dialer refuses a non-loopback address before dialing.
func TestCAT2FailoverCellDialerRefusesNonLoopback(t *testing.T) {
	var dialed, refused atomic.Int64
	dial := cat2LoopbackDialer(&dialed, &refused)
	for _, addr := range []string{"203.0.113.8:443", "provider.invalid:443"} {
		if conn, err := dial(context.Background(), "tcp", addr); !errors.Is(err, errCat2NonLoopback) {
			if conn != nil {
				conn.Close()
			}
			t.Errorf("dial %s: %v, want a refusal before dialing", addr, err)
		}
	}
	if refused.Load() != 2 || dialed.Load() != 0 {
		t.Fatalf("refused=%d dialed=%d, want 2 and 0", refused.Load(), dialed.Load())
	}
}

// check runs the cell on its own gateway and loopback upstream.
func (c cat2FailoverCell) check(t *testing.T) {
	t.Helper()
	var mu sync.Mutex
	var sent []string
	firstCalls := 0
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		var req models.ChatCompletionRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		mu.Lock()
		sent = append(sent, req.Model)
		if req.Model == cat2FailoverFirst {
			firstCalls++
		}
		failing := (req.Model == cat2FailoverFirst && firstCalls == 1) || req.Model == cat2FailoverLast
		mu.Unlock()
		if failing {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusServiceUnavailable)
			fmt.Fprint(w, `{"error":{"message":"synthetic provider failure","code":"provider_503"}}`)
			return
		}
		if req.Stream {
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprintf(w, "data: {\"id\":\"c\",\"object\":\"chat.completion.chunk\",\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"content\":\"ok\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n", req.Model)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"c","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`, req.Model)
	}))
	defer upstream.Close()

	var dialed, refused atomic.Int64
	dial := cat2LoopbackDialer(&dialed, &refused)
	failover := []int{http.StatusServiceUnavailable, http.StatusBadGateway}
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "cat2-failover", Key: cat2FailoverBearer, Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "org-m"}}}
	cfg.Providers = map[string]config.ProviderConfig{}
	for id, model := range map[string]string{"fo-first": cat2FailoverFirst, "fo-last": cat2FailoverLast, "fo-spare": cat2FailoverSpare, "fo-conflict": cat2FailoverConflict} {
		cfg.Providers[id] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "synthetic", APIFormat: "openai", Models: []string{model}, DialContext: dial}
	}
	cfg.Routing.DefaultStrategy = "least-latency"
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{cat2FailoverAlias: {
		{Provider: "fo-first", ModelOverride: cat2FailoverFirst},
		{Provider: "fo-last", ModelOverride: cat2FailoverLast},
	}}
	cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: failover}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	orgCfg := &tenant.OrgConfig{}
	if c.source == cat2SourceOrg {
		orgCfg.Routing = &tenant.RoutingConfig{
			FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false},
			Failover:       &tenant.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: failover},
			ModelFallbacks: c.configured(),
		}
	}
	tenants := tenant.NewStore()
	tenants.Set("org-m", orgCfg)
	owner := newR601Owner()
	ledger := creditsplugin.NewOrgLedger(owner)
	ledger.SetMicros("org-m", 2000000000)
	credits := creditsplugin.New(true, keyStore)
	credits.SetOrgLedger(ledger, true)
	srv := New(cfg, "", registry, pipeline.NewEngine(credits), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	defer func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := srv.Shutdown(ctx); err != nil {
			t.Errorf("gateway shutdown: %v", err)
		}
		srv.batchProcessor.Stop()
		forgetOrgRoutingOf(srv.handlers)
	}()
	if c.source != cat2SourceOrg {
		srv.handlers.modelFallbacks.Store(routing.NewModelFallbacks(c.configured()))
	}

	body := map[string]any{"model": cat2FailoverAlias, "max_tokens": 64, "stream": c.stream, "messages": []map[string]string{{"role": "user", "content": "synthetic"}}}
	if c.declared != nil {
		body["models"] = c.declared
	}
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewReader(raw))
	req.Header.Set("Authorization", "Bearer "+cat2FailoverBearer)
	req.Header.Set("Content-Type", "application/json")
	before := cat2ChargeLedgerOf(t, owner)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	mu.Lock()
	got := append([]string(nil), sent...)
	mu.Unlock()
	var failure models.ErrorResponse
	if w.Code != http.StatusOK {
		if err := json.Unmarshal(w.Body.Bytes(), &failure); err != nil {
			t.Fatalf("invalid error envelope: %v: %.200s", err, w.Body.String())
		}
	}
	// No customer charge and no hold, served or not: the whole charge ledger
	// is as it was before the request.
	if after := cat2ChargeLedgerOf(t, owner); !after.equal(before) || after.account.HeldMicros != 0 {
		t.Errorf("charge ledger changed: stream=%t entries %d->%d spent %d->%d held %d->%d pending %d->%d",
			c.stream, len(before.entries), len(after.entries), before.account.SpentMicros, after.account.SpentMicros,
			before.account.HeldMicros, after.account.HeldMicros, before.pending, after.pending)
	}
	if dialed.Load() == 0 || refused.Load() != 0 {
		t.Errorf("provider dials: loopback %d, refused non-loopback %d; want some and none", dialed.Load(), refused.Load())
	}
	wantSent, wantStatus, wantCode := c.expected()
	if w.Code != wantStatus || failure.Error.Code != wantCode || !slices.Equal(got, wantSent) {
		t.Fatalf("plan after provider failover != the plan admission judged: status=%d code=%q sent=%v; want status=%d code=%q sent=%v",
			w.Code, failure.Error.Code, got, wantStatus, wantCode, wantSent)
	}
}
