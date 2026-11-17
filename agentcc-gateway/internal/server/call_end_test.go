package server

import (
	"context"
	"net/http"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/spendguard"
)

// A call the gateway sent upstream that ended before its answer finished
// carries call_end in its request metadata, the request log's record of why:
// client_cancelled, provider_stopped, deadline (the request's own time limit)
// or gateway_timeout (the gateway's timeout for the model). A call that
// finished, or that the provider answered with a status, carries none, and a
// caller cannot set it. The charge is the same as before for every cause.

// callEndProbe records the call_end each request carried when the
// post-plugins ran, as the request log reads it.
type callEndProbe struct {
	mu   sync.Mutex
	seen []string
}

func (p *callEndProbe) Name() string      { return "call-end-probe" }
func (p *callEndProbe) Priority() int     { return spendguard.Priority }
func (p *callEndProbe) PostPriority() int { return spendguard.PostPriority }

func (p *callEndProbe) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

func (p *callEndProbe) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.mu.Lock()
	p.seen = append(p.seen, rc.Metadata["call_end"])
	p.mu.Unlock()
	return pipeline.ResultContinue()
}

// one is the call_end of the one request the probe saw.
func (p *callEndProbe) one(t *testing.T) string {
	t.Helper()
	p.mu.Lock()
	defer p.mu.Unlock()
	if len(p.seen) != 1 {
		t.Fatalf("requests seen = %d (%q), want one", len(p.seen), p.seen)
	}
	return p.seen[0]
}

// callEndGateway is the managed test gateway with both probes, the org on
// credits with a balance, and configure applied to the config.
func callEndGateway(t *testing.T, configure func(*config.Config)) (*managedTestGateway, *atHoldWindowProbe, *callEndProbe) {
	t.Helper()
	window := &atHoldWindowProbe{}
	ends := &callEndProbe{}
	g := newManagedTestGatewayWith(t, managedOptIn(), managedGatewayOptions{
		extraPlugins: []pipeline.Plugin{window, ends},
		configure:    configure,
	})
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))
	return g, window, ends
}

// modelTimeout sets the gateway's own timeout for the managed model.
func modelTimeout(c *config.Config) {
	c.Routing.ModelTimeouts = map[string]time.Duration{"anthropic/claude-sonnet-5.5": 300 * time.Millisecond}
}

// callEndAfterHeadersUpstream answers a non-streamed call with its 200
// headers and the start of a body, signals dispatched, then waits for the
// gateway to end the call or, with drop, drops the connection.
func callEndAfterHeadersUpstream(g *managedTestGateway, u *recordingUpstream, drop bool) (held *atomic.Int64, dispatched chan struct{}) {
	held = &atomic.Int64{}
	dispatched = make(chan struct{}, 4)
	answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
		if acct, err := g.ledger.Account(context.Background(), "org-m"); err == nil {
			held.Store(acct.HeldMicros)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"id":"c1","object":"chat.completion","model":"` + model + `","choices":[`))
		if f, ok := w.(http.Flusher); ok {
			f.Flush()
		}
		select {
		case dispatched <- struct{}{}:
		default:
		}
		if drop {
			panic(http.ErrAbortHandler)
		}
		select {
		case <-r.Context().Done():
		case <-time.After(10 * time.Second):
		}
	})
	u.responseWith.Store(&answer)
	return held, dispatched
}

func TestCallEndNonStreamNamesWhyTheCallEnded(t *testing.T) {
	body := chatBody("anthropic/claude-sonnet-5.5")
	t.Run("the client cancels before the provider answers", func(t *testing.T) {
		g, window, ends := callEndGateway(t, nil)
		held, dispatched := atHoldHangingUpstream(g, g.managed, false)
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		go cancelOnDispatch(dispatched, cancel)

		atHoldChat(ctx, g, body, nil)

		if got := ends.one(t); got != "client_cancelled" {
			t.Fatalf("call_end = %q, want client_cancelled", got)
		}
		assertSettledAtHold(t, g, window, held.Load())
	})
	t.Run("the client cancels after the provider's headers", func(t *testing.T) {
		g, window, ends := callEndGateway(t, nil)
		held, dispatched := callEndAfterHeadersUpstream(g, g.managed, false)
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		go cancelOnDispatch(dispatched, cancel)

		atHoldChat(ctx, g, body, nil)

		if got := ends.one(t); got != "client_cancelled" {
			t.Fatalf("call_end = %q, want client_cancelled", got)
		}
		assertSettledAtHold(t, g, window, held.Load())
	})
	t.Run("the provider drops the call after its headers", func(t *testing.T) {
		g, window, ends := callEndGateway(t, nil)
		held, _ := callEndAfterHeadersUpstream(g, g.managed, true)

		w := atHoldChat(context.Background(), g, body, nil)

		if w.Code == http.StatusOK {
			t.Fatalf("a dropped call answered 200: %s", w.Body)
		}
		if got := ends.one(t); got != "provider_stopped" {
			t.Fatalf("call_end = %q, want provider_stopped", got)
		}
		assertSettledAtHold(t, g, window, held.Load())
	})
	t.Run("the client's own timeout cuts the call", func(t *testing.T) {
		g, window, ends := callEndGateway(t, nil)
		held, _ := atHoldHangingUpstream(g, g.managed, false)

		atHoldChat(context.Background(), g, body, map[string]string{"x-agentcc-request-timeout": "300"})

		if got := ends.one(t); got != "deadline" {
			t.Fatalf("call_end = %q, want deadline", got)
		}
		assertSettledAtHold(t, g, window, held.Load())
	})
	t.Run("the request's deadline cuts the call", func(t *testing.T) {
		g, _, ends := callEndGateway(t, nil)
		atHoldHangingUpstream(g, g.managed, false)

		atHoldChat(context.Background(), g, body, map[string]string{"x-agentcc-deadline-ms": "300"})

		if got := ends.one(t); got != "deadline" {
			t.Fatalf("call_end = %q, want deadline", got)
		}
	})
	t.Run("the gateway's timeout for the model cuts the call", func(t *testing.T) {
		g, window, ends := callEndGateway(t, modelTimeout)
		held, _ := atHoldHangingUpstream(g, g.managed, false)

		atHoldChat(context.Background(), g, body, nil)

		if got := ends.one(t); got != "gateway_timeout" {
			t.Fatalf("call_end = %q, want gateway_timeout", got)
		}
		assertSettledAtHold(t, g, window, held.Load())
	})
	t.Run("the gateway's time limit for the provider cuts the call", func(t *testing.T) {
		g, window, ends := callEndGateway(t, func(c *config.Config) {
			p := c.Providers["openrouter"]
			p.DefaultTimeout = attemptTimeout
			c.Providers["openrouter"] = p
		})
		held, _ := atHoldHangingUpstream(g, g.managed, false)

		atHoldChat(context.Background(), g, body, nil)

		if got := ends.one(t); got != "gateway_timeout" {
			t.Fatalf("call_end = %q, want gateway_timeout", got)
		}
		assertSettledAtHold(t, g, window, held.Load())
	})
}

func TestCallEndIsNotSetOnACallThatFinishedOrWasAnswered(t *testing.T) {
	body := chatBody("anthropic/claude-sonnet-5.5")
	t.Run("the provider answers", func(t *testing.T) {
		g, _, ends := callEndGateway(t, nil)

		if w := atHoldChat(context.Background(), g, body, nil); w.Code != http.StatusOK {
			t.Fatalf("status = %d body %s, want 200", w.Code, w.Body)
		}
		if got := ends.one(t); got != "" {
			t.Fatalf("call_end = %q on a call that finished, want none", got)
		}
	})
	t.Run("the provider answers with a 5xx", func(t *testing.T) {
		g, _, ends := callEndGateway(t, nil)
		g.managed.status.Store(http.StatusServiceUnavailable)

		atHoldChat(context.Background(), g, body, nil)

		if got := ends.one(t); got != "" {
			t.Fatalf("call_end = %q on a call the provider answered, want none", got)
		}
	})
	t.Run("a fallback serves after a timed-out primary", func(t *testing.T) {
		ends := &callEndProbe{}
		g := timeoutGatewayFor(t, managedOptIn(), ends, nil)
		attemptsUpstream(g, "anthropic/claude-sonnet-5.5")

		w := atHoldChat(context.Background(), g, attemptsBody("anthropic/claude-sonnet-5.5", "openai/gpt-6-sol"), nil)

		if w.Code != http.StatusOK {
			t.Fatalf("status = %d body %s, want the fallback's answer", w.Code, w.Body)
		}
		if got := ends.one(t); got != "" {
			t.Fatalf("call_end = %q after the fallback served, want none", got)
		}
	})
}

func TestCallEndIsTheGatewaysNeverTheCallers(t *testing.T) {
	body := chatBody("anthropic/claude-sonnet-5.5")
	forged := map[string]string{"x-agentcc-metadata": `{"call_end":"provider_stopped","tenant":"acme"}`}
	t.Run("on a call that finished", func(t *testing.T) {
		g, _, ends := callEndGateway(t, nil)

		if w := atHoldChat(context.Background(), g, body, forged); w.Code != http.StatusOK {
			t.Fatalf("status = %d body %s, want 200", w.Code, w.Body)
		}
		if got := ends.one(t); got != "" {
			t.Fatalf("call_end = %q, want the caller's value dropped", got)
		}
	})
	t.Run("on a call the client cancelled", func(t *testing.T) {
		g, _, ends := callEndGateway(t, nil)
		_, dispatched := atHoldHangingUpstream(g, g.managed, false)
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		go cancelOnDispatch(dispatched, cancel)

		atHoldChat(ctx, g, body, forged)

		if got := ends.one(t); got != "client_cancelled" {
			t.Fatalf("call_end = %q, want the gateway's client_cancelled", got)
		}
	})
}

func TestCallEndStreamNamesWhyTheStreamEnded(t *testing.T) {
	for _, surface := range managedSurfaces {
		t.Run(surface+": the provider drops the stream", func(t *testing.T) {
			g, window, ends := callEndGateway(t, nil)
			held := atHoldDroppingUpstream(g, g.managed)

			atHoldStreamToEnd(t, g, surface, atHoldStreamBody(surface), nil)

			if got := ends.one(t); got != "provider_stopped" {
				t.Fatalf("call_end = %q, want provider_stopped", got)
			}
			assertSettledAtHold(t, g, window, held.Load())
		})
		t.Run(surface+": the client's own timeout cuts the stream", func(t *testing.T) {
			g, window, ends := callEndGateway(t, nil)
			held, _ := atHoldHangingUpstream(g, g.managed, true)

			atHoldStreamToEnd(t, g, surface, atHoldStreamBody(surface), map[string]string{"x-agentcc-request-timeout": "300"})

			if got := ends.one(t); got != "deadline" {
				t.Fatalf("call_end = %q, want deadline", got)
			}
			assertSettledAtHold(t, g, window, held.Load())
		})
		t.Run(surface+": the request's deadline cuts the stream", func(t *testing.T) {
			if surface != "/v1/chat/completions" {
				// x-agentcc-deadline-ms does not reach a /v1/messages
				// stream's context today (that surface reads the agent
				// context for its call cap only), so no deadline cuts it.
				t.Skip("x-agentcc-deadline-ms does not cut a /v1/messages stream")
			}
			g, _, ends := callEndGateway(t, nil)
			atHoldHangingUpstream(g, g.managed, true)

			atHoldStreamToEnd(t, g, surface, atHoldStreamBody(surface), map[string]string{"x-agentcc-deadline-ms": "300"})

			if got := ends.one(t); got != "deadline" {
				t.Fatalf("call_end = %q, want deadline", got)
			}
		})
		t.Run(surface+": the gateway's timeout for the model cuts the stream", func(t *testing.T) {
			g, _, ends := callEndGateway(t, modelTimeout)
			atHoldHangingUpstream(g, g.managed, true)

			atHoldStreamToEnd(t, g, surface, atHoldStreamBody(surface), nil)

			if got := ends.one(t); got != "gateway_timeout" {
				t.Fatalf("call_end = %q, want gateway_timeout", got)
			}
		})
		t.Run(surface+": the client stops the stream", func(t *testing.T) {
			g, window, ends := callEndGateway(t, nil)
			atHoldHangingUpstream(g, g.managed, true)

			atHoldStreamAndLeave(t, g, surface, atHoldStreamBody(surface))

			if got := ends.one(t); got != "client_cancelled" {
				t.Fatalf("call_end = %q, want client_cancelled", got)
			}
			// Charged on what was received, never at the hold, as before.
			if obs := window.observations(); len(obs) != 1 || obs[0].unreadable != "" || obs[0].costSource == "catalog_max" {
				t.Fatalf("spend guard window = %+v, want the estimate and no dispatch marker", obs)
			}
		})
		t.Run(surface+": the stream ends at the provider's close", func(t *testing.T) {
			g, _, ends := callEndGateway(t, nil)

			atHoldStreamToEnd(t, g, surface, atHoldStreamBody(surface), nil)

			if got := ends.one(t); got != "" {
				t.Fatalf("call_end = %q on a stream that finished, want none", got)
			}
		})
	}
}
