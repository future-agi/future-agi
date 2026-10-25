package server

// x-agentcc-deadline-ms (alias x-omega-deadline-ms, body
// metadata.agent.deadline_ms) bounds the request's upstream attempts.

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// delayedUpstream is an OpenAI-shaped upstream that answers a chat call
// after delay, unless the call's connection ends first. It counts the chat
// calls it got and the ones the caller cut before it answered.
type delayedUpstream struct {
	*httptest.Server
	calls atomic.Int32
	cut   atomic.Int32
}

func startDelayedUpstream(t *testing.T, delay time.Duration) *delayedUpstream {
	t.Helper()
	u := &delayedUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"object":"list","data":[]}`))
			return
		}
		u.calls.Add(1)
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		select {
		case <-time.After(delay):
		case <-r.Context().Done():
			u.cut.Add(1)
			return
		}
		model, _ := body["model"].(string)
		if stream, _ := body["stream"].(bool); stream {
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprintf(w, "data: {\"id\":\"chatcmpl-s\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"ok\"},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":3,\"completion_tokens\":1,\"total_tokens\":4}}\n\n", model)
			fmt.Fprint(w, "data: [DONE]\n\n")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"chatcmpl-s","object":"chat.completion","created":1,"model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}}`, model)
	}))
	t.Cleanup(u.Close)
	return u
}

// waitCut waits until the upstream saw n cut calls, or fails.
func (u *delayedUpstream) waitCut(t *testing.T, n int32) {
	t.Helper()
	for deadline := time.Now().Add(3 * time.Second); u.cut.Load() < n; time.Sleep(5 * time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatalf("the upstream saw %d cut calls, want %d: the deadline did not cancel the upstream call", u.cut.Load(), n)
		}
	}
}

// deadlineSleepPlugin is a pre-plugin that takes d, so a short deadline passes
// before the first attempt.
type deadlineSleepPlugin struct{ d time.Duration }

func (p deadlineSleepPlugin) Name() string  { return "deadline-sleep" }
func (p deadlineSleepPlugin) Priority() int { return 990 }
func (p deadlineSleepPlugin) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	time.Sleep(p.d)
	return pipeline.ResultContinue()
}
func (p deadlineSleepPlugin) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// deadlineAttempts records the request's attempts each time one finishes.
type deadlineAttempts struct {
	mu   sync.Mutex
	last []models.Attempt
}

func (a *deadlineAttempts) observe(hook, _ string, rc *models.RequestContext) {
	if hook != hookFinishAttempt {
		return
	}
	a.mu.Lock()
	a.last = rc.AttemptsSnapshot()
	a.mu.Unlock()
}

func (a *deadlineAttempts) snapshot() []models.Attempt {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]models.Attempt(nil), a.last...)
}

// deadlineServer is a real server with one provider, "openai",
// serving gpt-4o on upstreamURL, no retries and no failover (the default
// routing), the internal key sk-hook and the extra pre-plugins.
func deadlineServer(t *testing.T, upstreamURL string, extra ...pipeline.Plugin) (*hookOrderTest, *deadlineAttempts) {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "hook", Key: "sk-hook", Owner: "t", KeyType: "internal"}}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstreamURL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Routing.ProviderLock = config.ProviderLockConfig{Enabled: true}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	plugins := append([]pipeline.Plugin{authplugin.New(keyStore, true)}, extra...)
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	attempts := &deadlineAttempts{}
	srv.handlers.hookObserver = attempts.observe
	return &hookOrderTest{srv: srv, ev: &hookEvents{}}, attempts
}

func deadlineErrorCode(b []byte) string {
	var e models.ErrorResponse
	if json.Unmarshal(b, &e) != nil {
		return ""
	}
	return e.Error.Code
}

// TestAgentDeadline: a 100 ms deadline against a 2 s upstream ends the call
// with 504 gateway_timeout (the dispatched
// attempt's own timeout) well before the upstream would answer, and the
// upstream's connection is cut: the deadline cancels the upstream call, it
// does not just stop waiting. The header, its x-omega alias and the body field
// all set it. A stream that has not started by the deadline ends at it too,
// with its upstream cut; its status is chat_stream.go's (504 through
// attemptLimitError, tested directly below).
// A deadline the call meets changes nothing.
func TestAgentDeadline(t *testing.T) {
	up := startDelayedUpstream(t, 2*time.Second)
	ht, _ := deadlineServer(t, up.URL)
	for _, c := range []struct {
		name    string
		body    string
		headers []string
		stream  bool
	}{
		{"x-agentcc-deadline-ms", hookChatBody, []string{"x-agentcc-deadline-ms", "100"}, false},
		{"x-omega-deadline-ms", hookChatBody, []string{"x-omega-deadline-ms", "100"}, false},
		{"metadata.agent.deadline_ms", `{"model":"gpt-4o","metadata":{"agent":{"deadline_ms":100}},"messages":[{"role":"user","content":"hi"}]}`, nil, false},
		{"stream before its first chunk", `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`, []string{"x-agentcc-deadline-ms", "100"}, true},
	} {
		t.Run(c.name, func(t *testing.T) {
			calls, cut := up.calls.Load(), up.cut.Load()
			start := time.Now()
			w := ht.chat(t, c.body, c.headers...)
			elapsed := time.Since(start)
			t.Logf("%s: HTTP %d after %s, code %q, x-agentcc-deadline-exceeded=%q", c.name, w.Code, elapsed.Round(time.Millisecond),
				deadlineErrorCode(w.Body.Bytes()), w.Header().Get("x-agentcc-deadline-exceeded"))
			if elapsed >= time.Second {
				t.Fatalf("answered after %s: the 100 ms deadline did not end the call (the upstream takes 2 s)", elapsed)
			}
			if c.stream {
				if w.Code < 500 || strings.Contains(w.Body.String(), "[DONE]") {
					t.Fatalf("HTTP %d after %s: the stream was served: %s", w.Code, elapsed, w.Body.String())
				}
			} else {
				if w.Code != http.StatusGatewayTimeout {
					t.Fatalf("HTTP %d after %s, want 504: %s", w.Code, elapsed, w.Body.String())
				}
				if got := deadlineErrorCode(w.Body.Bytes()); got != "gateway_timeout" {
					t.Errorf("error code %q, want gateway_timeout (the dispatched attempt's own timeout)", got)
				}
			}
			if n := up.calls.Load() - calls; n != 1 {
				t.Fatalf("the upstream got %d calls, want 1 (no retry is configured)", n)
			}
			up.waitCut(t, cut+1)
		})
	}

	t.Run("a deadline the call meets", func(t *testing.T) {
		fast := startHookUpstream(t, 0)
		ht, _ := deadlineServer(t, fast.URL)
		if w := ht.chat(t, hookChatBody, "x-agentcc-deadline-ms", "60000"); w.Code != http.StatusOK {
			t.Fatalf("HTTP %d: %s", w.Code, w.Body.String())
		}
		if n := len(fast.received()); n != 1 {
			t.Fatalf("the upstream got %d calls, want 1", n)
		}
	})
}

// TestAgentDeadlinePassedBeforeTheFirstAttempt: when the deadline passes
// during the request's own work (a 60 ms pre-plugin against a 20 ms
// deadline), no attempt starts: the upstream is never called, the attempt is
// recorded as skipped for reason deadline, and the call ends with 504.
func TestAgentDeadlinePassedBeforeTheFirstAttempt(t *testing.T) {
	up := startDelayedUpstream(t, 2*time.Second)
	ht, attempts := deadlineServer(t, up.URL, deadlineSleepPlugin{d: 60 * time.Millisecond})
	start := time.Now()
	w := ht.chat(t, hookChatBody, "x-agentcc-deadline-ms", "20")
	elapsed := time.Since(start)
	t.Logf("HTTP %d after %s, code %q", w.Code, elapsed.Round(time.Millisecond), deadlineErrorCode(w.Body.Bytes()))
	if w.Code != http.StatusGatewayTimeout || elapsed >= time.Second {
		t.Fatalf("HTTP %d after %s, want 504 well before the 2 s upstream: %s", w.Code, elapsed, w.Body.String())
	}
	if n := up.calls.Load(); n != 0 {
		t.Fatalf("the upstream got %d calls, want none: an attempt started after the deadline", n)
	}
	got := attempts.snapshot()
	if len(got) != 1 || got[0].Outcome != models.OutcomeSkipped || got[0].Reason != models.ReasonDeadline ||
		got[0].ErrorClass != models.ErrClassTimeout {
		t.Fatalf("attempts %+v, want one skipped for reason %q with class timeout", got, models.ReasonDeadline)
	}
}

// TestAgentDeadlineAbsentChangesNothing: without a deadline a slow upstream
// is waited for (control for the two tests above).
func TestAgentDeadlineAbsentChangesNothing(t *testing.T) {
	up := startDelayedUpstream(t, 150*time.Millisecond)
	ht, _ := deadlineServer(t, up.URL)
	w := ht.chat(t, hookChatBody)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), `"ok"`) {
		t.Fatalf("HTTP %d: %s", w.Code, w.Body.String())
	}
	if n := up.calls.Load(); n != 1 {
		t.Fatalf("the upstream got %d calls, want 1", n)
	}
}
