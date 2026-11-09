package server

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/realtime"
)

const (
	// Short synthetic markers keep the original leaking close frame valid.
	r4dURLCanary    = "r4d-URL"
	r4dHeaderCanary = "r4d-HDR"
	r4dRequestID    = "r4d-correlation-fixture"
)

// The malformed port makes the real Gorilla dialer fail before any network
// connection. Its parse error echoes the complete URL, including userinfo and
// a mirror of the planted header value. Both values also occur in the provider
// URL/header diagnostics. No real credentials or non-loopback sockets are used.
func r4dRealtimeURL() string {
	return "ws://u:" + r4dURLCanary + "@127.0.0.1:x/?h=" + r4dHeaderCanary
}

type r4dRealtimeProvider struct {
	realtimeStub
	stage string
}

func (p *r4dRealtimeProvider) RealtimeURL(string) (string, error) {
	if p.stage == "url" {
		return "", fmt.Errorf("URL setup failed: %s headers=%s", r4dRealtimeURL(), r4dHeaderCanary)
	}
	return r4dRealtimeURL(), nil
}

func (p *r4dRealtimeProvider) RealtimeHeaders(string) (http.Header, error) {
	if p.stage == "headers" {
		return nil, fmt.Errorf("header setup failed: %s headers=%s", r4dRealtimeURL(), r4dHeaderCanary)
	}
	return http.Header{"Authorization": {r4dHeaderCanary}}, nil
}

type r4dLogBuffer struct {
	mu sync.Mutex
	bytes.Buffer
}

func (b *r4dLogBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.Buffer.Write(p)
}

func (b *r4dLogBuffer) text() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.Buffer.String()
}

func r4dNoRealtimeCanary(t *testing.T, sink, text string) {
	t.Helper()
	for _, canary := range []string{r4dURLCanary, r4dHeaderCanary} {
		if strings.Contains(text, canary) {
			t.Errorf("%s retained a synthetic provider credential", sink)
		}
	}
}

func TestOptBR4DRealtimeProviderErrors(t *testing.T) {
	for _, stage := range []string{"url", "headers", "dial"} {
		t.Run(stage, func(t *testing.T) {
			registry, err := providers.NewRegistry(config.DefaultConfig())
			if err != nil {
				t.Fatal(err)
			}
			defer registry.Close()
			p := &r4dRealtimeProvider{stage: stage}
			registry.RegisterProvider(p.ID(), p, []string{rtModel})
			tracker := realtime.NewSessionTracker(1)
			h := NewRealtimeHandler(tracker, registry, nil, realtimeHandlerConfig{})
			var logs r4dLogBuffer
			h.logger = slog.New(slog.NewJSONHandler(&logs, nil))
			done := make(chan struct{})
			serve := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				defer close(done)
				h.HandleRealtime(w, r.WithContext(models.WithRequestID(r.Context(), r4dRequestID)))
			})
			if stage != "dial" {
				w := httptest.NewRecorder()
				serve.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/v1/realtime?model="+rtModel, nil))
				r4dNoRealtimeCanary(t, "HTTP body", w.Body.String())
				var body models.ErrorResponse
				if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
					t.Fatal(err)
				}
				if w.Code != http.StatusBadGateway || body.Error.Type != models.ErrTypeUpstream || body.Error.Code != "realtime_provider_"+stage+"_failed" {
					t.Errorf("wrong typed realtime refusal: status=%d code=%q type=%q", w.Code, body.Error.Code, body.Error.Type)
				}
				if !strings.Contains(body.Error.Message, r4dRequestID) || !strings.Contains(body.Error.Message, "support") {
					t.Error("HTTP refusal must be actionable and carry correlation id")
				}
			} else {
				// Prove the actual Dial error contains BOTH planted values before
				// using the same provider inputs through the handler.
				_, _, probeErr := (&websocket.Dialer{}).Dial(r4dRealtimeURL(), http.Header{"Authorization": {r4dHeaderCanary}})
				if probeErr == nil || !strings.Contains(probeErr.Error(), r4dURLCanary) || !strings.Contains(probeErr.Error(), r4dHeaderCanary) {
					t.Fatal("dial fixture does not echo both planted values")
				}
				srv := httptest.NewServer(serve)
				defer srv.Close()
				conn, resp, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(srv.URL, "http")+"/v1/realtime?model="+rtModel, nil)
				if err != nil {
					t.Fatal(err)
				}
				defer conn.Close()
				if resp.StatusCode != http.StatusSwitchingProtocols {
					t.Fatal("client was not upgraded")
				}
				_ = conn.SetReadDeadline(time.Now().Add(3 * time.Second))
				_, _, err = conn.ReadMessage()
				var closed *websocket.CloseError
				if !errors.As(err, &closed) {
					t.Fatalf("want close frame, got %T", err)
				}
				r4dNoRealtimeCanary(t, "WebSocket close frame", closed.Text)
				if closed.Code != 4502 {
					t.Errorf("close code=%d, want 4502", closed.Code)
				}
				if !strings.Contains(closed.Text, "realtime_provider_dial_failed") || !strings.Contains(closed.Text, r4dRequestID) || !strings.Contains(closed.Text, "support") {
					t.Error("close frame must be typed, actionable and correlated")
				}
			}
			select {
			case <-done:
			case <-time.After(3 * time.Second):
				t.Fatal("handler did not finish")
			}
			r4dNoRealtimeCanary(t, "log", logs.text())
			if !strings.Contains(logs.text(), r4dRequestID) {
				t.Error("provider failure log must carry correlation id")
			}
			if !tracker.TryAcquire("default") {
				t.Error("failure leaked a session slot")
			} else {
				tracker.Release("default")
			}
		})
	}
}
