package server

// End to end through the chat attempt loop: the request deadline bounds
// every attempt, the
// waits between attempts and a stream that already started. chat_nonstream.go
// and chat_stream.go wrap the provider call in withRequestDeadline, refuse an
// attempt past the deadline (startAttempt) and answer it as the deadline
// (attemptLimitError); a cut stream ends with the deadline's error event,
// never [DONE].

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// chunkedStreamUpstream streams n content chunks gap apart, then a usage chunk
// and [DONE], unless the caller cuts the connection first.
type chunkedStreamUpstream struct {
	*httptest.Server
	calls, cut atomic.Int32
}

func startChunkedStreamUpstream(t *testing.T, n int, gap time.Duration) *chunkedStreamUpstream {
	t.Helper()
	u := &chunkedStreamUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"object":"list","data":[]}`))
			return
		}
		u.calls.Add(1)
		w.Header().Set("Content-Type", "text/event-stream")
		fl := w.(http.Flusher)
		for i := 0; i < n; i++ {
			select {
			case <-time.After(gap):
			case <-r.Context().Done():
				u.cut.Add(1)
				return
			}
			fmt.Fprintf(w, "data: {\"id\":\"c\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"t%d \"}}]}\n\n", i)
			fl.Flush()
		}
		fmt.Fprint(w, "data: {\"id\":\"c\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":3,\"completion_tokens\":10,\"total_tokens\":13}}\n\n")
		fmt.Fprint(w, "data: [DONE]\n\n")
		fl.Flush()
	}))
	t.Cleanup(u.Close)
	return u
}

const deadlineStreamBody = `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`

func wantDeadlineAnswer(t *testing.T, w *httptest.ResponseRecorder, elapsed time.Duration, code string) {
	t.Helper()
	t.Logf("HTTP %d after %s, code %q, x-agentcc-deadline-exceeded=%q", w.Code, elapsed.Round(time.Millisecond),
		deadlineErrorCode(w.Body.Bytes()), w.Header().Get("x-agentcc-deadline-exceeded"))
	if w.Code != http.StatusGatewayTimeout || deadlineErrorCode(w.Body.Bytes()) != code {
		t.Errorf("HTTP %d %s, want 504 %s", w.Code, w.Body.String(), code)
	}
	if w.Header().Get("x-agentcc-deadline-exceeded") != "true" {
		t.Errorf("x-agentcc-deadline-exceeded = %q, want true", w.Header().Get("x-agentcc-deadline-exceeded"))
	}
}

// TestAgentDeadlineCutsNonStreamAttempts: the dispatched attempt the deadline cuts is 504
// gateway_timeout and says the deadline ended it; a deadline that passed
// before the first attempt is 504 deadline_exceeded.
func TestAgentDeadlineCutsNonStreamAttempts(t *testing.T) {
	t.Run("the deadline cuts the attempt", func(t *testing.T) {
		up := startDelayedUpstream(t, 2*time.Second)
		ht, _ := deadlineServer(t, up.URL)
		start := time.Now()
		w := ht.chat(t, hookChatBody, "x-agentcc-deadline-ms", "100")
		wantDeadlineAnswer(t, w, time.Since(start), "gateway_timeout")
	})
	t.Run("the deadline passed before the first attempt", func(t *testing.T) {
		up := startDelayedUpstream(t, 2*time.Second)
		ht, _ := deadlineServer(t, up.URL, deadlineSleepPlugin{d: 60 * time.Millisecond})
		start := time.Now()
		w := ht.chat(t, hookChatBody, "x-agentcc-deadline-ms", "20")
		wantDeadlineAnswer(t, w, time.Since(start), "deadline_exceeded")
		if n := up.calls.Load(); n != 0 {
			t.Errorf("the upstream got %d calls, want none", n)
		}
	})
}

// TestAgentDeadlineBoundsRetryBackoff: a retry's backoff never outlives the deadline:
// an upstream 503, a retry backoff drawn by the retryer's full
// jitter from [0, 600 ms), a 150 ms deadline. The call always answers by
// about the deadline, and no upstream call is ever sent past it. When the
// drawn backoff ends past the deadline (most draws), the answer is the
// attempt's own 502 with x-agentcc-deadline-exceeded and the retry is never
// sent; when it ends before the deadline, the retry runs in time and its
// own 502 answers.
func TestAgentDeadlineBoundsRetryBackoff(t *testing.T) {
	var calls atomic.Int32
	var lastCallAt atomic.Int64 // UnixNano of the latest upstream call
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.Method != http.MethodPost {
			_, _ = w.Write([]byte(`{"object":"list","data":[]}`))
			return
		}
		calls.Add(1)
		lastCallAt.Store(time.Now().UnixNano())
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = w.Write([]byte(`{"error":{"message":"overloaded","type":"server_error"}}`))
	}))
	defer up.Close()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "hook", Key: "sk-hook", Owner: "t", KeyType: "internal"}}
	cfg.Providers["first"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Providers["twin"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Routing.DefaultStrategy = "least-latency"
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"gpt-4o": {{Provider: "first", Weight: 1}, {Provider: "twin", Weight: 1}}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	srv.handlers.failover.Store(routing.NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 1}, registry.Router(),
		routing.NewRetryer(config.RetryConfig{Enabled: true, MaxRetries: 1, InitialDelay: 600 * time.Millisecond, MaxDelay: 600 * time.Millisecond, Multiplier: 1}), nil))
	ht := &hookOrderTest{srv: srv, ev: &hookEvents{}}

	start := time.Now()
	w := ht.chat(t, hookChatBody, "x-agentcc-deadline-ms", "150")
	elapsed := time.Since(start)
	time.Sleep(800 * time.Millisecond) // a retry sent late would land here
	t.Logf("HTTP %d after %s, code %q, x-agentcc-deadline-exceeded=%q, upstream calls %d", w.Code, elapsed.Round(time.Millisecond),
		deadlineErrorCode(w.Body.Bytes()), w.Header().Get("x-agentcc-deadline-exceeded"), calls.Load())
	if elapsed >= 300*time.Millisecond {
		t.Errorf("answered after %s: the retry backoff outlived the 150 ms deadline", elapsed)
	}
	if w.Code != http.StatusBadGateway {
		t.Errorf("HTTP %d %s, want an attempt's own 502", w.Code, w.Body.String())
	}
	if last := time.Unix(0, lastCallAt.Load()).Sub(start); last >= 150*time.Millisecond {
		t.Errorf("an upstream call was sent %s after the start: past the 150 ms deadline", last)
	}
	switch n := calls.Load(); n {
	case 1: // the backoff ended past the deadline: the retry was refused
		if w.Header().Get("x-agentcc-deadline-exceeded") != "true" {
			t.Errorf("one upstream call: x-agentcc-deadline-exceeded = %q, want true", w.Header().Get("x-agentcc-deadline-exceeded"))
		}
	case 2: // the drawn backoff ended before the deadline: the retry ran in time
	default:
		t.Errorf("the upstream got %d calls, want 1 or 2 (MaxRetries 1)", n)
	}
}

// TestAgentDeadlineCutsStreams: a stream answers a deadline like a non-stream call
// before its first chunk (504 and the header; never dispatched when the
// deadline passed before the attempt), and one that started is cut at the
// deadline: its content so far, then a gateway_timeout error event and no
// [DONE], with the upstream connection cut.
func TestAgentDeadlineCutsStreams(t *testing.T) {
	t.Run("before the first chunk", func(t *testing.T) {
		up := startDelayedUpstream(t, 2*time.Second)
		ht, _ := deadlineServer(t, up.URL)
		start := time.Now()
		w := ht.chat(t, deadlineStreamBody, "x-agentcc-deadline-ms", "100")
		wantDeadlineAnswer(t, w, time.Since(start), "gateway_timeout")
		up.waitCut(t, 1)
	})
	t.Run("the deadline passed before the first attempt", func(t *testing.T) {
		up := startChunkedStreamUpstream(t, 3, 20*time.Millisecond)
		ht, attempts := deadlineServer(t, up.URL, deadlineSleepPlugin{d: 60 * time.Millisecond})
		start := time.Now()
		w := ht.chat(t, deadlineStreamBody, "x-agentcc-deadline-ms", "20")
		wantDeadlineAnswer(t, w, time.Since(start), "deadline_exceeded")
		time.Sleep(100 * time.Millisecond)
		if n := up.calls.Load(); n != 0 {
			t.Errorf("the upstream got %d calls, want none", n)
		}
		if got := attempts.snapshot(); len(got) != 1 || got[0].Outcome != models.OutcomeSkipped || got[0].Reason != models.ReasonDeadline {
			t.Errorf("attempts %+v, want one skipped for reason deadline", got)
		}
	})
	t.Run("mid-stream", func(t *testing.T) {
		up := startChunkedStreamUpstream(t, 10, 100*time.Millisecond)
		ht, _ := deadlineServer(t, up.URL)
		start := time.Now()
		w := ht.chat(t, deadlineStreamBody, "x-agentcc-deadline-ms", "350")
		elapsed := time.Since(start)
		body := w.Body.String()
		t.Logf("HTTP %d after %s; content chunks %d; [DONE] %v; gateway_timeout event %v", w.Code, elapsed.Round(time.Millisecond),
			strings.Count(body, `"content":"t`), strings.Contains(body, "[DONE]"), strings.Contains(body, `"code":"gateway_timeout"`))
		if elapsed >= 700*time.Millisecond {
			t.Errorf("the stream ran %s: the 350 ms deadline did not cut it (the upstream takes 1 s)", elapsed)
		}
		if strings.Count(body, `"content":"t`) == 0 {
			t.Errorf("no content before the cut: %s", body)
		}
		if strings.Contains(body, "[DONE]") || !strings.Contains(body, `"code":"gateway_timeout"`) {
			t.Errorf("want a gateway_timeout error event and no [DONE]: %s", body)
		}
		up.waitCut(t, 1)
	})
}

func (u *chunkedStreamUpstream) waitCut(t *testing.T, n int32) {
	t.Helper()
	for deadline := time.Now().Add(3 * time.Second); u.cut.Load() < n; time.Sleep(5 * time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatalf("the upstream saw %d cut calls, want %d", u.cut.Load(), n)
		}
	}
}
