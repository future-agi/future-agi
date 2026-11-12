package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// A managed stream start that was sent upstream and got no answer at all
// (the request's deadline cut it before any response byte, or the
// connection dropped once the upstream had the request) settles at its
// attempt's hold, the same class as a non-stream call sent and left
// unanswered (on the same
// dispatchTrace signal). A start that got an upstream status stays void, and
// a start its caller stopped before any answer stays void.

// atHoldSilentStreamUpstream makes u's stream record the org's hold while the
// call is in flight, signal that it has the call, and then answer nothing (no
// status, no byte) until the gateway gives up on it.
func atHoldSilentStreamUpstream(g *managedTestGateway, u *recordingUpstream) (held *atomic.Int64, dispatched chan struct{}) {
	held = &atomic.Int64{}
	dispatched = make(chan struct{}, 4)
	silent := upstreamStream(func(_ http.ResponseWriter, r *http.Request, _ string) {
		if acct, err := g.ledger.Account(context.Background(), "org-m"); err == nil {
			held.Store(acct.HeldMicros)
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
	u.streamWith.Store(&silent)
	return held, dispatched
}

// atHoldStreamStart sends a managed stream request on surface with ctx and
// headers, through the gateway's handler, and returns once it has finished.
func atHoldStreamStart(ctx context.Context, g *managedTestGateway, surface string, headers map[string]string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, surface, strings.NewReader(atHoldStreamBody(surface))).WithContext(ctx)
	req.Header.Set("Authorization", "Bearer "+managedOrgKey)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("anthropic-version", "2023-06-01")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// A managed stream start the request's deadline cuts after the upstream has
// the call and before it answers anything settles at its hold on both
// surfaces: one spend at the hold, cost source catalog_max, the dispatch
// marker, the window once.
func TestManagedStreamStartSentWithoutAnswerSettlesAtHold(t *testing.T) {
	for _, surface := range managedSurfaces {
		t.Run(surface, func(t *testing.T) {
			probe := &atHoldWindowProbe{}
			g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{extraPlugins: []pipeline.Plugin{probe}})
			g.ledger.SetMicros("org-m", auth.USDToMicros(1))
			held, _ := atHoldSilentStreamUpstream(g, g.managed)

			w := atHoldStreamStart(context.Background(), g, surface, map[string]string{"x-agentcc-request-timeout": "300"})

			if w.Code == http.StatusOK {
				t.Fatalf("an unanswered stream start answered 200: %s", w.Body)
			}
			if g.managed.calls() != 1 {
				t.Fatalf("upstream calls = %d, want 1", g.managed.calls())
			}
			assertSettledAtHold(t, g, probe, held.Load())
		})
	}
}

// A managed stream start whose connection drops once the upstream has
// accepted it, before any model output or usage, settles at its hold on both
// surfaces: the upstream had the request, and what it used is unknown.
func TestManagedStreamStartDroppedAfterSendSettlesAtHold(t *testing.T) {
	for _, surface := range managedSurfaces {
		t.Run(surface, func(t *testing.T) {
			g := newManagedTestGateway(t, managedOptIn())
			g.ledger.SetMicros("org-m", auth.USDToMicros(1))
			up := failedAcceptedStream("drop")
			g.managed.streamWith.Store(&up)

			status, body, _ := sendManaged(t, g, surface, decoratedModel, true, false, nil)

			spent, held := settledAccount(t, g)
			recs := g.outbox("org-m")
			if status != http.StatusBadGateway || held != 0 || g.managed.calls() != 1 || len(recs) != 1 {
				t.Fatalf("status=%d spent=%d held=%d calls=%d records=%v body=%s; want one 502 settled once", status, spent, held, g.managed.calls(), recs, body)
			}
			rec := recs[0]
			if rec["kind"] != "spend" || rec["billed_micros"] != rec["hold_micros"] || rec["hold_micros"] == "0" || rec["cost_source"] != "catalog_max" || rec["usage_estimated"] != "1" {
				t.Fatalf("settlement = %v, want one spend at the start's own hold, catalog_max", rec)
			}
			if billed := rec["billed_micros"]; strconv.FormatInt(spent, 10) != billed || spent <= 0 {
				t.Fatalf("account spent = %d, want the hold %s", spent, billed)
			}
		})
	}
}

// A managed stream start its caller stops after the upstream has the call
// and before any answer stays void on both surfaces: nothing spent, nothing
// held, no spend recorded, no dispatch marker.
func TestManagedStreamStartCallerStopBeforeAnswerStaysVoid(t *testing.T) {
	for _, surface := range managedSurfaces {
		t.Run(surface, func(t *testing.T) {
			probe := &atHoldWindowProbe{}
			g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{extraPlugins: []pipeline.Plugin{probe}})
			g.ledger.SetMicros("org-m", auth.USDToMicros(1))
			held, dispatched := atHoldSilentStreamUpstream(g, g.managed)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			go cancelOnDispatch(dispatched, cancel)

			atHoldStreamStart(ctx, g, surface, nil)

			if g.managed.calls() != 1 {
				t.Fatalf("upstream calls = %d, want 1", g.managed.calls())
			}
			if held.Load() <= 0 {
				t.Fatalf("no hold was observed while the call was in flight (%d)", held.Load())
			}
			spent, stillHeld := settledAccount(t, g)
			if spent != 0 || stillHeld != 0 {
				t.Fatalf("account spent=%d held=%d, want a void start: nothing spent, nothing held", spent, stillHeld)
			}
			for _, rec := range g.outbox("org-m") {
				if rec["kind"] == "spend" {
					t.Fatalf("a start the caller stopped was charged: %v", rec)
				}
			}
			for _, obs := range probe.observations() {
				if obs.unreadable != "" || obs.settled != 0 {
					t.Fatalf("request evidence = %+v, want no dispatch marker and nothing settled", obs)
				}
			}
		})
	}
}

// A managed stream start the upstream refuses with a status stays void on
// both surfaces: a status is an answer, and a refusal is not billed.
func TestManagedStreamStartUpstreamStatusStaysVoid(t *testing.T) {
	for _, surface := range managedSurfaces {
		t.Run(surface, func(t *testing.T) {
			probe := &atHoldWindowProbe{}
			g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{extraPlugins: []pipeline.Plugin{probe}})
			g.ledger.SetMicros("org-m", auth.USDToMicros(1))
			refuse := upstreamStream(func(w http.ResponseWriter, _ *http.Request, _ string) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusBadRequest)
				_, _ = w.Write([]byte(`{"error":{"message":"bad request","type":"invalid_request_error"}}`))
			})
			g.managed.streamWith.Store(&refuse)

			w := atHoldStreamStart(context.Background(), g, surface, nil)

			if w.Code == http.StatusOK {
				t.Fatalf("a refused stream start answered 200: %s", w.Body)
			}
			spent, held := settledAccount(t, g)
			if spent != 0 || held != 0 {
				t.Fatalf("account spent=%d held=%d, want a void start", spent, held)
			}
			for _, rec := range g.outbox("org-m") {
				if rec["kind"] == "spend" {
					t.Fatalf("a refused start was charged: %v", rec)
				}
			}
			for _, obs := range probe.observations() {
				if obs.unreadable != "" {
					t.Fatalf("request evidence = %+v, want no dispatch marker", obs)
				}
			}
		})
	}
}
