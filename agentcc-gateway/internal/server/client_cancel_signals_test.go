package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// A request its client cancelled says nothing about its provider. It counts
// toward neither the provider's health nor its latency, as neither a failure
// nor a success. A provider's own outcome, with the client still connected,
// still counts in both. A managed request the client cancelled is charged as
// before. (The circuit breaker's record site is tested in package routing.)

const cancelSigKey = "sk-client-cancel-signals"

// cancelSigUpstream answers as mode says: "hang" sends a stream's first chunk
// (for a stream), signals dispatched and waits until the gateway ends the
// call; "fail" answers a provider failure; "serve" answers after a short
// pause, so the call has a latency to record.
func cancelSigUpstream(t *testing.T, mode string) (url string, dispatched chan struct{}) {
	t.Helper()
	dispatched = make(chan struct{}, 8)
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			return
		}
		switch mode {
		case "hang":
			if strings.Contains(cancelSigBody(r), `"stream":true`) {
				w.Header().Set("Content-Type", "text/event-stream")
				_, _ = w.Write([]byte(`data: {"id":"c1","object":"chat.completion.chunk","model":"gpt-4o","choices":[{"index":0,"delta":{"role":"assistant","content":"partial answer"}}]}` + "\n\n"))
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
		case "fail":
			w.WriteHeader(http.StatusInternalServerError)
			_, _ = w.Write([]byte(`{"error":{"message":"upstream failed","type":"server_error"}}`))
		default:
			time.Sleep(30 * time.Millisecond)
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"id":"c1","object":"chat.completion","created":1,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`))
		}
	}))
	t.Cleanup(up.Close)
	return up.URL, dispatched
}

func cancelSigBody(r *http.Request) string {
	var b strings.Builder
	buf := make([]byte, 4096)
	for {
		n, err := r.Body.Read(buf)
		b.Write(buf[:n])
		if err != nil {
			return b.String()
		}
	}
}

// cancelSigGateway is a gateway with one provider, "only", for gpt-4o behind a
// router (so the latency tracker is in use) and the node's failover.
func cancelSigGateway(t *testing.T, upstream string) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "k", Key: cancelSigKey, Owner: "t", KeyType: "internal"}}
	cfg.Providers["only"] = config.ProviderConfig{BaseURL: upstream, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Routing.DefaultStrategy = "round-robin"
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"gpt-4o": {{Provider: "only", Weight: 1}}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { registry.Close() })
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	if registry.Router() == nil {
		t.Fatal("the gateway has no router")
	}
	srv.handlers.failover.Store(routing.NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 1}, registry.Router(), nil, nil))
	return srv
}

// cancelSigChat sends one chat request (a stream when stream is set) on ctx.
func cancelSigChat(ctx context.Context, srv *Server, stream bool) int {
	body := `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`
	if stream {
		body = `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`
	}
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body)).WithContext(ctx)
	req.Header.Set("Authorization", "Bearer "+cancelSigKey)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w.Code
}

// cancelSigCancelledChat sends one chat request that its client cancels once the
// upstream has it, after a pause long enough for a latency to be measured.
func cancelSigCancelledChat(srv *Server, dispatched <-chan struct{}, stream bool) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() {
		select {
		case <-dispatched:
			time.Sleep(30 * time.Millisecond)
			cancel()
		case <-time.After(10 * time.Second):
		}
	}()
	cancelSigChat(ctx, srv, stream)
}

func TestClientCancelledRequestCountsTowardNoProviderHealthOrLatency(t *testing.T) {
	for _, stream := range []bool{false, true} {
		name := "non-stream"
		if stream {
			name = "stream"
		}
		t.Run(name, func(t *testing.T) {
			upstream, dispatched := cancelSigUpstream(t, "hang")
			srv := cancelSigGateway(t, upstream)

			for i := 0; i < 2; i++ {
				cancelSigCancelledChat(srv, dispatched, stream)
			}

			if h := srv.handlers.healthMonitor.GetHealth("only"); h.RequestCount != 0 || h.ErrorCount != 0 {
				t.Errorf("provider health: %d requests, %d errors (last error %q) after requests the client cancelled; want none counted",
					h.RequestCount, h.ErrorCount, h.LastError)
			}
			if got := srv.registry.Router().GetLatencyTracker().Get("only"); got != 0 {
				t.Errorf("provider latency = %v ms after requests the client cancelled; want none recorded", got)
			}
		})
	}
}

func TestProviderOutcomeWithTheClientConnectedStillCountsInHealthAndLatency(t *testing.T) {
	t.Run("a provider failure counts as an error", func(t *testing.T) {
		upstream, _ := cancelSigUpstream(t, "fail")
		srv := cancelSigGateway(t, upstream)

		for i := 0; i < 2; i++ {
			if code := cancelSigChat(context.Background(), srv, false); code < 400 {
				t.Fatalf("status %d, want the provider's failure", code)
			}
		}

		if h := srv.handlers.healthMonitor.GetHealth("only"); h.RequestCount != 2 || h.ErrorCount != 2 {
			t.Errorf("provider health: %d requests, %d errors; want both failures counted", h.RequestCount, h.ErrorCount)
		}
	})
	t.Run("a served call counts as a success with its latency", func(t *testing.T) {
		upstream, _ := cancelSigUpstream(t, "serve")
		srv := cancelSigGateway(t, upstream)

		if code := cancelSigChat(context.Background(), srv, false); code != http.StatusOK {
			t.Fatalf("status %d, want 200", code)
		}

		if h := srv.handlers.healthMonitor.GetHealth("only"); h.RequestCount != 1 || h.ErrorCount != 0 {
			t.Errorf("provider health: %d requests, %d errors; want one success", h.RequestCount, h.ErrorCount)
		}
		if got := srv.registry.Router().GetLatencyTracker().Get("only"); got <= 0 {
			t.Errorf("provider latency = %v ms after a served call; want it recorded", got)
		}
	})
}

// A managed (Future AGI credits) request its client cancels after the
// upstream has it is charged as before, at its attempt's hold, and counts
// toward no provider's health.
func TestManagedClientCancelledRequestKeepsItsChargeAndCountsTowardNoHealth(t *testing.T) {
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
	for _, h := range g.srv.handlers.healthMonitor.GetAllHealth() {
		if h.RequestCount != 0 || h.ErrorCount != 0 {
			t.Errorf("provider %s health: %d requests, %d errors after a request the client cancelled; want none counted",
				h.ProviderID, h.RequestCount, h.ErrorCount)
		}
	}
}
