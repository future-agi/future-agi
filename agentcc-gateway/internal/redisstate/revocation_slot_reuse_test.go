package redisstate_test

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/server"
)

// This test compiles on the complete round-one tree. Its RED is the reused-slot
// positive assertion, not a new API, setup failure, or unavailable Redis.
func TestRevocationConfigSlotReuse(t *testing.T) {
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("TEST_REDIS_ADDR unset: isolated synthetic Redis required")
	}
	host, _, err := net.SplitHostPort(addr)
	if err != nil || (host != "127.0.0.1" && host != "localhost" && host != "::1") {
		t.Fatal("test requires loopback synthetic Redis")
	}
	for _, scenario := range []string{"shift_existing", "replace_credential"} {
		t.Run(scenario, func(t *testing.T) {
			suffix := strconv.FormatInt(time.Now().UnixNano(), 10)
			rawA := "synthetic-slot-revoked-" + suffix
			rawB := "synthetic-slot-existing-" + suffix
			rawC := "synthetic-slot-replacement-" + suffix
			cfg := func(raws ...string) config.AuthConfig {
				var keys []config.AuthKeyConfig
				for _, raw := range raws {
					keys = append(keys, config.AuthKeyConfig{Key: raw, Metadata: map[string]string{"org_id": "synthetic-slot-org"}})
				}
				return config.AuthConfig{Keys: keys}
			}
			clients := make([]*redisstate.Client, 3)
			broadcasters := make([]*redisstate.KeyRevocationBroadcaster, 3)
			stores := make([]*auth.KeyStore, 3)
			for i := range stores {
				client, err := redisstate.NewClient(redisstate.Config{Address: addr, Timeout: time.Second})
				if err != nil {
					t.Fatal("isolated synthetic Redis not ready")
				}
				clients[i] = client
				t.Cleanup(func() { _ = client.Close() })
				broadcasters[i] = redisstate.NewKeyRevocationBroadcaster(client)
				stores[i] = auth.NewKeyStore(cfg(rawA, rawB), false)
				bindDisconnectAuthority(t, stores[i], broadcasters[i])
				if stores[i].Authenticate(rawA) == nil || stores[i].Authenticate(rawB) == nil {
					t.Fatal("pre-revoke configured positives must authenticate")
				}
			}
			// Use a fresh dedicated Redis per runlist row: old round-one key_1
			// markers cannot be namespaced by this test. Cleanup touches only
			// this test's synthetic members, never database-wide state.
			t.Cleanup(func() {
				ctx, cancel := context.WithTimeout(context.Background(), time.Second)
				defer cancel()
				_ = clients[0].Redis().SRem(ctx, "agentcc:key:revoked", "key_1").Err()
				_ = clients[0].Redis().SRem(ctx, "agentcc:key:revoked:sha256:v1", auth.HashKey(rawA)).Err()
			})
			// No subscriber is running. The real admin DELETE must create the
			// replayable marker; transport delivery cannot mask the negative.
			started := time.Now()
			if status := slotRevoke(t, stores[0], broadcasters[0], "key_1"); status != http.StatusOK {
				t.Fatalf("known configured DELETE status=%d want=200", status)
			}
			positive := rawB
			if scenario == "replace_credential" {
				positive = rawC
			}
			for i := range stores {
				// Both a reorder and a brand-new credential reuse key_1. A now
				// occupies key_2, so an ID-based negative cannot satisfy this.
				stores[i] = auth.NewKeyStore(cfg(positive, rawA), false)
				bindDisconnectAuthority(t, stores[i], broadcasters[i])
				if stores[i].Get("key_1").KeyHash != auth.HashKey(positive) {
					t.Fatal("fixture did not reuse the configuration slot")
				}
				if stores[i].Authenticate(positive) == nil {
					t.Errorf("replica %d: reused-slot unrelated credential must authenticate", i)
				}
				if key, _ := stores[i].LookupActive(positive); key == nil {
					t.Errorf("replica %d: reused-slot unrelated credential must remain lookup-active", i)
				}
				if stores[i].Authenticate(rawA) != nil {
					t.Errorf("replica %d: revoked credential must remain refused after slot move", i)
				}
				if key, _ := stores[i].LookupActive(rawA); key != nil {
					t.Errorf("replica %d: revoked credential must remain lookup-refused after slot move", i)
				}
				elapsed := time.Since(started)
				t.Logf("replica %d scenario %s replay checked after %.3f ms", i, scenario, float64(elapsed)/float64(time.Millisecond))
				if elapsed > 5*time.Second {
					t.Errorf("replica %d: slot-reuse replay exceeded five-second refusal budget", i)
				}
			}
			// Re-deliver A after subscribers restart with B/C in A's old slot.
			// A stale transport payload must identify A, not key_1. The retained
			// helper is adapted only to prefer the identity-aware callback.
			old := auth.NewKeyStore(cfg(rawA, rawB), false)
			for i := range stores {
				probe := "synthetic-slot-probe-" + suffix + "-" + strconv.Itoa(i)
				stop, delivered := slotReplaySubscriber(t, broadcasters[i], stores[i], probe, auth.HashKey(rawA))
				if status := slotRevoke(t, old, broadcasters[0], "key_1"); status != http.StatusOK {
					t.Fatalf("replayed DELETE status=%d want=200", status)
				}
				select {
				case <-delivered:
				case <-time.After(2 * time.Second):
					t.Fatal("subscriber did not consume replayed revocation")
				}
				stop()
				if stores[i].Authenticate(positive) == nil {
					t.Errorf("replica %d: subscriber replay revoked the reused-slot credential", i)
				}
				if stores[i].Get("key_2").IsActive() {
					t.Errorf("replica %d: identity subscriber must locally revoke A at its new slot", i)
				}
			}
		})
	}
}

func slotRevoke(t *testing.T, ks *auth.KeyStore, b *redisstate.KeyRevocationBroadcaster, id string) int {
	t.Helper()
	const admin = "synthetic-slot-admin"
	h := server.NewKeyHandlers(ks, admin)
	h.SetRevocationPublisher(b)
	req := httptest.NewRequest(http.MethodDelete, "/-/keys/"+id+"?key_id="+id, nil)
	req.Header.Set("Authorization", "Bearer "+admin)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	w := httptest.NewRecorder()
	h.RevokeKey(w, req.WithContext(ctx))
	return w.Code
}

// The readiness and delivery acknowledgements are specific to this connection.
// Structural dispatch preserves an executable tests-only RED on round one.
func slotReplaySubscriber(t *testing.T, b *redisstate.KeyRevocationBroadcaster, ks *auth.KeyStore, probe, hash string) (func(), <-chan struct{}) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done, ready, delivered := make(chan struct{}), make(chan struct{}, 1), make(chan struct{}, 1)
	go func() {
		defer close(done)
		b.Subscribe(ctx, func(message string) {
			if receiver, ok := interface{}(ks).(interface{ ApplyRevocation(string) bool }); ok {
				receiver.ApplyRevocation(message)
			} else {
				ks.Revoke(message)
			}
			var notify chan struct{}
			if message == probe {
				notify = ready
			} else if message == "sha256:"+hash || message == "key_1" {
				notify = delivered
			}
			select {
			case notify <- struct{}{}:
			default:
			}
		})
	}()
	stop := func() {
		cancel()
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			t.Error("slot subscriber did not stop")
		}
	}
	t.Cleanup(stop)
	deadline := time.NewTimer(2 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		b.Publish(ctx, probe)
		select {
		case <-ready:
			return stop, delivered
		case <-deadline.C:
			t.Fatal("slot subscriber readiness not acknowledged")
			return stop, delivered
		case <-ticker.C:
		}
	}
}
