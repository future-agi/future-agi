package server

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/async"
	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// All orgs' async jobs share one pool of workers, and a job runs on its
// org's own provider instance, whose upstream and timeout the org chooses.
// One org must not be able to hold that pool, by the number of jobs it runs or
// by how long they run, and starve every other org's jobs.

const (
	shareOrgA = "org-async-share-a"
	shareOrgB = "org-async-share-b"
	shareKeyA = "sk-agentcc-async-share-a"
	shareKeyB = "sk-agentcc-async-share-b"

	shareCredA = "Bearer org-key-" + shareOrgA
	shareCredB = "Bearer org-key-" + shareOrgB
)

// heldUpstream answers chat completions at once, except those made with the
// credential hold, which it keeps open until their caller goes away (or the
// test ends). It reports when a held call starts and when its caller
// cancels it.
type heldUpstream struct {
	*httptest.Server
	hold      string
	started   chan struct{}
	cancelled chan struct{}

	mu   sync.Mutex
	seen map[string]int
}

func newHeldUpstream(t *testing.T, hold string) *heldUpstream {
	t.Helper()
	release := make(chan struct{})
	up := &heldUpstream{
		hold:      hold,
		started:   make(chan struct{}, 2048),
		cancelled: make(chan struct{}, 2048),
		seen:      make(map[string]int),
	}
	up.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
			fmt.Fprint(w, `{"object":"list","data":[]}`)
			return
		}
		// The server notices a caller that went away only once the request
		// body has been read.
		io.Copy(io.Discard, r.Body)
		cred := r.Header.Get("Authorization")
		up.mu.Lock()
		up.seen[cred]++
		up.mu.Unlock()
		if cred == up.hold {
			up.started <- struct{}{}
			select {
			case <-r.Context().Done():
				up.cancelled <- struct{}{}
				return
			case <-release:
			}
		}
		fmt.Fprint(w, `{"id":"chatcmpl-share","object":"chat.completion","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	// Cleanups run last-in first-out: release held calls, then close.
	t.Cleanup(up.Close)
	t.Cleanup(func() { close(release) })
	return up
}

func (up *heldUpstream) calls(cred string) int {
	up.mu.Lock()
	defer up.mu.Unlock()
	return up.seen[cred]
}

// waitStarted waits for n held calls to start.
func (up *heldUpstream) waitStarted(t *testing.T, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		select {
		case <-up.started:
		case <-time.After(5 * time.Second):
			t.Fatalf("only %d of %d held provider calls started", i, n)
		}
	}
}

// newShareServer builds the gateway with two orgs, each with its own openai
// provider (its own key; org A's with a 3600s timeout, as an org may set),
// against upstreamURL. The platform's openai provider has the platform key.
// configure adjusts the config before the server is built.
func newShareServer(t *testing.T, upstreamURL string, configure func(*config.Config)) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "a", Key: shareKeyA, Owner: "a", KeyType: "byok", Metadata: map[string]string{"org_id": shareOrgA}},
		{Name: "b", Key: shareKeyB, Owner: "b", KeyType: "byok", Metadata: map[string]string{"org_id": shareOrgB}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL:   upstreamURL,
		APIKey:    "platform-openai-key",
		APIFormat: "openai",
		Models:    []string{"gpt-4o"},
	}
	if configure != nil {
		configure(cfg)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	tenantStore := tenant.NewStore()
	tenantStore.Set(shareOrgA, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {Enabled: true, APIKey: "org-key-" + shareOrgA, Models: []string{"gpt-4o"}, Timeout: 3600},
	}})
	tenantStore.Set(shareOrgB, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"openai": {Enabled: true, APIKey: "org-key-" + shareOrgB, Models: []string{"gpt-4o"}},
	}})
	keyStore := auth.NewKeyStore(cfg.Auth)
	engine := pipeline.NewEngine(authplugin.New(keyStore, true))
	srv := New(cfg, "", registry, engine, keyStore, nil, nil, nil, testModelDBPtr(), tenantStore, nil)
	srv.ready.Store(true)
	t.Cleanup(srv.asyncWorker.Stop)
	return srv
}

// submitAsync posts an async chat completion and returns the response.
func submitAsync(t *testing.T, srv *Server, key string, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	h := map[string]string{"x-agentcc-async": "true"}
	for k, v := range headers {
		h[k] = v
	}
	return objCall(t, srv, "POST", "/v1/chat/completions", key, chatBody("gpt-4o"), h)
}

// A job with a far-off timeout header, on an org provider whose timeout the
// org set to an hour, must still end at the operator's cap; here the cap is
// server.default_request_timeout (1s), the longest timeout the config sets.
func TestAsyncJobs_JobTimeoutIsTheOperatorsCap(t *testing.T) {
	up := newHeldUpstream(t, shareCredA)
	srv := newShareServer(t, up.URL, func(cfg *config.Config) {
		cfg.Server.DefaultRequestTimeout = time.Second
	})

	w := submitAsync(t, srv, shareKeyA, map[string]string{"x-agentcc-timeout": "1000h"})
	if w.Code != http.StatusAccepted {
		t.Fatalf("submit: status %d body %s", w.Code, w.Body.String())
	}
	id := mediaDecode(t, w)["job_id"].(string)
	up.waitStarted(t, 1)

	select {
	case <-up.cancelled:
	case <-time.After(4 * time.Second):
		t.Fatalf("the job's provider call outlived the operator's 1s cap by 3s; job is %s",
			srv.handlers.asyncStore.Get(id).Status)
	}
	job := waitAsyncJob(t, srv, shareKeyA, id)
	if job["status"] != "failed" {
		t.Errorf("job status %v after its cap, want failed", job["status"])
	}
}

// Org A queues more long-running jobs than there are workers. Org B's job
// must still run at once, and org A must never hold more than its share of
// the workers.
func TestAsyncJobs_OneOrgCannotHoldEveryAsyncWorker(t *testing.T) {
	up := newHeldUpstream(t, shareCredA)
	srv := newShareServer(t, up.URL, nil)

	for i := 0; i < async.DefaultMaxWorkers; i++ {
		if w := submitAsync(t, srv, shareKeyA, map[string]string{"x-agentcc-timeout": "1000h"}); w.Code != http.StatusAccepted {
			t.Fatalf("org A's job %d: status %d body %s", i, w.Code, w.Body.String())
		}
	}
	share := async.DefaultMaxWorkers / 2
	up.waitStarted(t, share)

	w := submitAsync(t, srv, shareKeyB, nil)
	if w.Code != http.StatusAccepted {
		t.Fatalf("org B's job: status %d body %s", w.Code, w.Body.String())
	}
	job := waitAsyncJob(t, srv, shareKeyB, mediaDecode(t, w)["job_id"].(string))
	if job["status"] != "completed" {
		t.Fatalf("org B's job status %v while org A's jobs ran, want completed; job %v", job["status"], job)
	}
	if n := up.calls(shareCredB); n != 1 {
		t.Errorf("org B's key reached the upstream %d times, want 1", n)
	}
	if n := up.calls(shareCredA); n != share {
		t.Errorf("org A had %d jobs running at once, want its share, %d of %d workers", n, share, async.DefaultMaxWorkers)
	}
}

// Org A fills its share of the async queue with jobs that never finish. Its
// next job is refused (429) at once, and org B's job is still accepted and
// runs.
func TestAsyncJobs_OneOrgCannotFillTheAsyncQueue(t *testing.T) {
	up := newHeldUpstream(t, shareCredA)
	srv := newShareServer(t, up.URL, nil)

	// Enough jobs to fill the whole queue and every worker.
	attempts := async.QueueCapacity + async.DefaultMaxWorkers
	accepted := 0
	var refused *httptest.ResponseRecorder
	for i := 0; i < attempts; i++ {
		w := submitAsync(t, srv, shareKeyA, map[string]string{"x-agentcc-timeout": "1000h"})
		if w.Code != http.StatusAccepted {
			refused = w
			break
		}
		accepted++
	}
	if refused == nil {
		t.Fatalf("org A queued all %d of its jobs; its share of the queue is %d", attempts, async.QueueCapacity/2)
	}
	if refused.Code != http.StatusTooManyRequests {
		t.Errorf("org A's job past its share: status %d body %s, want 429", refused.Code, refused.Body.String())
	}
	// Queued, plus the jobs a worker already took (at most its share).
	if accepted < async.QueueCapacity/2 || accepted > async.QueueCapacity/2+async.DefaultMaxWorkers/2 {
		t.Errorf("org A had %d jobs accepted; want its share of the queue (%d) plus at most %d running",
			accepted, async.QueueCapacity/2, async.DefaultMaxWorkers/2)
	}

	w := submitAsync(t, srv, shareKeyB, nil)
	if w.Code != http.StatusAccepted {
		t.Fatalf("org B's job after org A filled its share: status %d body %s", w.Code, w.Body.String())
	}
	job := waitAsyncJob(t, srv, shareKeyB, mediaDecode(t, w)["job_id"].(string))
	if job["status"] != "completed" {
		t.Errorf("org B's job status %v, want completed; job %v", job["status"], job)
	}
}

func TestAsyncWorkerConfig_JobTimeoutDefaultsToTheLongestConfiguredTimeout(t *testing.T) {
	cases := []struct {
		name      string
		configure func(*config.Config)
		want      time.Duration
	}{
		{"server default only", func(c *config.Config) {}, 60 * time.Second},
		{"a longer model timeout", func(c *config.Config) {
			c.Routing.ModelTimeouts = map[string]time.Duration{"o1": 5 * time.Minute, "gpt-4o": 10 * time.Second}
		}, 5 * time.Minute},
		{"a longer provider timeout", func(c *config.Config) {
			c.Providers["slow"] = config.ProviderConfig{DefaultTimeout: 10 * time.Minute}
		}, 10 * time.Minute},
		{"set explicitly", func(c *config.Config) {
			c.Providers["slow"] = config.ProviderConfig{DefaultTimeout: 10 * time.Minute}
			c.Async.MaxJobTimeout = 30 * time.Second
		}, 30 * time.Second},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := config.DefaultConfig()
			tc.configure(cfg)
			if got := asyncWorkerConfig(cfg).MaxJobTimeout; got != tc.want {
				t.Errorf("MaxJobTimeout = %s, want %s", got, tc.want)
			}
		})
	}
}
