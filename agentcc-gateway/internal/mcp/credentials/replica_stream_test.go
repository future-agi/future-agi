package credentials

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/mcp/upstream"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestCredentialLifecycleReplicaStreamStops(t *testing.T) {
	for _, name := range []string{"personal", "shared", "pushed-bearer", "pushed-api-key", "supplied"} {
		shared := name != "personal"
		t.Run(name, func(t *testing.T) {
			r, purger, source := stoppingResolvers(t)
			k := keyA1
			if shared {
				k = SharedKey("org-a", "issues")
			}
			if name == "supplied" {
				k.ServerID = "fagi.search"
				defer r.purgeUserLifetimes(k.OrgID, "", k.ServerID)
			}
			stoppingSeed(source, k, 1)
			fixtures, err := mcpmock.LoadFixtures("../../../scripts/scenarios/fixtures/mcp/common")
			if err != nil {
				t.Fatal(err)
			}
			era, err := mcpmock.ParseEra("2025-06-18")
			if err != nil {
				t.Fatal(err)
			}
			mock, err := mcpmock.New(mcpmock.Config{Era: era, Auth: "none", Path: "/mcp", ServerName: "stream-lifetime"}, fixtures)
			if err != nil {
				t.Fatal(err)
			}
			release := make(chan struct{})
			handler := mock.Handler()
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
				if req.Method == http.MethodPost {
					raw, _ := io.ReadAll(req.Body)
					req.Body = io.NopCloser(bytes.NewReader(raw))
					var message struct {
						ID     json.RawMessage `json:"id"`
						Method string          `json:"method"`
						Params struct {
							Meta struct {
								Token json.RawMessage `json:"progressToken"`
							} `json:"_meta"`
						} `json:"params"`
					}
					if json.Unmarshal(raw, &message) == nil && message.Method == "tools/call" {
						w.Header().Set("Content-Type", "text/event-stream")
						emit := func(n int) {
							fmt.Fprintf(w, "event: message\ndata: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\",\"params\":{\"progressToken\":%s,\"progress\":%d}}\n\n", message.Params.Meta.Token, n)
							w.(http.Flusher).Flush()
						}
						emit(1)
						<-release
						emit(2)
						fmt.Fprintf(w, "event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":%s,\"result\":{\"content\":[]}}\n\n", message.ID)
						return
					}
				}
				handler.ServeHTTP(w, req)
			}))
			defer server.Close()
			mock.SetPublicURL(server.URL)
			credential := map[string]any{"mode": "per_user"}
			if name == "shared" {
				credential = signedIn("synthetic")
			} else if shared {
				kind, header := "bearer", "Authorization"
				if name == "pushed-api-key" {
					kind, header = "api_key", "X-Synthetic-Key"
				}
				credential = map[string]any{"mode": "shared", "state": "active", "credential_id": "synthetic-pushed", "revision": 1, "secret": map[string]any{"type": kind, "header": header, "value": "synthetic-stream-grant"}}
			}
			settings := map[string]any{"enabled": true, "servers": map[string]any{k.ServerID: map[string]any{"url": server.URL + "/mcp", "era": "2025-06-18", "credential": credential}}}
			var managed mcp.ManagedServers = noManagedServers{}
			if name == "supplied" {
				delete(settings, "servers")
				settings["managed_tools"] = map[string]any{"enabled": true, "credentials": map[string]any{k.ServerID: credential}}
				managed = independentR4Managed{server.URL + "/mcp"}
			}
			data, _ := json.Marshal(map[string]any{"mcp": settings})
			var cfg tenant.OrgConfig
			if err := json.Unmarshal(data, &cfg); err != nil {
				t.Fatal(err)
			}
			store := tenant.NewStore()
			store.Set("org-a", &cfg)
			factory := upstream.NewFactory(upstream.Options{})
			manager := tenancy.New(tenancy.Options{Store: store, RetryInitial: time.Hour, Deps: func() *mcp.Deps {
				return &mcp.Deps{SharedCredentials: r, ManagedServers: managed, ClientFactory: factory.Build}
			}})
			defer independentR3Close(manager)
			_, pool, err := manager.Resolve("org-a")
			if err != nil {
				t.Fatal(err)
			}
			auth := mcp.AuthOverride{}
			if !shared {
				auth = *stoppingGrant(t, r, k)
			}
			c, err := pool.Client(k.ServerID, k.UserID, auth)
			if err != nil {
				t.Fatal(err)
			}
			first := make(chan struct{})
			var chunks atomic.Int32
			ctx := mcp.WithUpstreamNotifications(context.Background(), func(*mcp.Message) {
				if chunks.Add(1) == 1 {
					close(first)
				}
			})
			done := make(chan error, 1)
			go func() {
				_, err := c.CallTool(ctx, "echo", json.RawMessage(`{}`), mcp.CallOptions{ProgressToken: "synthetic-progress"})
				done <- err
			}()
			select {
			case <-first:
			case <-time.After(5 * time.Second):
				close(release)
				t.Fatal("initial stream chunk never arrived")
			}
			source.set(k, Token{}, &Refusal{Reason: RefusalRevoked})
			start := time.Now()
			if _, err := purger.PurgeCredentials(context.Background(), k.OrgID, k.UserID, k.ServerID); err != nil {
				close(release)
				t.Fatal(err)
			}
			close(release)
			select {
			case err := <-done:
				if err == nil {
					t.Fatal("evicted stream completed successfully")
				}
			case <-time.After(2 * time.Second):
				t.Fatal("evicted stream remained active beyond bound")
			}
			if chunks.Load() != 1 {
				t.Fatal("stream forwarded content after eviction returned")
			}
			t.Logf("stream cancellation/completion=%s", time.Since(start))
		})
	}
}
