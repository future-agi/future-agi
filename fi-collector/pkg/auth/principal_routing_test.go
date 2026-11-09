package auth

import (
	"context"
	"errors"
	"log/slog"
	"testing"
	"time"
)

func TestRoutingAuthorityComesOnlyFromPrincipal(t *testing.T) {
	for _, principal := range []Principal{PrincipalPersonal, PrincipalPlatform, ""} {
		t.Run(string(principal), func(t *testing.T) {
			sim := &routeSim{project: hiddenProject()}
			a, _ := newRouter(t, sim)
			result := sandboxResult()
			result.Principal = principal
			result.KeyType = keyTypeIngest
			traces := routeTraces("customer-prod")
			out, err := a.RouteSimulation(context.Background(), "key", traces, result)
			if err != nil || out.Hidden != "" || out.DroppedSpans() != 0 || sim.callCount() != 0 {
				t.Fatal("legacy key type acquired routing authority")
			}
			if got, _ := resourceStr(t, traces, 0, "project_name"); got != "customer-prod" {
				t.Fatal("non-sandbox principal was rerouted")
			}
		})
	}
}

func TestPersonalRevocationBoundWithoutRedis(t *testing.T) {
	pool := routingDatabase(t)
	f := newEligibilityFixture()
	seedEligibility(t, pool, f)
	a, err := New(context.Background(), Config{PGWrite: pool.Config().ConnString(), Peppers: mustPeppers(t, goldenPepper)}, nil, slog.Default())
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	if _, err := a.Authenticate(context.Background(), f.api, f.secret); err != nil {
		t.Fatal(err)
	}
	routingFixture{t, pool}.exec(`UPDATE accounts_organization_membership SET deleted=true`)
	entry, _ := a.cache.get(CacheKey(f.api, f.secret))
	entry.fetchedAt = time.Now().Add(-30 * time.Second)
	if _, err := a.Authenticate(context.Background(), f.api, f.secret); !errors.Is(err, ErrUnauthenticated) {
		t.Fatalf("revocation without Redis: %v", err)
	}
}
