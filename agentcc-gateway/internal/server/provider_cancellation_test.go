package server

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// Hold stream opening until the real adapter has queued its normalized error.
// This exercises the race where both the error and ctx.Done are ready on entry.
type adaptiveBufferedStreamProvider struct {
	providers.Provider
}

func (p *adaptiveBufferedStreamProvider) StreamChatCompletion(ctx context.Context, req *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks, errs := p.Provider.StreamChatCompletion(ctx, req)
	ready := make(chan error, 1)
	if err, ok := <-errs; ok {
		ready <- err
	}
	close(ready)
	return chunks, ready
}

func TestAdaptiveHealthHTTPClientCancellation(t *testing.T) {
	for _, mode := range []string{"chat", "native", "translated", "chat-stream", "legacy-stream", "chat-stream-race", "legacy-stream-race"} {
		for _, cancelClient := range []bool{true, false} {
			t.Run(fmt.Sprintf("%s/client-cancel=%t", mode, cancelClient), func(t *testing.T) {
				started := make(chan struct{}, 1)
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.Method == http.MethodGet {
						w.Header().Set("Content-Type", "application/json")
						fmt.Fprint(w, `{"object":"list","data":[]}`)
						return
					}
					// Read the body so the transport observes a disconnected peer.
					io.Copy(io.Discard, r.Body)
					started <- struct{}{}
					<-r.Context().Done()
				}))
				t.Cleanup(upstream.Close)
				cfg := config.DefaultConfig()
				cfg.Providers = map[string]config.ProviderConfig{"fast": completionHealthConfig(upstream.URL, mode)}
				cfg.Routing = config.RoutingConfig{DefaultStrategy: "adaptive"}
				if !cancelClient {
					cfg.Server.DefaultRequestTimeout = 100 * time.Millisecond
				}
				srv, _ := adaptiveServer(t, cfg)
				if strings.HasSuffix(mode, "-race") {
					provider, _ := srv.registry.GetProvider("fast")
					srv.registry.RegisterProvider("fast", &adaptiveBufferedStreamProvider{Provider: provider}, nil)
				}
				finished := make(chan struct{}, 1)
				gateway := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					srv.httpServer.Handler.ServeHTTP(w, r)
					finished <- struct{}{}
				}))
				t.Cleanup(gateway.Close)
				endpoint := "/v1/completions"
				input := `"prompt":"synthetic probe"`
				if strings.HasPrefix(mode, "chat") {
					endpoint = "/v1/chat/completions"
					input = `"messages":[{"role":"user","content":"synthetic probe"}]`
				}
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				body := fmt.Sprintf(`{"model":"adaptive-sample","stream":%t,%s}`, strings.Contains(mode, "stream"), input)
				req, err := http.NewRequestWithContext(ctx, http.MethodPost, gateway.URL+endpoint, strings.NewReader(body))
				if err != nil {
					t.Fatal(err)
				}
				req.Header.Set("Content-Type", "application/json")
				type response struct {
					status int
					err    error
				}
				result := make(chan response, 1)
				go func() {
					resp, err := gateway.Client().Do(req)
					status := 0
					if resp != nil {
						status = resp.StatusCode
						io.Copy(io.Discard, resp.Body)
						resp.Body.Close()
					}
					result <- response{status: status, err: err}
				}()
				select {
				case <-started:
				case <-time.After(5 * time.Second):
					t.Fatal("upstream was never called")
				}
				if cancelClient {
					cancel()
				}
				select {
				case got := <-result:
					if cancelClient && !errors.Is(got.err, context.Canceled) {
						t.Fatal("client did not observe cancellation")
					}
					// Chat stream-open can return the raw context error (500) or
					// the adapter's normalized timeout (504); both are upstream failures.
					deadlineStatus := got.status == http.StatusGatewayTimeout || (strings.HasPrefix(mode, "chat-stream") && got.status == http.StatusInternalServerError)
					if !cancelClient && (got.err != nil || !deadlineStatus) {
						t.Fatalf("deadline response status=%d", got.status)
					}
				case <-time.After(5 * time.Second):
					t.Fatal("client did not finish")
				}
				select {
				case <-finished:
				case <-time.After(5 * time.Second):
					t.Fatal("gateway did not finish after cancellation/deadline")
				}
				health := srv.handlers.healthMonitor.GetHealth("fast")
				want := int64(1)
				if cancelClient {
					want = 0
				}
				if health.RequestCount != want || health.ErrorCount != want || (cancelClient && health.LatencyEWMAMs != 0) {
					t.Fatalf("cancellation classification: samples=%d errors=%d latency=%d want_samples=%d", health.RequestCount, health.ErrorCount, health.LatencyEWMAMs, want)
				}
			})
		}
	}
}

func TestAdaptiveCanceledContextOverridesAdapterOutcome(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{"fast": {APIFormat: "openai", BaseURL: "http://synthetic.invalid", Models: []string{"adaptive-sample"}}}
	cfg.Routing = config.RoutingConfig{DefaultStrategy: "adaptive"}
	srv, _ := adaptiveServer(t, cfg)
	provider, _ := srv.registry.GetProvider("fast")
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	// Model both sides of the stream-open race: a queued normalized error or a
	// completed stream may win select at the same time as the cancelled context.
	for _, outcome := range []error{models.ErrGatewayTimeout("synthetic cancellation"), nil} {
		srv.handlers.recordProviderOutcome(ctx, provider, time.Second, outcome)
	}
	health := srv.handlers.healthMonitor.GetHealth("fast")
	if health.RequestCount != 0 || health.ErrorCount != 0 || health.LatencyEWMAMs != 0 {
		t.Fatalf("canceled stream race polluted health: samples=%d errors=%d latency=%d", health.RequestCount, health.ErrorCount, health.LatencyEWMAMs)
	}
}
