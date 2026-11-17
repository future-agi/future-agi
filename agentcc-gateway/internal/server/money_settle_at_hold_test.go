package server

import (
	"context"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/spendguard"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A managed (Future AGI credits) call that was sent upstream and ended with
// no usage (the caller cancelled, the request's timeout cut it, a stream was
// cut before its usage chunk) is charged at the hold its attempt reserved,
// never voided and never billed on the visible text: the provider was asked
// and may have charged for it. A call never sent is voided as before, and a
// call on the org's own key never touches the managed ledger.

// atHoldWindow is one post-step observation of a request: what the spend
// guard's window would record and the evidence the request carried.
type atHoldWindow struct {
	settled     int64 // 0 when voided
	mode        string
	unreadable  string
	costSource  string
	usageMarked string
}

// atHoldWindowProbe is the spend guard's post step as an in-package fake. It
// runs where the spend guard does (spendguard.PostPriority, after credits)
// and applies its rule verbatim (spendguard/plugin.go finish): settle the
// velocity window at rc.Billing.BilledMicros, or void when nothing was billed
// or the answer came from the cache.
type atHoldWindowProbe struct {
	mu   sync.Mutex
	seen []atHoldWindow
}

func (p *atHoldWindowProbe) Name() string      { return "at-hold-window-probe" }
func (p *atHoldWindowProbe) Priority() int     { return spendguard.Priority }
func (p *atHoldWindowProbe) PostPriority() int { return spendguard.PostPriority }

func (p *atHoldWindowProbe) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

func (p *atHoldWindowProbe) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	obs := atHoldWindow{
		mode:        rc.Metadata["billing_mode"],
		unreadable:  rc.Metadata["usage_unreadable"],
		costSource:  string(rc.Billing.CostSource),
		usageMarked: rc.Metadata["usage_estimated"],
	}
	if billed := rc.Billing.BilledMicros; billed > 0 && !rc.Flags.CacheHit {
		obs.settled = billed
	}
	p.mu.Lock()
	p.seen = append(p.seen, obs)
	p.mu.Unlock()
	return pipeline.ResultContinue()
}

func (p *atHoldWindowProbe) observations() []atHoldWindow {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]atHoldWindow(nil), p.seen...)
}

// atHoldHangingUpstream makes u receive every call, record the org's held
// micros while the call is in flight, signal dispatched, and then never
// answer until the gateway cuts the call (or a safety timeout passes). For a
// stream it first sends one content chunk and no usage chunk.
func atHoldHangingUpstream(g *managedTestGateway, u *recordingUpstream, stream bool) (held *atomic.Int64, dispatched chan struct{}) {
	held = &atomic.Int64{}
	dispatched = make(chan struct{}, 4)
	answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
		if acct, err := g.ledger.Account(context.Background(), "org-m"); err == nil {
			held.Store(acct.HeldMicros)
		}
		if stream {
			w.Header().Set("Content-Type", "text/event-stream")
			_, _ = w.Write([]byte(`data: {"id":"c1","object":"chat.completion.chunk","model":"` + model +
				`","choices":[{"index":0,"delta":{"role":"assistant","content":"partial answer"}}]}` + "\n\n"))
			if f, ok := w.(http.Flusher); ok {
				f.Flush()
			}
		}
		select {
		case dispatched <- struct{}{}:
		default:
		}
		select {
		case <-r.Context().Done():
		case <-time.After(10 * time.Second):
		}
	})
	if stream {
		u.streamWith.Store(&answer)
	} else {
		u.responseWith.Store(&answer)
	}
	return held, dispatched
}

// atHoldChat sends a chat request with the managed org's key on ctx.
func atHoldChat(ctx context.Context, g *managedTestGateway, body string, headers map[string]string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body)).WithContext(ctx)
	req.Header.Set("Authorization", "Bearer "+managedOrgKey)
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// cancelOnDispatch cancels when the upstream has the call.
func cancelOnDispatch(dispatched <-chan struct{}, cancel context.CancelFunc) {
	select {
	case <-dispatched:
		cancel()
	case <-time.After(10 * time.Second):
	}
}

// assertSettledAtHold checks the one settlement of the org's request: a
// spend at exactly hold (the micros held while the call was in flight, never
// a recomputed quote), its upstream part at the 5% markup the hold carries,
// the hold released, the balance moved once, and the spend guard's window
// settled once at the same amount.
func assertSettledAtHold(t *testing.T, g *managedTestGateway, probe *atHoldWindowProbe, hold int64) {
	t.Helper()
	if hold <= 0 {
		t.Fatalf("no hold was observed while the call was in flight (%d)", hold)
	}
	recs := g.outbox("org-m")
	if len(recs) != 1 {
		t.Fatalf("settlement records = %d (%v), want exactly one", len(recs), recs)
	}
	rec := recs[0]
	want := strconv.FormatInt(hold, 10)
	if rec["kind"] != "spend" || rec["billed_micros"] != want || rec["hold_micros"] != want {
		t.Fatalf("settlement kind=%s billed=%s hold=%s, want one spend at the hold %s", rec["kind"], rec["billed_micros"], rec["hold_micros"], want)
	}
	if up := strconv.FormatInt(hold*10000/10500, 10); rec["upstream_micros"] != up {
		t.Errorf("upstream_micros = %s, want %s (the hold without its 5%% markup)", rec["upstream_micros"], up)
	}
	if rec["cost_source"] != "catalog_max" || rec["usage_estimated"] != "1" {
		t.Errorf("cost_source=%q usage_estimated=%q, want catalog_max and 1 (charged at the hold)", rec["cost_source"], rec["usage_estimated"])
	}
	acct, err := g.ledger.Account(context.Background(), "org-m")
	if err != nil {
		t.Fatal(err)
	}
	if acct.SpentMicros != hold || acct.HeldMicros != 0 {
		t.Errorf("account spent=%d held=%d, want spent %d and nothing held", acct.SpentMicros, acct.HeldMicros, hold)
	}
	obs := probe.observations()
	if len(obs) != 1 || obs[0].settled != hold {
		t.Fatalf("spend guard window = %+v, want one settle at %d", obs, hold)
	}
	if obs[0].unreadable != "dispatched_without_usage" || obs[0].costSource != "catalog_max" || obs[0].usageMarked != "true" {
		t.Errorf("request evidence = %+v, want usage_unreadable dispatched_without_usage, cost source catalog_max, usage_estimated true", obs[0])
	}
}

// (a) A non-stream managed call the caller abandons, or the request's timeout
// cuts, after the upstream has it settles once at its attempt's hold.
func TestManagedNonStreamUnknownOutcomeSettlesAtHold(t *testing.T) {
	t.Run("caller cancels after dispatch", func(t *testing.T) {
		probe := &atHoldWindowProbe{}
		g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{extraPlugins: []pipeline.Plugin{probe}})
		g.ledger.SetMicros("org-m", auth.USDToMicros(1))
		held, dispatched := atHoldHangingUpstream(g, g.managed, false)
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		go cancelOnDispatch(dispatched, cancel)

		w := atHoldChat(ctx, g, chatBody("anthropic/claude-sonnet-5.5"), nil)

		if w.Code == http.StatusOK {
			t.Fatalf("a cancelled call answered 200: %s", w.Body)
		}
		if g.managed.calls() != 1 {
			t.Fatalf("upstream calls = %d, want 1", g.managed.calls())
		}
		assertSettledAtHold(t, g, probe, held.Load())
	})
	t.Run("request timeout cuts the call", func(t *testing.T) {
		probe := &atHoldWindowProbe{}
		g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{extraPlugins: []pipeline.Plugin{probe}})
		g.ledger.SetMicros("org-m", auth.USDToMicros(1))
		held, _ := atHoldHangingUpstream(g, g.managed, false)

		w := atHoldChat(context.Background(), g, chatBody("anthropic/claude-sonnet-5.5"), map[string]string{"x-agentcc-request-timeout": "300"})

		if w.Code != http.StatusGatewayTimeout && w.Code != http.StatusRequestTimeout {
			t.Fatalf("status = %d body %s, want the caller's own timeout (504 or 408)", w.Code, w.Body)
		}
		if g.managed.calls() != 1 {
			t.Fatalf("upstream calls = %d, want 1", g.managed.calls())
		}
		assertSettledAtHold(t, g, probe, held.Load())
	})
}

// (c) A managed call that never reached the upstream (its connection was
// refused) is voided as before: the hold returns and nothing is spent.
func TestManagedCallNeverSentIsStillVoided(t *testing.T) {
	probe := &atHoldWindowProbe{}
	refuse := func(context.Context, string, string) (net.Conn, error) {
		return nil, errors.New("connection refused (test)")
	}
	g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{extraPlugins: []pipeline.Plugin{probe}, dialContext: refuse})
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))

	w := atHoldChat(context.Background(), g, chatBody("anthropic/claude-sonnet-5.5"), nil)

	if w.Code == http.StatusOK || g.managed.calls() != 0 {
		t.Fatalf("status = %d, upstream calls = %d; the call must fail before the upstream", w.Code, g.managed.calls())
	}
	recs := g.outbox("org-m")
	if len(recs) != 1 || recs[0]["kind"] != "void" || recs[0]["billed_micros"] != "0" {
		t.Fatalf("settlement records = %v, want one void", recs)
	}
	acct, err := g.ledger.Account(context.Background(), "org-m")
	if err != nil {
		t.Fatal(err)
	}
	if acct.SpentMicros != 0 || acct.HeldMicros != 0 {
		t.Fatalf("account spent=%d held=%d, want nothing spent and nothing held", acct.SpentMicros, acct.HeldMicros)
	}
	obs := probe.observations()
	if len(obs) != 1 || obs[0].settled != 0 || obs[0].unreadable != "" {
		t.Fatalf("spend guard window = %+v, want one void and no dispatch marker", obs)
	}
}

// (e) A call on the org's own provider key that the caller cancels after
// dispatch is unchanged: no managed hold, no settlement, no dispatch marker.
func TestOwnKeyCancelAfterDispatchIsUnchanged(t *testing.T) {
	probe := &atHoldWindowProbe{}
	g := newManagedTestGatewayWith(t, &tenant.OrgConfig{
		Providers: map[string]*tenant.ProviderConfig{
			"operator": {APIKey: "org-operator-key", APIFormat: "openai", Models: []string{"org-model"}, Enabled: true},
		},
	}, managedGatewayOptions{extraPlugins: []pipeline.Plugin{probe}})
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))
	_, dispatched := atHoldHangingUpstream(g, g.operator, false)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go cancelOnDispatch(dispatched, cancel)

	w := atHoldChat(ctx, g, chatBody("org-model"), nil)

	if w.Code == http.StatusOK || g.operator.calls() != 1 {
		t.Fatalf("status = %d, operator calls = %d; want one cancelled own-key call", w.Code, g.operator.calls())
	}
	if got := g.operator.lastAuth(); got != "Bearer org-operator-key" {
		t.Fatalf("the call was sent with %q, want the org's own key", got)
	}
	if recs := g.outbox("org-m"); len(recs) != 0 {
		t.Fatalf("an own-key call reached the managed ledger: %v", recs)
	}
	acct, err := g.ledger.Account(context.Background(), "org-m")
	if err != nil {
		t.Fatal(err)
	}
	if acct.SpentMicros != 0 || acct.HeldMicros != 0 {
		t.Fatalf("account spent=%d held=%d, want untouched", acct.SpentMicros, acct.HeldMicros)
	}
	obs := probe.observations()
	if len(obs) != 1 || obs[0].mode == "managed" || obs[0].unreadable != "" || obs[0].settled != 0 {
		t.Fatalf("own-key request evidence = %+v, want byok, no dispatch marker, nothing billed", obs)
	}
}
