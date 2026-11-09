package credentials

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Ported from the round-5 lifecycle verifier (builtin_replica_test.go). The
// remote revoke is the pushed revoked config plus the revision eviction.
func verifierR5BuiltinReplica(t *testing.T, method string, awaitCancellation bool) {
	t.Helper()
	r, purger := independentR4Resolvers(t)
	defer r.purgeUserLifetimes("org-a", "", "fagi.search")
	storeA, storeB := tenant.NewStore(), tenant.NewStore()
	url := "https://synthetic.example.test/mcp"
	storeA.Set("org-a", independentR4Config(t, true, url, "active"))
	storeB.Set("org-a", independentR4Config(t, true, url, "active"))
	raw := &independentLiteralClient{entered: make(chan context.Context, 1), release: make(chan struct{})}
	reg := mcp.NewBuiltinRegistry()
	if err := reg.Register("search", raw); err != nil {
		t.Fatal(err)
	}
	a := tenancy.New(tenancy.Options{Store: storeA, MCP: config.MCPConfig{ManagedServers: map[string]config.ManagedMCPServerConfig{"search": {Kind: config.ManagedServerKindBuiltin}}}, Deps: func() *mcp.Deps {
		return &mcp.Deps{SharedCredentials: r, Builtins: reg, ManagedServers: independentR4Managed{url}}
	}})
	defer independentR3Close(a)
	_, pool, err := a.Resolve("org-a")
	if err != nil {
		t.Fatal(err)
	}
	c, err := pool.Client("fagi.search", "", mcp.AuthOverride{})
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() {
		var err error
		switch method {
		case "tool":
			_, err = c.CallTool(context.Background(), "search", nil, mcp.CallOptions{})
		case "resource":
			_, err = c.ReadResource(context.Background(), "synthetic://item", mcp.CallOptions{})
		case "prompt":
			_, err = c.GetPrompt(context.Background(), "synthetic", nil, mcp.CallOptions{})
		}
		done <- err
	}()
	var active context.Context
	select {
	case active = <-raw.entered:
	case <-time.After(time.Second):
		close(raw.release)
		t.Fatal("supplied built-in operation did not enter")
	}
	storeB.Set("org-a", independentR4Config(t, true, url, "revoked"))
	started := time.Now()
	if err := purger.RevokePushedGrants(context.Background(), "org-a", "fagi.search", independentR4Revision); err != nil {
		close(raw.release)
		t.Fatal(err)
	}
	if awaitCancellation {
		select {
		case <-active.Done():
			t.Logf("supplied builtin cancellation=%s", time.Since(started))
		case <-time.After(2 * time.Second):
			t.Error("supplied builtin operation remained active beyond the channel cancellation bound")
		}
	}
	close(raw.release)
	select {
	case err := <-done:
		if err == nil {
			t.Error("supplied builtin returned success after remote revocation and confirmed purge of its customer grant")
		}
	case <-time.After(time.Second):
		t.Fatal("released supplied builtin did not complete")
	}
	if raw.closed.Load() {
		t.Error("ending one org's customer grant closed the registry-owned built-in")
	}
}
func TestVerifierR5SuppliedBuiltinReplicaFence(t *testing.T) {
	for _, method := range []string{"tool", "resource", "prompt"} {
		t.Run(method, func(t *testing.T) { verifierR5BuiltinReplica(t, method, false) })
	}
}
func TestVerifierR5SuppliedBuiltinReplicaCancellationBound(t *testing.T) {
	verifierR5BuiltinReplica(t, "tool", true)
}
