package credentials

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

func pushedGrant(t *testing.T, r *Resolver, org, server, identity string, revision int64) *mcp.AuthOverride {
	t.Helper()
	a, err := r.PushedCredential(context.Background(), org, server, mcp.PushedGrant{Identity: identity, Revision: revision})
	if err != nil {
		t.Fatal(err)
	}
	return a
}

func TestCredentialLifecyclePushedGrantNeverAdoptsNewEpoch(t *testing.T) {
	r, replica, _ := stoppingResolvers(t)
	ctx := context.Background()
	old := pushedGrant(t, r, "org-a", "issues", "synthetic-old-identity", 1)
	otherOrg := pushedGrant(t, r, "org-b", "issues", "synthetic-old-identity", 1)
	otherServer := pushedGrant(t, r, "org-a", "other", "synthetic-old-identity", 1)
	defer r.purgeUserLifetimes("org-a", "", "other")
	if _, err := replica.PurgeCredentials(ctx, "org-a", "u1", "issues"); err != nil {
		t.Fatal(err)
	}
	if err := old.Check(ctx); err != nil {
		t.Fatal("personal eviction cancelled the organization's pushed grant")
	}
	if _, err := replica.PurgeCredentials(ctx, "org-a", "", "issues"); err != nil {
		t.Fatal(err)
	}
	if old.Check(ctx) == nil {
		t.Fatal("evicted grant passed its completion fence")
	}
	fresh := pushedGrant(t, replica, "org-a", "issues", "synthetic-new-identity", 2)
	// A newly created resolver has no local knowledge of the revoked identity.
	// Redis, not process memory, must reject its stale config's first attempt.
	restarted, err := NewResolver(ResolverOptions{Tokens: r.tokens, Cache: NewTokenCache(r.cache.client, testCodec(t), CacheOptions{Prefix: r.cache.prefix}), Codec: testCodec(t)})
	if err != nil {
		t.Fatal(err)
	}
	for _, candidate := range []*Resolver{r, replica, restarted} {
		if stale, err := candidate.PushedCredential(ctx, "org-a", "issues", mcp.PushedGrant{Identity: "synthetic-old-identity", Revision: 1}); err == nil || stale != nil {
			t.Fatal("stale config adopted the evicted identity into a new epoch")
		}
	}
	for _, live := range []*mcp.AuthOverride{fresh, otherOrg, otherServer} {
		if live.Lifetime.Err() != nil || live.Check(ctx) != nil {
			t.Fatal("stale grant resolution cancelled a successor or unrelated identity")
		}
	}
}

// A rotation's successor can be admitted before the eviction of its
// predecessor lands. Revoking through the old revision must stop only that
// grant: never the successor, and never a person's grant on the same server.
func TestCredentialLifecyclePushedRevisionEvictionSparesSuccessorAndPersonal(t *testing.T) {
	r, replica, source := stoppingResolvers(t)
	ctx := context.Background()
	personal := Key{OrgID: "org-a", UserID: "u1", ServerID: "issues"}
	defer r.purgeUserLifetimes("org-a", "u1", "issues")
	stoppingSeed(source, personal, 1)
	member := stoppingGrant(t, r, personal)
	old := pushedGrant(t, r, "org-a", "issues", "synthetic-rotated-from", 1)
	successor := pushedGrant(t, replica, "org-a", "issues", "synthetic-rotated-to", 2)
	if err := replica.RevokePushedGrants(ctx, "org-a", "issues", 1); err != nil {
		t.Fatal(err)
	}
	if old.Check(ctx) == nil {
		t.Error("the revoked revision passed its completion fence")
	}
	if successor.Lifetime.Err() != nil || successor.Check(ctx) != nil {
		t.Error("revoking the predecessor's revision cancelled its already-admitted successor")
	}
	if member.Lifetime.Err() != nil || member.Check(ctx) != nil {
		t.Error("revoking a shared revision cancelled a person's grant on the same server")
	}
	// A delayed, repeated or reordered eviction can never lower the value.
	if err := r.RevokePushedGrants(ctx, "org-a", "issues", 1); err != nil {
		t.Fatal(err)
	}
	if again, err := r.PushedCredential(ctx, "org-a", "issues", mcp.PushedGrant{Identity: "synthetic-rotated-to", Revision: 2}); err != nil || again.Check(ctx) != nil {
		t.Fatal("the live successor was refused on another replica", err)
	}
	if err := r.RevokePushedGrants(ctx, "org-a", "issues", 2); err != nil {
		t.Fatal(err)
	}
	if successor.Check(ctx) == nil {
		t.Error("the successor survived the eviction of its own revision")
	}
}

// With a distributed authority, a pushed grant whose push names no revision
// can never be fenced on a replica that has not admitted it, so it is never
// authorized, before or after an eviction.
func TestCredentialLifecyclePushedUnregisteredGrantRefused(t *testing.T) {
	r, replica, _ := stoppingResolvers(t)
	ctx := context.Background()
	if a, err := r.PushedCredential(ctx, "org-a", "issues", mcp.PushedGrant{Identity: "synthetic-unregistered"}); err == nil || a != nil {
		t.Fatal("a pushed grant with no revision was authorized")
	}
	if err := replica.RevokePushedGrants(ctx, "org-a", "issues", 1); err != nil {
		t.Fatal(err)
	}
	if a, err := r.PushedCredential(ctx, "org-a", "issues", mcp.PushedGrant{Identity: "synthetic-unregistered"}); err == nil || a != nil {
		t.Fatal("a pushed grant with no revision was authorized after an eviction")
	}
}

func TestCredentialLifecyclePushedCancellationBounds(t *testing.T) {
	for _, mode := range []string{"pubsub", "legacy-purge", "disconnected-subscriber-and-lost-message"} {
		t.Run(mode, func(t *testing.T) {
			r, replica, _ := stoppingResolvers(t)
			ctx := context.Background()
			k := SharedKey("org-a", "issues")
			old := pushedGrant(t, r, k.OrgID, k.ServerID, "synthetic-old", 1)
			bound := 2 * time.Second
			start := time.Now()
			switch mode {
			case "pubsub":
				if err := replica.RevokePushedGrants(ctx, k.OrgID, k.ServerID, 1); err != nil {
					t.Fatal(err)
				}
			case "legacy-purge":
				if _, err := replica.PurgeCredentials(ctx, k.OrgID, "", k.ServerID); err != nil {
					t.Fatal(err)
				}
			default:
				bound = 5 * time.Second
				if err := r.cache.client.Redis().ClientKillByFilter(ctx, "TYPE", "pubsub", "SKIPME", "yes").Err(); err != nil {
					t.Fatal(err)
				}
				start = time.Now()
				// The eviction's write lands but its message is lost.
				if err := r.cache.client.Redis().Set(ctx, r.cache.pushedRevokedKey(k), 1, 0).Err(); err != nil {
					t.Fatal(err)
				}
			}
			stoppingAwait(t, old.Lifetime.Done(), start, bound)
			fresh := pushedGrant(t, r, k.OrgID, k.ServerID, "synthetic-new", 2)
			if fresh.Check(ctx) != nil {
				t.Fatal("late invalidation cancelled the new identity")
			}
			start = time.Now()
			if err := replica.RevokePushedGrants(ctx, k.OrgID, k.ServerID, 2); err != nil {
				t.Fatal(err)
			}
			stoppingAwait(t, fresh.Lifetime.Done(), start, 5*time.Second)
		})
	}
}

func TestCredentialLifecyclePushedExpiryAtCompletion(t *testing.T) {
	r, _, _ := stoppingResolvers(t)
	now := time.Now()
	r.now = func() time.Time { return now }
	a, err := r.PushedCredential(context.Background(), "org-a", "issues", mcp.PushedGrant{Identity: "synthetic-expiring", Revision: 1, Expiry: now.Add(time.Minute)})
	if err != nil {
		t.Fatal(err)
	}
	now = now.Add(time.Minute)
	if a.Check(context.Background()) == nil || a.Lifetime.Err() == nil {
		t.Fatal("expired pushed grant passed the completion fence")
	}
}
