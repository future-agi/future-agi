package credentials

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/tenancy"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Ported from the round-5 lifecycle verifier (verifier_r5_test.go). The
// eviction carries the revoked grant's revision, as Django's shared revoke
// now sends it: a revision-less purge cannot name a grant nobody admitted.
//
// The old grant is in A's pushed configuration before B revokes it. Neither
// process has constructed a client for it yet. Delayed first use must not turn
// the completed revocation into permission to bind that stale secret anew.
func TestVerifierR5UnseenPushedGrantAfterRemoteRevoke(t *testing.T) {
	for _, supplied := range []bool{false, true} {
		name, id := "org-shared", "issues"
		if supplied {
			name, id = "supplied-server", "fagi.search"
		}
		t.Run(name, func(t *testing.T) {
			r, purger := independentR4Resolvers(t)
			defer r.purgeUserLifetimes("org-a", "", id)
			storeA, storeB := tenant.NewStore(), tenant.NewStore()
			url := "https://synthetic.example.test/mcp"
			storeA.Set("org-a", independentR4Config(t, supplied, url, "active"))
			storeB.Set("org-a", independentR4Config(t, supplied, url, "active"))
			var managed mcp.ManagedServers = noManagedServers{}
			if supplied {
				managed = independentR4Managed{url}
			}
			a := tenancy.New(tenancy.Options{Store: storeA, RetryInitial: time.Hour, Deps: func() *mcp.Deps {
				return &mcp.Deps{SharedCredentials: r, ManagedServers: managed, ClientFactory: func(mcp.ClientConfig) (mcp.UpstreamClient, error) { return &lifecycleClient{}, nil }}
			}})
			defer independentR3Close(a)
			storeB.Set("org-a", independentR4Config(t, supplied, url, "revoked"))
			if err := purger.RevokePushedGrants(context.Background(), "org-a", id, independentR4Revision); err != nil {
				t.Fatal(err)
			}
			_, pool, err := a.Resolve("org-a")
			if err != nil {
				return
			}
			c, err := pool.Client(id, "", mcp.AuthOverride{})
			if err != nil {
				return
			}
			if _, err := c.CallTool(context.Background(), "search", nil, mcp.CallOptions{}); err == nil {
				t.Error("stale pushed grant first admitted after remote revoke published a client and returned success")
			}
		})
	}
}

// A cache serialization detail is not a new credential deadline. Ordinary
// cache hits on one unchanged grant must not cancel its live predecessor.
func TestVerifierR5SharedCachePrecisionPreservesLifetime(t *testing.T) {
	r, _, source := stoppingResolvers(t)
	k := SharedKey("org-a", "issues")
	expiry := time.Now().Add(time.Hour).Truncate(time.Millisecond).Add(123 * time.Microsecond)
	source.set(k, Token{AccessToken: "synthetic-precision", ExpiresAt: expiry, CredentialVersion: 1}, nil)
	first := stoppingGrant(t, r, k)
	second := stoppingGrant(t, r, k)
	if second.Check(context.Background()) != nil {
		t.Fatal("second resolution was refused")
	}
	if first.Lifetime.Err() != nil {
		t.Error("ordinary Redis cache hit cancelled an unchanged shared grant because expiry lost sub-millisecond precision")
	}
}
