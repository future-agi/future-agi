package credentials

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Ported from the round-5 tenancy verifier (config_revoke_test.go). Shared
// static mark_lapsed now pushes the revoked config AND evicts the grant's
// revision through the gateway purge seam, as the personal branch evicts its
// grant (agentcc test_shared_static_revoke_evicts_its_revision pins the
// Django half). Model both reaching B: B retires its entry before Set returns
// and then evicts, while A retains the old snapshot. Both replicas already
// admitted the credential, so this is distinct from first-admission binding.
func TestIndependentR5SharedConfigRevokeMustFenceRemoteCompletions(t *testing.T) {
	for _, supplied := range []bool{false, true} {
		name, id := "org-server", "issues"
		if supplied {
			name, id = "supplied-server", "fagi.search"
		}
		for _, local := range []bool{true, false} {
			location := "remote"
			if local {
				location = "local-control"
			}
			for _, method := range []string{"tool", "resource", "prompt"} {
				t.Run(name+"/"+location+"/"+method, func(t *testing.T) {
					r, replica, _ := stoppingResolvers(t)
					defer r.purgeUserLifetimes("org-a", "", id)
					defer replica.purgeUserLifetimes("org-a", "", id)
					storeA, storeB := tenant.NewStore(), tenant.NewStore()
					if local {
						storeB = storeA
					}
					storeA.Set("org-a", independentLiteralConfig(t, supplied, "active"))
					storeB.Set("org-a", independentLiteralConfig(t, supplied, "active"))
					raw := &independentLiteralClient{entered: make(chan context.Context, 1), release: make(chan struct{})}
					managerA := tenancy.New(tenancy.Options{Store: storeA, RetryInitial: time.Hour, Deps: func() *mcp.Deps {
						return &mcp.Deps{SharedCredentials: r, ManagedServers: independentLiteralManaged{enabled: supplied}, ClientFactory: func(mcp.ClientConfig) (mcp.UpstreamClient, error) { return raw, nil }}
					}})
					defer independentR3Close(managerA)
					managerB := tenancy.New(tenancy.Options{Store: storeB, RetryInitial: time.Hour, Deps: func() *mcp.Deps {
						return &mcp.Deps{SharedCredentials: replica, ManagedServers: independentLiteralManaged{enabled: supplied}, ClientFactory: func(mcp.ClientConfig) (mcp.UpstreamClient, error) { return &lifecycleClient{}, nil }}
					}})
					defer independentR3Close(managerB)
					_, pool, err := managerA.Resolve("org-a")
					if err != nil {
						t.Fatal(err)
					}
					if _, _, err := managerB.Resolve("org-a"); err != nil {
						t.Fatal(err)
					}
					client, err := pool.Client(id, "", mcp.AuthOverride{})
					if err != nil {
						t.Fatal(err)
					}
					done := make(chan error, 1)
					go func() {
						var err error
						switch method {
						case "tool":
							_, err = client.CallTool(context.Background(), "search", nil, mcp.CallOptions{})
						case "resource":
							_, err = client.ReadResource(context.Background(), "synthetic://item", mcp.CallOptions{})
						case "prompt":
							_, err = client.GetPrompt(context.Background(), "synthetic", nil, mcp.CallOptions{})
						}
						done <- err
					}()
					select {
					case <-raw.entered:
					case <-time.After(time.Second):
						close(raw.release)
						t.Fatal("operation did not enter synthetic provider")
					}
					// The complete shared-static mark_lapsed path: the revoked config
					// push, then the gateway eviction of the grant's revision.
					storeB.Set("org-a", independentLiteralConfig(t, supplied, "revoked"))
					if err := replica.RevokePushedGrants(context.Background(), "org-a", id, independentLiteralRevision); err != nil {
						close(raw.release)
						t.Fatal(err)
					}
					close(raw.release)
					select {
					case err := <-done:
						if err == nil {
							t.Error("shared config revoke returned on replica B but evicted grant completed successfully on replica A")
						}
					case <-time.After(time.Second):
						t.Fatal("released synthetic operation did not finish")
					}
				})
			}
		}
	}
}
