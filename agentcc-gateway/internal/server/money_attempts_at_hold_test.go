package server

import (
	"context"
	"errors"
	"fmt"
	"math"
	"net"
	"net/http"
	"strconv"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/spendguard"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A managed request whose attempt was sent upstream and ended unanswered (the
// gateway's client timed it out after the send) and that then moved on to
// another attempt is charged for every such attempt at that attempt's own
// hold, added to what the later attempts billed, in one settlement: the
// provider was asked each time and may have charged for it. A retry of such
// an attempt takes a hold of its own, as a fallback does, rather than
// reusing the one its predecessor is charged at. An attempt that was never
// written is never charged.

// attemptTimeout is the managed provider's client timeout in these tests: an
// attempt the upstream does not answer within it fails as the gateway's
// timeout after the send, and the request moves on to its next model.
const attemptTimeout = 300 * time.Millisecond

// attemptsAtHold is one post-step observation of a request, where the spend
// guard reads it (after credits): what its window would settle, the
// request's cost evidence, and its attempts as priced.
type attemptsAtHold struct {
	billed         int64
	costSource     string // rc.Billing.CostSource, the log entry's enum
	metaCostSource string // the request's cost_source metadata
	usageEstimated string
	attempts       []models.Attempt
}

// attemptsAtHoldProbe records attemptsAtHold at the spend guard's post step.
type attemptsAtHoldProbe struct {
	mu   sync.Mutex
	seen []attemptsAtHold
}

func (p *attemptsAtHoldProbe) Name() string      { return "attempts-at-hold-probe" }
func (p *attemptsAtHoldProbe) Priority() int     { return spendguard.Priority }
func (p *attemptsAtHoldProbe) PostPriority() int { return spendguard.PostPriority }

func (p *attemptsAtHoldProbe) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

func (p *attemptsAtHoldProbe) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	var sent []models.Attempt
	for _, a := range rc.AttemptsSnapshot() {
		if a.Outcome != models.OutcomeSkipped {
			sent = append(sent, a)
		}
	}
	obs := attemptsAtHold{
		costSource:     string(rc.Billing.CostSource),
		metaCostSource: rc.Metadata["cost_source"],
		usageEstimated: rc.Metadata["usage_estimated"],
		attempts:       sent,
	}
	if !rc.Flags.CacheHit {
		obs.billed = rc.Billing.BilledMicros
	}
	p.mu.Lock()
	p.seen = append(p.seen, obs)
	p.mu.Unlock()
	return pipeline.ResultContinue()
}

// only is the one observation of the test's one request.
func (p *attemptsAtHoldProbe) only(t *testing.T) attemptsAtHold {
	t.Helper()
	p.mu.Lock()
	defer p.mu.Unlock()
	if len(p.seen) != 1 {
		t.Fatalf("post-step observations = %d, want exactly one", len(p.seen))
	}
	return p.seen[0]
}

// heldAtCalls is the org's held micros at each managed call, in order.
type heldAtCalls struct {
	mu   sync.Mutex
	held []int64
}

func (h *heldAtCalls) at(i int) int64 {
	h.mu.Lock()
	defer h.mu.Unlock()
	if i >= len(h.held) {
		return 0
	}
	return h.held[i]
}

// attemptsUpstream answers g's managed non-stream calls by model: a model in
// hang is never answered until the gateway cuts the call; any other is served
// with the upstream's usage and charge (upstreamCost). It records the org's
// held micros at each call it answers or hangs.
func attemptsUpstream(g *managedTestGateway, hang ...string) *heldAtCalls {
	h := &heldAtCalls{}
	answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
		if acct, err := g.ledger.Account(context.Background(), "org-m"); err == nil {
			h.mu.Lock()
			h.held = append(h.held, acct.HeldMicros)
			h.mu.Unlock()
		}
		for _, m := range hang {
			if m == model {
				select {
				case <-r.Context().Done():
				case <-time.After(10 * time.Second):
				}
				return
			}
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"c1","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500,"cost":%g}}`, model, upstreamCost)
	})
	g.managed.responseWith.Store(&answer)
	return h
}

// timeoutGateway is the managed test gateway with probe, the managed
// provider's client timeout at attemptTimeout, and dial, when set, as its
// sockets' dialer.
func timeoutGateway(t *testing.T, probe pipeline.Plugin, dial func(context.Context, string, string) (net.Conn, error)) *managedTestGateway {
	t.Helper()
	return timeoutGatewayFor(t, managedOptIn(), probe, dial)
}

// timeoutGatewayFor is timeoutGateway for the org org.
func timeoutGatewayFor(t *testing.T, org *tenant.OrgConfig, probe pipeline.Plugin, dial func(context.Context, string, string) (net.Conn, error)) *managedTestGateway {
	t.Helper()
	g := newManagedTestGatewayWith(t, org, managedGatewayOptions{
		extraPlugins: []pipeline.Plugin{probe},
		dialContext:  dial,
		configure: func(c *config.Config) {
			p := c.Providers["openrouter"]
			p.DefaultTimeout = attemptTimeout
			c.Providers["openrouter"] = p
		},
	})
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))
	return g
}

// attemptsBody is a managed chat request for primary that falls back to
// fallbacks (the request's own models[]).
func attemptsBody(primary string, fallbacks ...string) string {
	list := ""
	for i, m := range fallbacks {
		if i > 0 {
			list += ","
		}
		list += strconv.Quote(m)
	}
	return `{"model":"` + primary + `","models":[` + list + `],"messages":[{"role":"user","content":"hi"}]}`
}

// servedBilled is what the served fallback bills: the upstream's charge plus
// the 5% markup.
func servedBilled() int64 {
	return int64(math.Round(upstreamCost * 1.05 * 1e6))
}

// The primary is sent and times out at the gateway; the fallback serves. The
// request settles once at the primary's own hold plus the fallback's usage:
// the ledger, the account and the spend guard's window move once, by that sum.
func TestTimedOutPrimaryIsChargedAtItsHoldWhenAFallbackServes(t *testing.T) {
	probe := &attemptsAtHoldProbe{}
	g := timeoutGateway(t, probe, nil)
	held := attemptsUpstream(g, "anthropic/claude-sonnet-5.5")

	w := atHoldChat(context.Background(), g, attemptsBody("anthropic/claude-sonnet-5.5", "openai/gpt-6-sol"), nil)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d body %s, want the fallback's answer", w.Code, w.Body)
	}
	if g.managed.calls() != 2 || g.managed.lastModel() != "openai/gpt-6-sol" {
		t.Fatalf("upstream calls = %d (last %q), want the primary then the fallback", g.managed.calls(), g.managed.lastModel())
	}
	primaryHold := held.at(0)
	if primaryHold <= 0 {
		t.Fatalf("no hold was observed while the primary was in flight (%d)", primaryHold)
	}
	obs := probe.only(t)
	if len(obs.attempts) != 2 {
		t.Fatalf("attempts = %+v, want the primary and the fallback", obs.attempts)
	}
	primary, fallback := obs.attempts[0], obs.attempts[1]
	if !primary.Billable || primary.BilledCostMicros != primaryHold || primary.CostSource != models.CostSourceCatalogMax {
		t.Errorf("primary attempt billable=%v billed=%d source=%q, want charged at its hold %d, catalog_max",
			primary.Billable, primary.BilledCostMicros, primary.CostSource, primaryHold)
	}
	if fallback.BilledCostMicros != servedBilled() {
		t.Errorf("fallback attempt billed %d, want its usage %d", fallback.BilledCostMicros, servedBilled())
	}
	want := primaryHold + servedBilled()
	recs := g.outbox("org-m")
	if len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["billed_micros"] != strconv.FormatInt(want, 10) {
		t.Fatalf("settlement records = %v, want one spend at the primary's hold + the fallback's usage = %d", recs, want)
	}
	if up := strconv.FormatInt(primaryHold*10000/10500+fallback.UpstreamCostMicros, 10); recs[0]["upstream_micros"] != up {
		t.Errorf("upstream_micros = %s, want %s (the hold without its 5%% markup, plus the fallback's upstream charge)", recs[0]["upstream_micros"], up)
	}
	if recs[0]["usage_estimated"] != "1" {
		t.Errorf("usage_estimated = %q, want 1 (part of the charge is a hold)", recs[0]["usage_estimated"])
	}
	acct, err := g.ledger.Account(context.Background(), "org-m")
	if err != nil {
		t.Fatal(err)
	}
	if acct.SpentMicros != want || acct.HeldMicros != 0 {
		t.Errorf("account spent=%d held=%d, want spent %d once and nothing held", acct.SpentMicros, acct.HeldMicros, want)
	}
	if obs.billed != want {
		t.Errorf("spend guard window settles %d, want %d", obs.billed, want)
	}
	if obs.metaCostSource != string(models.CostSourceMixed) || obs.costSource == string(models.CostSourceCatalogMax) || obs.usageEstimated != "true" {
		t.Errorf("request evidence cost_source=%q (log %q) usage_estimated=%q, want mixed (log: the served attempt's source) and estimated",
			obs.metaCostSource, obs.costSource, obs.usageEstimated)
	}
}

// Two attempts are sent and time out at the gateway, then the last model's
// upstream refuses the call: the request settles once at both holds.
func TestEverySentUnansweredAttemptIsChargedBeforeARefusal(t *testing.T) {
	probe := &attemptsAtHoldProbe{}
	g := timeoutGateway(t, probe, nil)
	held := attemptsUpstream(g, "anthropic/claude-sonnet-5.5", "openai/gpt-6-sol")
	g.managed.failFor.Store("qwen/qwen3.7-flash", http.StatusBadRequest)

	w := atHoldChat(context.Background(), g, attemptsBody("anthropic/claude-sonnet-5.5", "openai/gpt-6-sol", "qwen/qwen3.7-flash"), nil)

	if w.Code == http.StatusOK {
		t.Fatalf("a refused request answered 200: %s", w.Body)
	}
	if g.managed.calls() != 3 || g.managed.lastModel() != "qwen/qwen3.7-flash" {
		t.Fatalf("upstream calls = %d (last %q), want all three models", g.managed.calls(), g.managed.lastModel())
	}
	first, both := held.at(0), held.at(1)
	if first <= 0 || both <= first {
		t.Fatalf("held while in flight: %d then %d; want the primary's hold, then both holds", first, both)
	}
	obs := probe.only(t)
	if len(obs.attempts) != 3 {
		t.Fatalf("attempts = %+v, want three sent attempts", obs.attempts)
	}
	for i, hold := range []int64{first, both - first} {
		if a := obs.attempts[i]; !a.Billable || a.BilledCostMicros != hold || a.CostSource != models.CostSourceCatalogMax {
			t.Errorf("attempt %d billable=%v billed=%d source=%q, want charged at its own hold %d, catalog_max",
				i+1, a.Billable, a.BilledCostMicros, a.CostSource, hold)
		}
	}
	if a := obs.attempts[2]; a.BilledCostMicros != 0 {
		t.Errorf("the refused attempt billed %d, want nothing", a.BilledCostMicros)
	}
	recs := g.outbox("org-m")
	if len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["billed_micros"] != strconv.FormatInt(both, 10) {
		t.Fatalf("settlement records = %v, want one spend at both holds = %d", recs, both)
	}
	if recs[0]["cost_source"] != "catalog_max" || recs[0]["usage_estimated"] != "1" {
		t.Errorf("cost_source=%q usage_estimated=%q, want catalog_max and 1", recs[0]["cost_source"], recs[0]["usage_estimated"])
	}
	acct, err := g.ledger.Account(context.Background(), "org-m")
	if err != nil {
		t.Fatal(err)
	}
	if acct.SpentMicros != both || acct.HeldMicros != 0 {
		t.Errorf("account spent=%d held=%d, want spent %d once and nothing held", acct.SpentMicros, acct.HeldMicros, both)
	}
	if obs.billed != both || obs.metaCostSource != "catalog_max" || obs.usageEstimated != "true" {
		t.Errorf("window %d, cost_source %q, usage_estimated %q; want %d, catalog_max, true", obs.billed, obs.metaCostSource, obs.usageEstimated, both)
	}
}

// retryOn504 is a Future AGI org whose routing policy retries a 504 once.
// Messages has no retry of its own (openRoutedChatStream: retries are only
// ever configured), so the retry below is the org's.
func retryOn504() *tenant.OrgConfig {
	org := managedOptIn()
	org.Routing = &tenant.RoutingConfig{Retry: &tenant.RetryConfig{
		Enabled: true, MaxRetries: 1, InitialDelay: "1ms", MaxDelay: "2ms",
		OnStatusCodes: []int{http.StatusGatewayTimeout},
	}}
	return org
}

// retryUpstream answers g's managed non-stream calls in order: the first is
// never answered until the gateway cuts it, every later one is served with
// the upstream's usage and charge (upstreamCost). It records the org's held
// micros at each call, before it answers or hangs.
func retryUpstream(g *managedTestGateway) *heldAtCalls {
	h := &heldAtCalls{}
	answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
		acct, _ := g.ledger.Account(context.Background(), "org-m")
		h.mu.Lock()
		h.held = append(h.held, acct.HeldMicros)
		first := len(h.held) == 1
		h.mu.Unlock()
		if first {
			select {
			case <-r.Context().Done():
			case <-time.After(10 * time.Second):
			}
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"c1","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500,"cost":%g}}`, model, upstreamCost)
	})
	g.managed.responseWith.Store(&answer)
	return h
}

// A non-streamed /v1/messages call is sent and times out at the gateway
// (504), the org's policy retries it once, and the retry serves. The request
// settles once at the first try's own hold plus the retry's usage, and the
// retry took its own hold (ReserveAdd) before it was sent: the ledger holds
// two reservations while the retry is in flight, the settlement releases
// both, and the retry's pricing snapshot is its own hold, so the first try's
// hold is never charged and spent again by the retry.
func TestTimedOutTryIsChargedAtItsHoldAndItsRetryReservesItsOwn(t *testing.T) {
	probe := &attemptsAtHoldProbe{}
	g := timeoutGatewayFor(t, retryOn504(), probe, nil)
	held := retryUpstream(g)

	w := postMessages(t, g.srv, "/v1/messages", managedOrgKey, managedMessagesBody("anthropic/claude-sonnet-5.5", false, ""))

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d body %s, want the retry's answer", w.Code, w.Body)
	}
	if g.managed.calls() != 2 {
		t.Fatalf("upstream calls = %d, want the first try and one retry", g.managed.calls())
	}
	first, both := held.at(0), held.at(1)
	if first <= 0 {
		t.Fatalf("no hold was observed while the first try was in flight (%d)", first)
	}
	obs := probe.only(t)
	if len(obs.attempts) != 2 || obs.attempts[1].Kind != models.AttemptRetry {
		t.Fatalf("attempts = %+v, want the first try and its retry", obs.attempts)
	}
	try, retry := obs.attempts[0], obs.attempts[1]
	if !try.Billable || try.BilledCostMicros != first || try.CostSource != models.CostSourceCatalogMax {
		t.Errorf("first try billable=%v billed=%d source=%q, want charged at its hold %d, catalog_max",
			try.Billable, try.BilledCostMicros, try.CostSource, first)
	}
	if retry.BilledCostMicros != servedBilled() {
		t.Errorf("retry billed %d, want its usage %d", retry.BilledCostMicros, servedBilled())
	}
	want := first + servedBilled()
	recs := g.outbox("org-m")
	if len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["billed_micros"] != strconv.FormatInt(want, 10) {
		t.Fatalf("settlement records = %v, want one spend at the first try's hold + the retry's usage = %d", recs, want)
	}
	// Two reservations: the retry's own hold (ReserveAdd) on top of the
	// first try's, both released by the one settlement.
	if both <= first {
		t.Errorf("held %d while the retry was in flight and %d while the first try was; want the retry's own reservation on top", both, first)
	}
	if recs[0]["hold_micros"] != strconv.FormatInt(both, 10) {
		t.Errorf("hold_micros = %s, want %d: the first try's and the retry's reservations", recs[0]["hold_micros"], both)
	}
	if retry.Pricing == nil || retry.Pricing.HoldMicros != both-first {
		t.Errorf("retry pricing snapshot %+v, want its own reservation %d", retry.Pricing, both-first)
	}
	if up := strconv.FormatInt(first*10000/10500+retry.UpstreamCostMicros, 10); recs[0]["upstream_micros"] != up {
		t.Errorf("upstream_micros = %s, want %s (the hold without its 5%% markup, plus the retry's upstream charge)", recs[0]["upstream_micros"], up)
	}
	if recs[0]["usage_estimated"] != "1" {
		t.Errorf("usage_estimated = %q, want 1 (part of the charge is a hold)", recs[0]["usage_estimated"])
	}
	acct, err := g.ledger.Account(context.Background(), "org-m")
	if err != nil {
		t.Fatal(err)
	}
	if acct.SpentMicros != want || acct.HeldMicros != 0 {
		t.Errorf("account spent=%d held=%d, want spent %d once and nothing held", acct.SpentMicros, acct.HeldMicros, want)
	}
	if obs.billed != want {
		t.Errorf("spend guard window settles %d, want %d", obs.billed, want)
	}
	if obs.metaCostSource != string(models.CostSourceMixed) || obs.usageEstimated != "true" {
		t.Errorf("request evidence cost_source=%q usage_estimated=%q, want mixed and estimated", obs.metaCostSource, obs.usageEstimated)
	}
}

// The primary's connection is refused, so it is never written; the fallback
// serves. Only the fallback's usage is billed, never a hold.
func TestNeverWrittenAttemptIsNotChargedWhenAFallbackServes(t *testing.T) {
	probe := &attemptsAtHoldProbe{}
	var dials atomic.Int32
	dial := func(ctx context.Context, network, addr string) (net.Conn, error) {
		if dials.Add(1) == 1 {
			return nil, errors.New("connection refused (test)")
		}
		var d net.Dialer
		return d.DialContext(ctx, network, addr)
	}
	g := timeoutGateway(t, probe, dial)
	attemptsUpstream(g)

	w := atHoldChat(context.Background(), g, attemptsBody("anthropic/claude-sonnet-5.5", "openai/gpt-6-sol"), nil)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d body %s, want the fallback's answer", w.Code, w.Body)
	}
	if g.managed.calls() != 1 || g.managed.lastModel() != "openai/gpt-6-sol" {
		t.Fatalf("upstream calls = %d (last %q), want only the fallback to reach the upstream", g.managed.calls(), g.managed.lastModel())
	}
	want := servedBilled()
	recs := g.outbox("org-m")
	if len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["billed_micros"] != strconv.FormatInt(want, 10) {
		t.Fatalf("settlement records = %v, want one spend at the fallback's usage %d", recs, want)
	}
	if recs[0]["cost_source"] == "catalog_max" || recs[0]["usage_estimated"] == "1" {
		t.Errorf("settlement = %v, want the reported usage only", recs[0])
	}
	acct, err := g.ledger.Account(context.Background(), "org-m")
	if err != nil {
		t.Fatal(err)
	}
	if acct.SpentMicros != want || acct.HeldMicros != 0 {
		t.Errorf("account spent=%d held=%d, want spent %d and nothing held", acct.SpentMicros, acct.HeldMicros, want)
	}
	obs := probe.only(t)
	if obs.billed != want || obs.metaCostSource == string(models.CostSourceMixed) || obs.metaCostSource == "catalog_max" {
		t.Errorf("window %d, cost_source %q; want %d from the reported usage alone", obs.billed, obs.metaCostSource, want)
	}
	for _, a := range obs.attempts {
		if a.CostSource == models.CostSourceCatalogMax {
			t.Errorf("attempt %d was charged at a hold: %+v", a.Index, a)
		}
	}
}
