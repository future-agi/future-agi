package redisstate_test

import (
	"context"
	"net"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

const historicalIDs = "agentcc:key:revoked"
const historicalHashes = "agentcc:key:revoked:sha256:v1"

func bindHistoricalAuthority(t *testing.T, ks *auth.KeyStore, b *redisstate.KeyRevocationBroadcaster) {
	t.Helper()
	setter, hasSetter := interface{}(ks).(interface {
		SetRevocationIdentityCheck(func(context.Context, string, string) (bool, error))
	})
	reader, hasReader := interface{}(b).(interface {
		CheckCredentialRevocation(context.Context, string, string) (bool, error)
	})
	if hasSetter && hasReader {
		setter.SetRevocationIdentityCheck(reader.CheckCredentialRevocation)
	} else {
		// Pinned-base RED is the actual admission failure, not a missing symbol.
		ks.SetRevocationCheck(b.CheckKeyRevocation)
	}
}

func historicalClient(t *testing.T) *redisstate.Client {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Fatal("TEST_REDIS_ADDR must name isolated synthetic Redis; no skipped qualification")
	}
	host, _, err := net.SplitHostPort(addr)
	if err != nil || (host != "127.0.0.1" && host != "localhost" && host != "::1") {
		t.Fatal("historical revocation tests require loopback synthetic Redis")
	}
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, Timeout: time.Second})
	if err != nil {
		t.Fatal("synthetic Redis not ready")
	}
	t.Cleanup(func() { _ = client.Close() })
	return client
}

func historicalStore(t *testing.T, id, raw, clean string) *auth.KeyStore {
	t.Helper()
	ks := auth.NewKeyStore(config.AuthConfig{}, true)
	meta := map[string]string{"org_id": "synthetic-history-org", "user_id": "01234567-89ab-4cde-8fab-0123456789ab"}
	if ks.LoadFromHashes([]auth.SyncedKey{
		{ID: id, KeyHash: auth.HashKey(raw), Metadata: meta},
		{ID: id + "-clean", KeyHash: auth.HashKey(clean), Metadata: meta},
	}) != 2 {
		t.Fatal("synthetic fixtures did not load")
	}
	return ks
}

func assertHistoricalDenied(t *testing.T, ks *auth.KeyStore, raw string) {
	t.Helper()
	if ks.Authenticate(raw) != nil {
		t.Error("historical revocation reopened Authenticate")
	}
	if key, _ := ks.LookupActive(raw); key != nil {
		t.Error("historical revocation reopened LookupActive")
	}
}

func TestHistoricalRevocationsBeforeDuringAfterMigration(t *testing.T) {
	client := historicalClient(t)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	for _, resolved := range []bool{true, false} {
		t.Run(strconv.FormatBool(resolved), func(t *testing.T) {
			suffix := strconv.FormatInt(time.Now().UnixNano(), 10)
			id, raw, clean := "synthetic-historical-"+suffix, "synthetic-old-"+suffix, "synthetic-clean-"+suffix
			b := redisstate.NewKeyRevocationBroadcaster(client)
			stores := make([]*auth.KeyStore, 3)
			for i := range stores {
				stores[i] = historicalStore(t, id, raw, clean)
				bindHistoricalAuthority(t, stores[i], b)
				if stores[i].Authenticate(raw) == nil {
					t.Fatal("positive control denied before tombstone")
				}
			}
			if client.Redis().SAdd(ctx, historicalIDs, id).Err() != nil {
				t.Fatal("cannot seed synthetic pre-cutover tombstone")
			}
			t.Cleanup(func() {
				_ = client.Redis().SRem(context.Background(), historicalIDs, id).Err()
				_ = client.Redis().SRem(context.Background(), historicalHashes, auth.HashKey(raw)).Err()
			})
			for _, ks := range stores {
				assertHistoricalDenied(t, ks, raw)
			}
			// This is the migration wire contract. Django tests execute the
			// real RunPython, including partial-write and replay failures.
			if resolved && client.Redis().SAdd(ctx, historicalHashes, auth.HashKey(raw)).Err() != nil {
				t.Fatal("cannot seed migrated synthetic hash")
			}
			for i := range stores {
				newID := id
				if resolved {
					newID += "-relocated"
				}
				stores[i] = historicalStore(t, newID, raw, clean)
				bindHistoricalAuthority(t, stores[i], b)
				assertHistoricalDenied(t, stores[i], raw)
				if stores[i].Authenticate(clean) == nil {
					t.Error("unrelated credential denied after migration")
				}
				if key, _ := stores[i].LookupActive(clean); key == nil {
					t.Error("unrelated lookup denied after migration")
				}
			}
			if retained, err := client.Redis().SIsMember(ctx, historicalIDs, id).Result(); err != nil || !retained {
				t.Error("historical ID must survive the cutover")
			}
		})
	}
}

func TestHistoricalUnresolvedConfigurationSlotStaysDenied(t *testing.T) {
	client := historicalClient(t)
	ctx := context.Background()
	if client.Redis().SAdd(ctx, historicalIDs, "key_1").Err() != nil {
		t.Fatal("cannot seed synthetic historical slot")
	}
	t.Cleanup(func() { _ = client.Redis().SRem(ctx, historicalIDs, "key_1").Err() })
	// A historical unknown credential cannot be inferred from this CURRENT
	// slot occupant. By design, that ID stays denied.
	for _, raw := range []string{"synthetic-original-slot", "synthetic-reused-slot"} {
		ks := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Key: raw}}}, false)
		bindHistoricalAuthority(t, ks, redisstate.NewKeyRevocationBroadcaster(client))
		assertHistoricalDenied(t, ks, raw)
	}
}

func TestHistoricalAuthorityUnavailableNeverAllows(t *testing.T) {
	ks := historicalStore(t, "synthetic-unavailable", "synthetic-denied", "synthetic-other")
	bindHistoricalAuthority(t, ks, redisstate.NewKeyRevocationBroadcaster(nil))
	assertHistoricalDenied(t, ks, "synthetic-denied")
	assertHistoricalDenied(t, ks, "synthetic-other")
}

func TestHistoricalLegacyReadFailureNeverAllows(t *testing.T) {
	client := historicalClient(t)
	ctx := context.Background()
	// Dedicated fresh Redis is required: never overwrite a shared namespace.
	if n, err := client.Redis().Exists(ctx, historicalIDs).Result(); err != nil || n != 0 {
		t.Fatal("this row requires a fresh isolated historical namespace")
	}
	if client.Redis().Set(ctx, historicalIDs, "synthetic-wrong-type", 0).Err() != nil {
		t.Fatal("cannot seed synthetic read failure")
	}
	t.Cleanup(func() { _ = client.Redis().Del(ctx, historicalIDs).Err() })
	ks := historicalStore(t, "synthetic-read-error", "synthetic-denied", "synthetic-other")
	bindHistoricalAuthority(t, ks, redisstate.NewKeyRevocationBroadcaster(client))
	assertHistoricalDenied(t, ks, "synthetic-denied")
}

func TestHistoricalMigratedHashDelivery(t *testing.T) {
	client := historicalClient(t)
	b := redisstate.NewKeyRevocationBroadcaster(client)
	raw := "synthetic-migrated-delivery-" + strconv.FormatInt(time.Now().UnixNano(), 10)
	ks := historicalStore(t, "synthetic-relocated-delivery", raw, "synthetic-unrelated-delivery")
	// Real subscriber, with the same identity callback as production startup.
	stop, delivered := slotReplaySubscriber(t, b, ks, "synthetic-history-ready", auth.HashKey(raw))
	defer stop()
	if client.Redis().Publish(context.Background(), "agentcc:key:revoke", "sha256:"+auth.HashKey(raw)).Err() != nil {
		t.Fatal("cannot publish synthetic migration notification")
	}
	select {
	case <-delivered:
	case <-time.After(2 * time.Second):
		t.Fatal("migrated hash was not consumed")
	}
	assertHistoricalDenied(t, ks, raw)
	if ks.Authenticate("synthetic-unrelated-delivery") == nil {
		t.Error("migrated notification denied an unrelated credential")
	}
}
