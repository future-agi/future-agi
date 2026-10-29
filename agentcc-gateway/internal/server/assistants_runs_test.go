package server

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

const (
	runQueued    = `{"id":"run_1","object":"thread.run","status":"queued","model":"gpt-4o","usage":null}`
	runCompleted = `{"id":"run_1","object":"thread.run","status":"completed","model":"gpt-4o","usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}`
)

// assistantsUpstream answers as OpenAI's Assistants API does: a run is
// queued when created, and complete, with its usage, when polled.
func assistantsUpstream(t *testing.T, stall bool) *httptest.Server {
	t.Helper()
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		switch {
		case r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/runs/run_1"):
			io.WriteString(w, runCompleted)
		case strings.Contains(string(body), `"stream":true`):
			w.Header().Set("Content-Type", "text/event-stream")
			w.WriteHeader(http.StatusOK)
			io.WriteString(w, "event: thread.run.created\ndata: "+runQueued+"\n\n")
			w.(http.Flusher).Flush()
			if stall {
				<-r.Context().Done()
				return
			}
			io.WriteString(w, "event: thread.run.completed\ndata: "+runCompleted+"\n\nevent: done\ndata: [DONE]\n\n")
		case strings.HasSuffix(r.URL.Path, "/runs"):
			io.WriteString(w, runQueued)
		default:
			io.WriteString(w, `{"data":[]}`)
		}
	}))
	t.Cleanup(ts.Close)
	return ts
}

// keyNamePlugin names the calling key after its bearer token, standing in
// for the auth plugin, so a charge's attribution can be seen.
type keyNamePlugin struct{}

func (keyNamePlugin) Name() string  { return "key_name" }
func (keyNamePlugin) Priority() int { return 1 }
func (keyNamePlugin) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	rc.Metadata["auth_key_name"] = strings.TrimPrefix(rc.Metadata["authorization"], "Bearer ")
	return pipeline.ResultContinue()
}
func (keyNamePlugin) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// chargeRecorder records every charge the post-plugins see.
type chargeRecorder struct {
	mu         sync.Mutex
	charges    []string // "<key>:<total tokens>"
	operations []string // the assistants operation each charge is attributed to
	runs       int
}

func (p *chargeRecorder) Name() string  { return "charges" }
func (p *chargeRecorder) Priority() int { return 100 }
func (p *chargeRecorder) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *chargeRecorder) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.runs++
	if rc.Response != nil && rc.Response.Usage != nil {
		p.charges = append(p.charges, fmt.Sprintf("%s:%d", rc.Metadata["auth_key_name"], rc.Response.Usage.TotalTokens))
		p.operations = append(p.operations, rc.Metadata["assistants_operation"])
	}
	return pipeline.ResultContinue()
}

func (p *chargeRecorder) recorded() ([]string, int) {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]string(nil), p.charges...), p.runs
}

func assistantsCall(t *testing.T, gateway, method, path, key, body string, headers map[string]string) (int, string) {
	t.Helper()
	req, err := http.NewRequest(method, gateway+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	out, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("reading the response: %v", err)
	}
	return resp.StatusCode, string(out)
}

// A run's usage arrives only when it finishes. Created without a stream and
// then polled, it is charged once — on the first poll that sees it finished —
// and to the key that started it, not the one polling.
func TestAssistantsRunIsChargedOnceWhenAPollSeesItFinish(t *testing.T) {
	charges := &chargeRecorder{}
	gateway := streamingGateway(t, assistantsUpstream(t, false).URL, keyNamePlugin{}, charges)

	if status, body := assistantsCall(t, gateway, http.MethodPost, "/v1/threads/thread_1/runs", "creator", `{"assistant_id":"asst_1"}`, nil); status != http.StatusOK || !strings.Contains(body, `"queued"`) {
		t.Fatalf("create run: %d %s", status, body)
	}
	if got, _ := charges.recorded(); len(got) != 0 {
		t.Fatalf("charged before the run finished: %v", got)
	}

	for range 3 {
		assistantsCall(t, gateway, http.MethodGet, "/v1/threads/thread_1/runs/run_1", "poller", "", nil)
	}

	got, _ := charges.recorded()
	if len(got) != 1 || got[0] != "creator:15" {
		t.Errorf("charges = %v, want one charge of 15 tokens to the creator", got)
	}
	// The poll's own attribution is replaced by the starting call's, not merged.
	charges.mu.Lock()
	defer charges.mu.Unlock()
	if len(charges.operations) != 1 || charges.operations[0] != "threads.runs.create" {
		t.Errorf("charge attributed to %v, want the call that started the run", charges.operations)
	}
}

// A run that finishes on its own stream is charged there, and a later poll
// does not charge it again.
func TestAssistantsRunIsChargedOnTheStreamItFinishesOn(t *testing.T) {
	charges := &chargeRecorder{}
	gateway := streamingGateway(t, assistantsUpstream(t, false).URL, keyNamePlugin{}, charges)

	status, body := assistantsCall(t, gateway, http.MethodPost, "/v1/threads/thread_1/runs", "creator", `{"assistant_id":"asst_1","stream":true}`, nil)
	if status != http.StatusOK || !strings.Contains(body, "thread.run.completed") {
		t.Fatalf("stream: %d %s", status, body)
	}
	assistantsCall(t, gateway, http.MethodGet, "/v1/threads/thread_1/runs/run_1", "poller", "", nil)

	got, runs := charges.recorded()
	if len(got) != 1 || got[0] != "creator:15" {
		t.Errorf("charges = %v, want one charge of 15 tokens to the creator", got)
	}
	if runs != 1 {
		t.Errorf("post-plugins ran %d times, want once: for the stream; the poll charged nothing", runs)
	}
}

// A run stream that reaches its deadline ends with an error event, as every
// other stream does.
func TestAssistantsRunStreamAtDeadlineEndsWithAnError(t *testing.T) {
	gateway := streamingGateway(t, assistantsUpstream(t, true).URL)

	_, body := assistantsCall(t, gateway, http.MethodPost, "/v1/threads/thread_1/runs", "creator", `{"assistant_id":"asst_1","stream":true}`, map[string]string{"x-agentcc-timeout": "300ms"})

	frames := sseFrames(body)
	if len(frames) == 0 || frames[len(frames)-1].event != "error" {
		t.Fatalf("stream did not end with an error event:\n%s", body)
	}
	if _, message := streamError(t, body); !strings.Contains(message, "timed out") {
		t.Errorf("error = %q, want a timeout", message)
	}
}

// The pending charge may sit in shared Redis; the caller's credential must not.
func TestPendingChargeLeavesTheCredentialBehind(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["authorization"] = "Bearer sk-live-secret"
	rc.Metadata["auth_key_id"] = "key_1"
	rc.Metadata["upstream_api_key"] = "sk-other"
	rc.Metadata["application"] = "billing"

	charge := newPendingCharge(rc, "gpt-4o")

	for _, secret := range []string{"authorization", "upstream_api_key"} {
		if _, kept := charge.Metadata[secret]; kept {
			t.Errorf("pending charge kept %q", secret)
		}
	}
	if charge.Metadata["auth_key_id"] != "key_1" || charge.Metadata["application"] != "billing" {
		t.Errorf("pending charge lost its attribution: %v", charge.Metadata)
	}
}

func TestRunChargeStoresHandBackOnce(t *testing.T) {
	stores := map[string]runChargeStore{"memory": newMemoryRunCharges()}
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		addr = "localhost:6379"
	}
	if client, err := redisstate.NewClient(redisstate.Config{Address: addr, Timeout: time.Second}); err == nil {
		t.Cleanup(func() { client.Close() })
		stores["redis"] = &redisRunCharges{client: client}
	} else {
		t.Logf("redis store not tested: Redis not available at %s: %v", addr, err)
	}

	for name, store := range stores {
		t.Run(name, func(t *testing.T) {
			ctx := context.Background()
			runID := fmt.Sprintf("run_test_%d", time.Now().UnixNano())
			store.Put(ctx, runID, pendingCharge{Model: "gpt-4o", Metadata: map[string]string{"auth_key_id": "key_1"}})

			charge, ok := store.Take(ctx, runID)
			if !ok || charge.Model != "gpt-4o" || charge.Metadata["auth_key_id"] != "key_1" {
				t.Fatalf("Take = %+v, %v; want the charge put", charge, ok)
			}
			if _, again := store.Take(ctx, runID); again {
				t.Error("the same run was handed back twice")
			}
		})
	}
}

// Vector-store writes and searches used to skip the pipeline: no rate limit,
// no budget, no request log. Reads still skip it.
func TestVectorStoreCallsGoThroughThePipeline(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, `{"object":"vector_store.search_results.page","data":[]}`)
	}))
	t.Cleanup(upstream.Close)

	t.Run("a search is logged, a read is not", func(t *testing.T) {
		charges := &chargeRecorder{}
		gateway := streamingGateway(t, upstream.URL, charges)

		if status, body := assistantsCall(t, gateway, http.MethodPost, "/v1/vector_stores/vs_1/search", "k", `{"query":"q"}`, nil); status != http.StatusOK {
			t.Fatalf("search: %d %s", status, body)
		}
		assistantsCall(t, gateway, http.MethodGet, "/v1/vector_stores/vs_1", "k", "", nil)

		if _, runs := charges.recorded(); runs != 1 {
			t.Errorf("post-plugins ran %d times, want once: for the search", runs)
		}
	})
	t.Run("a refusing pre-plugin stops a write", func(t *testing.T) {
		gateway := streamingGateway(t, upstream.URL, refusePlugin{})
		if status, _ := assistantsCall(t, gateway, http.MethodPost, "/v1/vector_stores/vs_1/search", "k", `{"query":"q"}`, nil); status != http.StatusTooManyRequests {
			t.Errorf("status = %d, want the pre-plugin's 429", status)
		}
	})
	// A key out of budget must still be able to delete what it pays to keep.
	t.Run("a spent budget stops a write, not a delete", func(t *testing.T) {
		charges := &chargeRecorder{}
		gateway := streamingGateway(t, upstream.URL, spendLimitPlugin{}, charges)
		for _, path := range []string{"/v1/vector_stores/vs_1", "/v1/vector_stores/vs_1/files/file_1"} {
			if status, body := assistantsCall(t, gateway, http.MethodDelete, path, "k", "", nil); status != http.StatusOK {
				t.Errorf("DELETE %s: %d %s", path, status, body)
			}
		}
		if status, _ := assistantsCall(t, gateway, http.MethodPost, "/v1/vector_stores/vs_1/search", "k", `{"query":"q"}`, nil); status != http.StatusTooManyRequests {
			t.Errorf("search: status = %d, want the spend limit's 429", status)
		}
		if _, runs := charges.recorded(); runs != 3 {
			t.Errorf("post-plugins ran %d times, want 3: deletes are logged too", runs)
		}
	})
}

// spendLimitPlugin refuses every call that could spend, as a spent budget
// does.
type spendLimitPlugin struct{}

func (spendLimitPlugin) Name() string  { return "spend_limit" }
func (spendLimitPlugin) Priority() int { return 10 }
func (spendLimitPlugin) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if rc.Flags.NoSpend {
		return pipeline.ResultContinue()
	}
	return pipeline.ResultError(&models.APIError{Status: http.StatusTooManyRequests, Type: models.ErrTypeRateLimit, Code: "budget_exceeded", Message: "budget exceeded"})
}
func (spendLimitPlugin) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// Key model lists name models as clients request them, provider prefix and
// all. A run's model used to reach the auth plugin with the prefix stripped,
// so a key allowed "openai/gpt-4o" could not start a run on it.
func TestRunCreationChecksTheModelAsRequested(t *testing.T) {
	upstream := assistantsUpstream(t, false)
	keys := auth.NewKeyStore(config.AuthConfig{Enabled: true, Keys: []config.AuthKeyConfig{
		{Name: "runs", Key: "sk-runs", Models: []string{"openai/gpt-4o"}},
	}})
	gateway := streamingGateway(t, upstream.URL, authplugin.New(keys, true))

	for _, route := range []struct{ name, path string }{
		{"create a run", "/v1/threads/thread_1/runs"},
		{"create a thread and run", "/v1/threads/runs"},
	} {
		t.Run(route.name, func(t *testing.T) {
			if status, body := assistantsCall(t, gateway, http.MethodPost, route.path, "sk-runs", `{"assistant_id":"asst_1","model":"openai/gpt-4o"}`, nil); status != http.StatusOK {
				t.Errorf("a model the key may use: %d %s", status, body)
			}
			if status, _ := assistantsCall(t, gateway, http.MethodPost, route.path, "sk-runs", `{"assistant_id":"asst_1","model":"openai/gpt-4o-mini"}`, nil); status != http.StatusForbidden {
				t.Errorf("a model the key may not use: status = %d, want 403", status)
			}
		})
	}
}
