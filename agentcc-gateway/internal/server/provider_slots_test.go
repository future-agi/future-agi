package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"gopkg.in/yaml.v3"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// PROVIDER-CONCURRENCY-1. A provider entry's upstream slots (max_concurrent)
// default to 1024 on a remote entry, and a call that finds them all taken
// waits at most queue_timeout, then gets 503 provider_busy with Retry-After
// before anything is sent upstream: never a wait to the request's deadline.
//
// These tests set the entry through YAML and read the answer's code as text,
// so they build against the gateway before the change too and show what it
// did then (bench A2: a 200 ms upstream at 1000 rps queued for seconds).

const (
	slotOrg        = "org-slots"
	slotKey        = "sk-slots"
	slotEntry      = "slotted"
	slotBlockModel = "m-block"
)

// slotUpstream is an OpenAI-compatible upstream. A request for
// slotBlockModel is held until release is closed; every other one is
// answered after delay, once barrier requests are in flight together (or
// barrierWait has passed).
type slotUpstream struct {
	*httptest.Server
	release     chan struct{}
	releaseOnce sync.Once
	arrived     chan struct{} // one value per held request that arrived

	calls    atomic.Int32 // requests other than slotBlockModel's
	inflight atomic.Int32
	peak     atomic.Int32

	delay       time.Duration
	barrier     int32
	barrierWait time.Duration
	arrivals    atomic.Int32
	full        chan struct{}
	fullOnce    sync.Once
}

func (u *slotUpstream) unblock() { u.releaseOnce.Do(func() { close(u.release) }) }

func startSlotUpstream(t *testing.T, delay time.Duration, barrier int32, barrierWait time.Duration) *slotUpstream {
	t.Helper()
	u := &slotUpstream{release: make(chan struct{}), arrived: make(chan struct{}, 64), delay: delay,
		barrier: barrier, barrierWait: barrierWait, full: make(chan struct{})}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"object":"list","data":[]}`))
			return
		}
		body, _ := io.ReadAll(r.Body)
		var head struct {
			Model  string `json:"model"`
			Stream bool   `json:"stream"`
		}
		_ = json.Unmarshal(body, &head)
		n := u.inflight.Add(1)
		defer u.inflight.Add(-1)
		for p := u.peak.Load(); n > p && !u.peak.CompareAndSwap(p, n); p = u.peak.Load() {
		}
		if head.Model == slotBlockModel {
			u.arrived <- struct{}{}
			<-u.release
		} else {
			u.calls.Add(1)
		}
		if u.barrier > 0 {
			if u.arrivals.Add(1) >= u.barrier {
				u.fullOnce.Do(func() { close(u.full) })
			}
			select {
			case <-u.full:
			case <-time.After(u.barrierWait):
			}
		}
		if u.delay > 0 {
			time.Sleep(u.delay)
		}
		writeSlotAnswer(w, r.URL.Path, head.Model, head.Stream)
	}))
	// Cleanups run last-in first-out: release the held requests before the
	// server closes, which waits for them.
	t.Cleanup(u.Close)
	t.Cleanup(u.unblock)
	return u
}

func writeSlotAnswer(w http.ResponseWriter, path, model string, stream bool) {
	switch {
	case strings.HasSuffix(path, "/chat/completions") && stream:
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprintf(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"ok\"}}]}\n\n", model)
		fmt.Fprintf(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":1,\"completion_tokens\":1,\"total_tokens\":2}}\n\n", model)
		fmt.Fprint(w, "data: [DONE]\n\n")
	case strings.HasSuffix(path, "/embeddings"):
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"object":"list","model":%q,"data":[{"object":"embedding","index":0,"embedding":[0.1,0.2]}],"usage":{"prompt_tokens":1,"total_tokens":1}}`, model)
	case strings.HasSuffix(path, "/images/generations"):
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"created":1,"data":[{"url":"https://example.invalid/a.png"}]}`))
	case strings.HasSuffix(path, "/audio/speech"):
		w.Header().Set("Content-Type", "audio/mpeg")
		_, _ = w.Write([]byte("audio"))
	case strings.HasSuffix(path, "/responses"):
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"resp_1","object":"response","status":"completed","model":%q,"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"ok"}]}],"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}`, model)
	default:
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"c1","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`, model)
	}
}

// yamlInto sets the fields text names on v, as a config file would. A key
// the struct does not have is ignored, as the config loader ignores it.
func yamlInto(t *testing.T, v any, text string) {
	t.Helper()
	if err := yaml.Unmarshal([]byte(text), v); err != nil {
		t.Fatalf("yaml %q: %v", text, err)
	}
}

// newSlotGateway serves org slotOrg's own key on provider entry slotEntry
// (the org-config path the bench measured): the config.yaml entry gives the
// upstream and entryYAML, the org its key. requestTimeout is the server's
// default request deadline.
func newSlotGateway(t *testing.T, upstreamURL, entryYAML string, requestTimeout time.Duration) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Server.DefaultRequestTimeout = requestTimeout
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "slots", Key: slotKey, Owner: "t", KeyType: "byok",
		Metadata: map[string]string{"org_id": slotOrg},
	}}
	off := false
	pc := config.ProviderConfig{BaseURL: upstreamURL, APIKey: "operator-key", APIFormat: "openai",
		Models: []string{"m-unused"}, AutoDiscover: &off}
	// The upstream is on loopback; local: false makes it the remote provider
	// a hosted gateway calls.
	yamlInto(t, &pc, "local: false\n"+entryYAML)
	cfg.Providers[slotEntry] = pc
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	tenants := tenant.NewStore()
	tenants.Set(slotOrg, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		slotEntry: {APIKey: "org-key", Enabled: true, Models: []string{"m-chat", "m-embed", "m-image", "m-tts", slotBlockModel}},
	}})
	keyStore := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	return srv
}

// slotAnswer is what one request got, and how long it took.
type slotAnswer struct {
	status  int
	body    string
	header  http.Header
	elapsed time.Duration
	done    bool
}

// slotCall serves one request and returns once it is answered or within has
// passed (done false), whichever is first.
func slotCall(srv *Server, path, body string, within time.Duration) slotAnswer {
	answered := make(chan slotAnswer, 1)
	go func() {
		req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+slotKey)
		req.Header.Set("anthropic-version", "2023-06-01")
		w := httptest.NewRecorder()
		start := time.Now()
		srv.httpServer.Handler.ServeHTTP(w, req)
		answered <- slotAnswer{status: w.Code, body: w.Body.String(), header: w.Header(), elapsed: time.Since(start), done: true}
	}()
	select {
	case a := <-answered:
		return a
	case <-time.After(within):
		return slotAnswer{elapsed: within}
	}
}

func slotChatBody(model string, stream bool) string {
	return fmt.Sprintf(`{"model":%q,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, model, stream)
}

// holdTheSlot sends a request for slotBlockModel that the upstream holds,
// and returns once the upstream has it: the entry's one slot is taken.
func holdTheSlot(t *testing.T, srv *Server, u *slotUpstream) {
	t.Helper()
	go slotCall(srv, "/v1/chat/completions", slotChatBody(slotBlockModel, false), time.Minute)
	select {
	case <-u.arrived:
	case <-time.After(5 * time.Second):
		t.Fatal("the held request never reached the upstream")
	}
}

// TestProviderBusyEverySurface: with the entry's one slot taken, a request
// on every surface that calls the provider is refused within the queue wait
// (150 ms here) with 503 provider_busy, Retry-After and a message naming
// the entry, and is never sent upstream. Before the change each waited for
// the slot until its request deadline or the slot freed.
func TestProviderBusyEverySurface(t *testing.T) {
	const msgs = `"messages":[{"role":"user","content":"hi"}]`
	surfaces := []struct{ name, path, body string }{
		{"chat", "/v1/chat/completions", slotChatBody("m-chat", false)},
		{"chat stream start", "/v1/chat/completions", slotChatBody("m-chat", true)},
		{"completions", "/v1/completions", `{"model":"m-chat","prompt":"hi"}`},
		{"completions stream start", "/v1/completions", `{"model":"m-chat","prompt":"hi","stream":true}`},
		{"messages", "/v1/messages", `{"model":"m-chat","max_tokens":16,` + msgs + `}`},
		{"messages stream start", "/v1/messages", `{"model":"m-chat","max_tokens":16,"stream":true,` + msgs + `}`},
		{"responses", "/v1/responses", `{"model":"m-chat","input":"hi"}`},
		{"responses stream start", "/v1/responses", `{"model":"m-chat","input":"hi","stream":true}`},
		{"embeddings", "/v1/embeddings", `{"model":"m-embed","input":"hi"}`},
		{"images", "/v1/images/generations", `{"model":"m-image","prompt":"a cat"}`},
		{"speech", "/v1/audio/speech", `{"model":"m-tts","input":"hi","voice":"alloy"}`},
		{"speech stream start", "/v1/audio/speech/stream", `{"model":"m-tts","input":"hi","voice":"alloy"}`},
		{"genai", "/v1beta/models/m-chat:generateContent", `{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`},
		{"genai stream start", "/v1beta/models/m-chat:streamGenerateContent?alt=sse", `{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`},
	}
	u := startSlotUpstream(t, 0, 0, 0)
	// The held request must keep its slot through every surface: a deadline
	// far past the test's.
	srv := newSlotGateway(t, u.URL, "max_concurrent: 1\nqueue_timeout: 150ms\n", 30*time.Second)
	holdTheSlot(t, srv, u)

	for _, s := range surfaces {
		t.Run(s.name, func(t *testing.T) {
			a := slotCall(srv, s.path, s.body, 3*time.Second)
			if !a.done {
				t.Fatalf("no answer within 3s: the request waited for a slot")
			}
			if a.status != http.StatusServiceUnavailable || !strings.Contains(a.body, "provider_busy") {
				t.Fatalf("status %d after %v, body %s; want 503 provider_busy", a.status, a.elapsed, a.body)
			}
			if got := a.header.Get("Retry-After"); got != "1" {
				t.Errorf("Retry-After %q, want 1", got)
			}
			// Admission is an HTTP refusal, never an in-band SSE error. Each
			// route must keep its own error dialect even when stream was asked for.
			if ct := a.header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") || !json.Valid([]byte(a.body)) {
				t.Fatalf("not a JSON refusal: Content-Type %q, body %s", ct, a.body)
			}
			var wire struct {
				Error struct {
					Code   json.RawMessage `json:"code"`
					Type   string          `json:"type"`
					Status string          `json:"status"`
				} `json:"error"`
			}
			if err := json.Unmarshal([]byte(a.body), &wire); err != nil {
				t.Fatal(err)
			}
			switch {
			case strings.HasPrefix(s.path, "/v1beta/"):
				if string(wire.Error.Code) != "503" || wire.Error.Status != "UNAVAILABLE" {
					t.Errorf("not a GenAI UNAVAILABLE refusal: %s", a.body)
				}
			case s.path != "/v1/messages":
				if string(wire.Error.Code) != `"provider_busy"` || wire.Error.Type != "server_error" {
					t.Errorf("not an OpenAI provider_busy refusal: %s", a.body)
				}
			}
			if !strings.Contains(a.body, slotEntry) {
				t.Errorf("the message does not name the provider entry: %s", a.body)
			}
			if a.elapsed > 1500*time.Millisecond {
				t.Errorf("answered after %v; the queue wait is 150ms", a.elapsed)
			}
		})
	}
	if n := u.calls.Load(); n != 0 {
		t.Fatalf("%d refused requests reached the upstream", n)
	}
}

// TestProviderBusyBoundsTheQueue: 20 calls on an entry of 4 slots whose
// upstream takes 1 s. 4 are served; the other 16 are refused provider_busy
// at the 200 ms queue wait, long before any slot frees, instead of queueing
// behind them (before the change the last ones hit the 2 s deadline).
func TestProviderBusyBoundsTheQueue(t *testing.T) {
	u := startSlotUpstream(t, time.Second, 0, 0)
	srv := newSlotGateway(t, u.URL, "max_concurrent: 4\nqueue_timeout: 200ms\n", 2*time.Second)

	answers := make([]slotAnswer, 20)
	var wg sync.WaitGroup
	for i := range answers {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			answers[i] = slotCall(srv, "/v1/chat/completions", slotChatBody("m-chat", false), 5*time.Second)
		}(i)
	}
	wg.Wait()

	served, busy := 0, 0
	for _, a := range answers {
		switch {
		case a.done && a.status == http.StatusOK:
			served++
		case a.done && a.status == http.StatusServiceUnavailable && strings.Contains(a.body, "provider_busy"):
			busy++
			if a.elapsed > 900*time.Millisecond {
				t.Errorf("a busy refusal took %v; the queue wait is 200ms and no slot frees before 1s", a.elapsed)
			}
		default:
			t.Errorf("answer done=%v status %d after %v: %s", a.done, a.status, a.elapsed, a.body)
		}
	}
	if served != 4 || busy != 16 {
		t.Fatalf("served %d, refused busy %d; want 4 and 16", served, busy)
	}
	if p := u.peak.Load(); p > 4 {
		t.Fatalf("the upstream had %d calls at once; max_concurrent is 4", p)
	}
}

// TestA2DefaultSlotsCarry200InFlight is the bench's A2 point as a test: a
// 200 ms upstream at 1000 requests per second has 200 calls in flight. With
// max_concurrent and conn_pool_size unset, a remote entry must let all 200
// reach the upstream at once. The upstream holds each call until 200 are in
// flight (or 1.5 s pass): before the change the default of 100 slots let
// only 100 through, and the rest queued past the 2 s deadline.
func TestA2DefaultSlotsCarry200InFlight(t *testing.T) {
	const n = 200
	u := startSlotUpstream(t, 200*time.Millisecond, n, 1500*time.Millisecond)
	srv := newSlotGateway(t, u.URL, "", 2*time.Second)

	answers := make([]slotAnswer, n)
	var wg sync.WaitGroup
	for i := range answers {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			answers[i] = slotCall(srv, "/v1/chat/completions", slotChatBody("m-chat", false), 10*time.Second)
		}(i)
	}
	wg.Wait()

	failed := 0
	for _, a := range answers {
		if !a.done || a.status != http.StatusOK {
			failed++
		}
	}
	if p := u.peak.Load(); p < n || failed != 0 {
		t.Fatalf("the upstream had at most %d calls at once (want %d); %d of %d requests failed", p, n, failed, n)
	}
}

// TestA2OpenLoop1000RPS is A2's arrival process: 1000 requests at 1000 per
// second to a 200 ms upstream, with a 1 s request deadline. On the shipped
// default every one is served in about 200 ms. Before the change 100 slots
// served 500 per second, the queue grew by 500 a second, and the requests
// from about 0.8 s on waited past their deadline.
func TestA2OpenLoop1000RPS(t *testing.T) {
	const n = 1000
	u := startSlotUpstream(t, 200*time.Millisecond, 0, 0)
	srv := newSlotGateway(t, u.URL, "", time.Second)

	answers := make([]slotAnswer, n)
	var wg sync.WaitGroup
	tick := time.NewTicker(time.Millisecond)
	defer tick.Stop()
	for i := range answers {
		<-tick.C
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			answers[i] = slotCall(srv, "/v1/chat/completions", slotChatBody("m-chat", false), 5*time.Second)
		}(i)
	}
	wg.Wait()

	failed := map[int]int{}
	lat := make([]time.Duration, 0, n)
	for _, a := range answers {
		lat = append(lat, a.elapsed)
		if !a.done || a.status != http.StatusOK {
			failed[a.status]++
		}
	}
	sort.Slice(lat, func(i, j int) bool { return lat[i] < lat[j] })
	t.Logf("latency p50 %v p99 %v max %v; upstream peak in flight %d", lat[n/2], lat[n*99/100], lat[n-1], u.peak.Load())
	if len(failed) != 0 {
		t.Fatalf("requests not served, by status: %v (p99 %v)", failed, lat[n*99/100])
	}
}
