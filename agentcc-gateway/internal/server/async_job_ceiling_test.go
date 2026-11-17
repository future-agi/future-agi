package server

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// An async job runs on a context detached from its request (asyncExecutor):
// the request is answered 202 at once, and the job writes no synchronous
// response. server.max_request_timeout is the ceiling on that synchronous
// response, whose write fails at write_timeout, so it does not cut the job;
// the operator's async.max_job_timeout bounds the job (async.Worker.jobTimeout).
// The job keeps the timeout its request asked for (timeout_ms), and the same
// request sent synchronously is still held at the ceiling.
//
// The ceiling here is 1s and the async cap 10s. The request asks for 5s,
// through the operator's model timeout or a timeout header, and the upstream
// answers after 2s.
func TestAsyncJobRunsPastTheRequestCeiling(t *testing.T) {
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		select {
		case <-time.After(2 * time.Second):
		case <-r.Context().Done():
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"id":"chatcmpl-ceiling","object":"chat.completion","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	defer up.Close()

	for _, tc := range []struct {
		name          string
		modelTimeouts map[string]time.Duration
		header        map[string]string
	}{
		{"the operator's model timeout", map[string]time.Duration{"gpt-4o": 5 * time.Second}, nil},
		{"a timeout header", nil, map[string]string{"x-agentcc-timeout": "5s"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := newShareServer(t, up.URL, func(cfg *config.Config) {
				cfg.Server.WriteTimeout = time.Second
				cfg.Server.DefaultRequestTimeout = time.Second
				cfg.Server.MaxRequestTimeout = time.Second
				cfg.Routing.ModelTimeouts = tc.modelTimeouts
				cfg.Async.MaxJobTimeout = 10 * time.Second
			})

			w := submitAsync(t, srv, shareKeyOps, tc.header)
			if w.Code != http.StatusAccepted {
				t.Fatalf("submit: status %d body %s", w.Code, w.Body.String())
			}
			id := mediaDecode(t, w)["job_id"].(string)
			if snap := srv.handlers.asyncStore.Get(id); snap == nil {
				t.Fatalf("job %s is not stored", id)
			} else if got := snap.Metadata["timeout_ms"]; got != "5000" {
				t.Errorf("the job's timeout_ms = %q, want 5000: the request's own timeout, not the 1s request ceiling", got)
			}
			job := waitAsyncJob(t, srv, shareKeyOps, id)
			if job["status"] != "completed" {
				t.Errorf("job status %v, want completed: the request's 5s and the 10s cap both outlast the 2s upstream; job %v", job["status"], job)
			}

			// The same request, answered synchronously, is still held at the
			// 1s ceiling.
			sync := objCall(t, srv, "POST", "/v1/chat/completions", shareKeyOps, chatBody("gpt-4o"), tc.header)
			if sync.Code != http.StatusGatewayTimeout {
				t.Errorf("the synchronous request: status %d, want 504 at the 1s ceiling; body %s", sync.Code, sync.Body.String())
			}
		})
	}
}
