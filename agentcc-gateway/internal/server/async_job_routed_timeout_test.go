package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// ChatCompletion resolves a request's timeout before routing (call-order
// step 11), on the model the request names, under the org config it is
// routed by (a rollout candidate's, when step 4 chose it). So when routing
// renames the model (an access-group alias, complexity routing, model
// "auto"), the synchronous request keeps the requested model's timeout. An
// async job of the same request runs to that same timeout, held at
// async.max_job_timeout in place of the request ceiling; here the ceiling
// (10s) and the cap (10s) are above it, so the job's timeout and the
// synchronous request's are equal.
//
// The requested model's timeout is 5s and the server default 1s; no routed
// model has a timeout of its own, and the upstream answers after 2s. A job
// that took the routed model's timeout would get the 1s default and fail 504.
// In the rollout case the 5s is the candidate config's, and the stored config
// says 3s.
func TestRoutedAsyncJobTimeoutMatchesTheSynchronousRequest(t *testing.T) {
	var mu sync.Mutex
	var sent []string
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		body, _ := io.ReadAll(r.Body)
		var req struct {
			Model string `json:"model"`
		}
		_ = json.Unmarshal(body, &req)
		mu.Lock()
		sent = append(sent, req.Model)
		mu.Unlock()
		select {
		case <-time.After(2 * time.Second):
		case <-r.Context().Done():
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"chatcmpl-routed","object":"chat.completion","created":1700000000,"model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`, req.Model)
	}))
	defer up.Close()

	complexity := config.ComplexityConfig{
		Enabled:     true,
		DefaultTier: "simple",
		Tiers:       map[string]config.ComplexityTierConfig{"simple": {MaxScore: 1000000, Model: "gpt-4o-mini"}},
	}
	const rolloutOrg, rolloutKey = "org-async-rollout", "sk-agentcc-async-rollout-ops"
	for _, tc := range []struct {
		name      string
		model     string // the model the client asks for, which has the 5s timeout
		renamed   bool   // routing sends another model
		configure func(*config.Config)
		key       string            // the internal key both requests carry, else shareKeyOps
		org       *tenant.OrgConfig // that key's org config
	}{
		{name: "no routing", model: "gpt-4o"},
		{name: "complexity routing", model: "gpt-4o", renamed: true, configure: func(cfg *config.Config) {
			cfg.Routing.Complexity = complexity
		}},
		{name: "an access-group alias", model: "gpt-4o", renamed: true, configure: func(cfg *config.Config) {
			cfg.Routing.AccessGroups = config.AccessGroupsConfig{"ops": {
				Models:  []string{"gpt-4o-mini"},
				Aliases: map[string]string{"gpt-4o": "gpt-4o-mini"},
			}}
			for i := range cfg.Auth.Keys {
				if cfg.Auth.Keys[i].Key == shareKeyOps {
					cfg.Auth.Keys[i].Metadata = map[string]string{"access_groups": "ops"}
				}
			}
		}},
		{name: "model auto", model: "auto", renamed: true},
		{name: "a rollout candidate's model timeout, with complexity routing", model: "gpt-4o", renamed: true,
			configure: func(cfg *config.Config) {
				cfg.Routing.ModelTimeouts = nil // the 5s is the candidate's
				cfg.Routing.Complexity = complexity
				cfg.Auth.Keys = append(cfg.Auth.Keys, config.AuthKeyConfig{Name: "ops-rollout", Key: rolloutKey, Owner: "ops",
					KeyType: "internal", Metadata: map[string]string{"org_id": rolloutOrg}})
			},
			key: rolloutKey,
			org: &tenant.OrgConfig{
				Meta: &tenant.ConfigMeta{Version: 3},
				Routing: &tenant.RoutingConfig{
					ModelTimeouts: map[string]string{"gpt-4o": "3s"},
					Rollout: &tenant.RolloutConfig{
						Version: "v4", Percent: 100, StickyBy: "request",
						Routing: &tenant.RoutingConfig{ModelTimeouts: map[string]string{"gpt-4o": "5s"}},
					},
				},
			}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			mu.Lock()
			sent = nil
			mu.Unlock()
			srv := newShareServer(t, up.URL, func(cfg *config.Config) {
				cfg.Server.WriteTimeout = 10 * time.Second
				cfg.Server.MaxRequestTimeout = 10 * time.Second
				cfg.Server.DefaultRequestTimeout = time.Second
				cfg.Routing.ModelTimeouts = map[string]time.Duration{tc.model: 5 * time.Second}
				cfg.Async.MaxJobTimeout = 10 * time.Second
				p := cfg.Providers["openai"]
				p.Models = []string{"gpt-4o", "gpt-4o-mini"}
				cfg.Providers["openai"] = p
				if tc.configure != nil {
					tc.configure(cfg)
				}
			})
			key := shareKeyOps
			if tc.key != "" {
				key = tc.key
			}
			if tc.org != nil {
				srv.handlers.tenantStore.Set(rolloutOrg, tc.org)
			}

			syncResp := objCall(t, srv, "POST", "/v1/chat/completions", key, chatBody(tc.model), nil)
			if syncResp.Code != http.StatusOK {
				t.Fatalf("the synchronous request: status %d body %s", syncResp.Code, syncResp.Body.String())
			}
			syncTimeout := syncResp.Header().Get("x-agentcc-timeout-ms")

			w := objCall(t, srv, "POST", "/v1/chat/completions", key, chatBody(tc.model), map[string]string{"x-agentcc-async": "true"})
			if w.Code != http.StatusAccepted {
				t.Fatalf("the async request: status %d body %s", w.Code, w.Body.String())
			}
			id := mediaDecode(t, w)["job_id"].(string)
			snap := srv.handlers.asyncStore.Get(id)
			if snap == nil {
				t.Fatalf("job %s is not stored", id)
			}
			jobTimeout := snap.Metadata["timeout_ms"]
			job := waitAsyncJob(t, srv, key, id)

			mu.Lock()
			models := append([]string(nil), sent...)
			mu.Unlock()
			if len(models) != 2 || models[0] != models[1] || (models[0] == tc.model) == tc.renamed {
				t.Fatalf("the upstream received %v; want the same model twice, renamed by routing: %v", models, tc.renamed)
			}
			if syncTimeout != "5000" {
				t.Errorf("the synchronous request's x-agentcc-timeout-ms = %q, want 5000, the timeout of %s, the model it asked for", syncTimeout, tc.model)
			}
			if jobTimeout != syncTimeout {
				t.Errorf("the job's timeout_ms = %q, the synchronous request's %q: the same request must get the same timeout (upstream got %s)", jobTimeout, syncTimeout, models[0])
			}
			if job["status"] != "completed" {
				t.Errorf("job status %v, want completed: 5s and the 10s cap both outlast the 2s upstream; job %v", job["status"], job)
			}
		})
	}
}
