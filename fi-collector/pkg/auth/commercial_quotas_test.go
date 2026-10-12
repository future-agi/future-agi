package auth

import (
	"context"
	"log/slog"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
)

// Turning commercial quotas off (self-hosted, TH-8084) must keep Redis for
// what it is really needed for: API-key revocation.
func TestRevocationStillUsesRedisWhenCommercialQuotasAreOff(t *testing.T) {
	off := false
	cfg := Config{CommercialQuotas: &off}
	if cfg.CommercialQuotasOn() {
		t.Fatal("CommercialQuotas=false must report off")
	}

	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	t.Cleanup(func() { rdb.Close() })
	a := &Authenticator{
		cfg:   cfg,
		cache: newCache(5*time.Minute, 1*time.Hour),
		rdb:   rdb,
		log:   slog.Default(),
	}
	a.cache.putPositive("revoked-key", &ResolveResult{OrgID: "org-1"})

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go a.WatchRevocations(ctx)
	time.Sleep(100 * time.Millisecond)

	if err := rdb.Publish(ctx, revocationChannel, "revoked-key").Err(); err != nil {
		t.Fatalf("publish failed: %v", err)
	}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if _, status := a.cache.get("revoked-key"); status == "miss" {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("a revoked key stayed cached with commercial quotas off")
}
