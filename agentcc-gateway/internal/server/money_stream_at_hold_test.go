package server

import (
	"bufio"
	"context"
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A managed stream that was sent upstream and ended before the upstream's
// usage chunk for a reason that is not its caller's stop (the upstream
// dropped it, the request's deadline cut it) is charged at its hold, on
// /v1/messages as on chat: the visible text misses hidden reasoning and the
// output not yet delivered. A stream its caller stops is
// charged on what was received, the visible-text estimate, as before: the
// gateway cancels the upstream, so the provider's bill is close to what was
// sent back. A stream the upstream ended itself keeps its price (its reported
// usage, or the estimate when it sent none), and a stream on the org's own
// key never touches the managed ledger.

// atHoldStreamAndLeave streams body on surface through a real HTTP server,
// reads until the upstream's first content ("partial answer") arrives, then
// leaves, and returns once the gateway has finished the request.
func atHoldStreamAndLeave(t *testing.T, g *managedTestGateway, surface, body string) {
	t.Helper()
	done := make(chan struct{})
	front := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer close(done)
		g.srv.httpServer.Handler.ServeHTTP(w, r)
	}))
	defer front.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, front.URL+surface, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+managedOrgKey)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("anthropic-version", "2023-06-01")
	resp, err := front.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("%s: stream status = %d", surface, resp.StatusCode)
	}
	lines := bufio.NewReader(resp.Body)
	for {
		line, err := lines.ReadString('\n')
		if err != nil {
			t.Fatalf("%s: the stream ended before its first content: %v", surface, err)
		}
		if strings.Contains(line, "partial answer") {
			break
		}
	}
	cancel()
	resp.Body.Close()
	select {
	case <-done:
	case <-time.After(10 * time.Second):
		t.Fatalf("%s: the gateway did not finish the stream its caller stopped", surface)
	}
}

// atHoldStreamToEnd streams body on surface through a real HTTP server, with
// headers added to the request, reads the answer to its end, and returns
// once the gateway has finished the request.
func atHoldStreamToEnd(t *testing.T, g *managedTestGateway, surface, body string, headers map[string]string) {
	t.Helper()
	done := make(chan struct{})
	front := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer close(done)
		g.srv.httpServer.Handler.ServeHTTP(w, r)
	}))
	defer front.Close()

	req, err := http.NewRequest(http.MethodPost, front.URL+surface, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+managedOrgKey)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("anthropic-version", "2023-06-01")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := front.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	got, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("%s: stream status = %d body %s", surface, resp.StatusCode, got)
	}
	select {
	case <-done:
	case <-time.After(10 * time.Second):
		t.Fatalf("%s: the gateway did not finish the stream", surface)
	}
}

// atHoldStreamBody is a managed stream request on surface.
func atHoldStreamBody(surface string) string {
	if surface == "/v1/messages" {
		return managedMessagesBody("anthropic/claude-sonnet-5.5", true, "")
	}
	return `{"model":"anthropic/claude-sonnet-5.5","stream":true,"messages":[{"role":"user","content":"hi"}]}`
}

// atHoldDroppingUpstream makes u's stream record the org's hold while the
// call is in flight, send its first content ("partial answer"), then drop
// the connection, before any usage. It returns the hold it saw.
func atHoldDroppingUpstream(g *managedTestGateway, u *recordingUpstream) *atomic.Int64 {
	held := &atomic.Int64{}
	answer := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
		if acct, err := g.ledger.Account(context.Background(), "org-m"); err == nil {
			held.Store(acct.HeldMicros)
		}
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte(`data: {"id":"c1","object":"chat.completion.chunk","model":"` + model +
			`","choices":[{"index":0,"delta":{"role":"assistant","content":"partial answer"}}]}` + "\n\n"))
		if f, ok := w.(http.Flusher); ok {
			f.Flush()
		}
		panic(http.ErrAbortHandler)
	})
	u.streamWith.Store(&answer)
	return held
}

// A managed stream whose upstream drops the connection after its first
// text, before its usage, settles at its own hold on both surfaces: one
// spend at the hold, cost source catalog_max, the dispatch marker, the
// window once.
func TestManagedStreamUpstreamDropBeforeUsageSettlesAtHold(t *testing.T) {
	for _, surface := range managedSurfaces {
		t.Run(surface, func(t *testing.T) {
			probe := &atHoldWindowProbe{}
			g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{extraPlugins: []pipeline.Plugin{probe}})
			g.ledger.SetMicros("org-m", auth.USDToMicros(1))
			held := atHoldDroppingUpstream(g, g.managed)

			atHoldStreamToEnd(t, g, surface, atHoldStreamBody(surface), nil)

			if g.managed.calls() != 1 {
				t.Fatalf("upstream calls = %d, want 1", g.managed.calls())
			}
			assertSettledAtHold(t, g, probe, held.Load())
		})
	}
}

// A managed stream the request's own deadline cuts after its first text,
// before its usage, is not its caller's stop: it settles at its own hold on
// both surfaces, as an upstream drop does.
func TestManagedStreamDeadlineCutBeforeUsageSettlesAtHold(t *testing.T) {
	for _, surface := range managedSurfaces {
		t.Run(surface, func(t *testing.T) {
			probe := &atHoldWindowProbe{}
			g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{extraPlugins: []pipeline.Plugin{probe}})
			g.ledger.SetMicros("org-m", auth.USDToMicros(1))
			held, _ := atHoldHangingUpstream(g, g.managed, true)

			atHoldStreamToEnd(t, g, surface, atHoldStreamBody(surface), map[string]string{"x-agentcc-request-timeout": "300"})

			if g.managed.calls() != 1 {
				t.Fatalf("upstream calls = %d, want 1", g.managed.calls())
			}
			assertSettledAtHold(t, g, probe, held.Load())
		})
	}
}

// A managed stream its caller stops after the first text, before the
// upstream's usage, is charged on what was received: one
// spend on the visible-text estimate, below the hold, nothing left held, and
// no dispatch marker. It is never settled at its hold.
func TestManagedStreamCallerStopIsChargedOnWhatWasReceived(t *testing.T) {
	for _, surface := range managedSurfaces {
		t.Run(surface, func(t *testing.T) {
			probe := &atHoldWindowProbe{}
			g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{extraPlugins: []pipeline.Plugin{probe}})
			g.ledger.SetMicros("org-m", auth.USDToMicros(1))
			held, _ := atHoldHangingUpstream(g, g.managed, true)

			atHoldStreamAndLeave(t, g, surface, atHoldStreamBody(surface))

			if g.managed.calls() != 1 {
				t.Fatalf("upstream calls = %d, want 1", g.managed.calls())
			}
			hold := held.Load()
			if hold <= 0 {
				t.Fatalf("no hold was observed while the call was in flight (%d)", hold)
			}
			recs := g.outbox("org-m")
			if len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["usage_estimated"] != "1" {
				t.Fatalf("settlement records = %v, want one spend on an estimate", recs)
			}
			billed, err := strconv.ParseInt(recs[0]["billed_micros"], 10, 64)
			if err != nil || billed <= 0 || billed >= hold {
				t.Fatalf("billed_micros = %q, want the visible-text estimate, above 0 and below the hold %d", recs[0]["billed_micros"], hold)
			}
			if recs[0]["cost_source"] == "catalog_max" {
				t.Errorf("settlement = %v, want the estimate, not the hold", recs[0])
			}
			acct, err := g.ledger.Account(context.Background(), "org-m")
			if err != nil {
				t.Fatal(err)
			}
			if acct.SpentMicros != billed || acct.HeldMicros != 0 {
				t.Errorf("account spent=%d held=%d, want spent %d and nothing held", acct.SpentMicros, acct.HeldMicros, billed)
			}
			obs := probe.observations()
			if len(obs) != 1 || obs[0].settled != billed || obs[0].unreadable != "" || obs[0].costSource == "catalog_max" {
				t.Fatalf("spend guard window = %+v, want one settle at the estimate %d and no dispatch marker", obs, billed)
			}
		})
	}
}

// A managed stream the upstream ended itself keeps its price on both
// surfaces: the usage it reported (the upstream's charge plus 5%), or, when it
// sent none, the estimate. Neither is marked as dispatched without usage nor
// charged at the hold.
func TestManagedStreamEndedByTheUpstreamKeepsItsPrice(t *testing.T) {
	for _, surface := range managedSurfaces {
		t.Run(surface+" with usage", func(t *testing.T) {
			probe := &atHoldWindowProbe{}
			g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{extraPlugins: []pipeline.Plugin{probe}})
			g.ledger.SetMicros("org-m", auth.USDToMicros(1))

			atHoldStreamToEnd(t, g, surface, atHoldStreamBody(surface), nil)

			want := int64(math.Round(upstreamCost * 1.05 * 1e6))
			recs := g.outbox("org-m")
			if len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["billed_micros"] != strconv.FormatInt(want, 10) {
				t.Fatalf("settlement records = %v, want one spend at the reported %d", recs, want)
			}
			if recs[0]["cost_source"] == "catalog_max" || recs[0]["usage_estimated"] == "1" {
				t.Errorf("settlement = %v, want the reported usage, not the hold", recs[0])
			}
			obs := probe.observations()
			if len(obs) != 1 || obs[0].settled != want || obs[0].unreadable != "" {
				t.Fatalf("spend guard window = %+v, want one settle at %d and no dispatch marker", obs, want)
			}
		})
		t.Run(surface+" without usage", func(t *testing.T) {
			probe := &atHoldWindowProbe{}
			g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{extraPlugins: []pipeline.Plugin{probe}})
			g.ledger.SetMicros("org-m", auth.USDToMicros(1))
			g.managed.noUsage.Store(true)

			atHoldStreamToEnd(t, g, surface, atHoldStreamBody(surface), nil)

			recs := g.outbox("org-m")
			if len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["usage_estimated"] != "1" {
				t.Fatalf("settlement records = %v, want one spend on an estimate", recs)
			}
			if recs[0]["cost_source"] == "catalog_max" || recs[0]["billed_micros"] == recs[0]["hold_micros"] {
				t.Errorf("settlement = %v, want the estimate, not the hold", recs[0])
			}
			obs := probe.observations()
			if len(obs) != 1 || obs[0].settled <= 0 || obs[0].unreadable != "" {
				t.Fatalf("spend guard window = %+v, want one settle on the estimate and no dispatch marker", obs)
			}
		})
	}
}

// A stream on the org's own provider key that its upstream drops after its
// first text is unchanged: no managed hold, no settlement, no dispatch
// marker.
func TestOwnKeyStreamDropIsUnchanged(t *testing.T) {
	probe := &atHoldWindowProbe{}
	g := newManagedTestGatewayWith(t, &tenant.OrgConfig{
		Providers: map[string]*tenant.ProviderConfig{
			"operator": {APIKey: "org-operator-key", APIFormat: "openai", Models: []string{"org-model"}, Enabled: true},
		},
	}, managedGatewayOptions{extraPlugins: []pipeline.Plugin{probe}})
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))
	atHoldDroppingUpstream(g, g.operator)

	atHoldStreamToEnd(t, g, "/v1/chat/completions", `{"model":"org-model","stream":true,"messages":[{"role":"user","content":"hi"}]}`, nil)

	if g.operator.calls() != 1 || g.managed.calls() != 0 {
		t.Fatalf("operator calls = %d, managed calls = %d; want one own-key call", g.operator.calls(), g.managed.calls())
	}
	if got := g.operator.lastAuth(); got != "Bearer org-operator-key" {
		t.Fatalf("the call was sent with %q, want the org's own key", got)
	}
	if recs := g.outbox("org-m"); len(recs) != 0 {
		t.Fatalf("an own-key stream reached the managed ledger: %v", recs)
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
