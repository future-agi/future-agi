package redisstate_test

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/server"
)

// This is an in-process two-replica integration test, not a deployment test.
// Only Redis pub/sub connects the independent stores; no periodic sync runs.
// The opt-in negative control disables B's subscriber without changing the
// assertions: TEST_REVOCATION_NO_B_SUBSCRIBER=1 must FAIL after five seconds.
func TestKeyRevocationAcrossReplicas(t *testing.T) {
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("skipping: TEST_REDIS_ADDR is not set")
	}
	newBroadcaster := func() *redisstate.KeyRevocationBroadcaster {
		t.Helper()
		client, err := redisstate.NewClient(redisstate.Config{Address: addr, Timeout: time.Second})
		if err != nil {
			// An explicitly requested Redis row must fail, not silently skip.
			t.Fatal("cannot connect to the configured synthetic Redis")
		}
		t.Cleanup(func() {
			if err := client.Close(); err != nil {
				t.Error(err)
			}
		})
		return redisstate.NewKeyRevocationBroadcaster(client)
	}
	broadcasterA, broadcasterB := newBroadcaster(), newBroadcaster()
	storeA := auth.NewKeyStore(config.AuthConfig{}, true)
	storeB := auth.NewKeyStore(config.AuthConfig{}, true)
	metadata := map[string]string{"org_id": "revocation-org", "user_id": "01234567-89ab-4cde-8fab-0123456789ab"}
	key, rawKey := storeA.Create("revocation-target", "", nil, nil, metadata)
	other, rawOther := storeA.Create("unrelated-key", "", nil, nil, metadata)
	if key == nil || other == nil {
		t.Fatal("failed to create person-owned key fixtures")
	}
	if loaded := storeB.LoadFromHashes([]auth.SyncedKey{
		{ID: key.ID, KeyHash: key.KeyHash, Metadata: metadata},
		{ID: other.ID, KeyHash: other.KeyHash, Metadata: metadata},
	}); loaded != 2 {
		t.Fatalf("replica B loaded %d keys, want 2", loaded)
	}
	if storeA.Get(key.ID) == storeB.Get(key.ID) || storeA.Get(other.ID) == storeB.Get(other.ID) {
		t.Fatal("replicas must not share mutable APIKey objects")
	}

	// Probe with unique, unknown IDs until each callback acknowledges receipt.
	// This avoids the publish-before-subscribe race without an arbitrary sleep
	// or a global PUBSUB NUMSUB count that could include other tests' clients.
	startRevocationSubscriber(t, broadcasterA, storeA, key.ID+"-ready-A")
	if os.Getenv("TEST_REVOCATION_NO_B_SUBSCRIBER") == "1" {
		t.Log("fallibility: replica B subscriber NOT started; revocation assertion must fail at 5s")
	} else {
		startRevocationSubscriber(t, broadcasterB, storeB, key.ID+"-ready-B")
	}
	for name, store := range map[string]*auth.KeyStore{"A": storeA, "B": storeB} {
		if store.Authenticate(rawKey) == nil || store.Authenticate(rawOther) == nil {
			t.Fatalf("replica %s must accept both keys before DELETE", name)
		}
	}

	const adminToken = "synthetic-revocation-admin"
	handlerA := server.NewKeyHandlers(storeA, adminToken)
	handlerA.SetRevocationPublisher(broadcasterA)
	// RevokeKey is the real admin DELETE handler. The router normally copies
	// the path parameter into key_id; supply that same input without a listener.
	req := httptest.NewRequest(http.MethodDelete, "/-/keys/"+key.ID+"?key_id="+key.ID, nil)
	req.Header.Set("Authorization", "Bearer "+adminToken)
	w := httptest.NewRecorder()
	started := time.Now()
	deadline := started.Add(5 * time.Second)
	ctx, cancel := context.WithDeadline(context.Background(), deadline)
	defer cancel()
	req = req.WithContext(ctx)
	handlerA.RevokeKey(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("replica A DELETE status = %d, want 200", w.Code)
	}
	if storeA.Authenticate(rawKey) != nil {
		t.Fatal("replica A still accepts the key immediately after DELETE")
	}

	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	var refusedWithinDeadline bool
waitForRevocation:
	for {
		refused := storeB.Authenticate(rawKey) == nil
		elapsed := time.Since(started)
		if refused {
			t.Logf("replica B revocation observed %.3f ms after admin DELETE began", float64(elapsed)/float64(time.Millisecond))
			refusedWithinDeadline = elapsed <= 5*time.Second
			break
		}
		if !time.Now().Before(deadline) {
			t.Logf("replica B still accepts the key after %.3f ms", float64(elapsed)/float64(time.Millisecond))
			break
		}
		select {
		case <-ctx.Done():
			// Check authentication once more at the deadline below.
			t.Logf("replica B accepts key at 5s deadline: %v", storeB.Authenticate(rawKey) != nil)
			break waitForRevocation
		case <-ticker.C:
		}
	}
	for name, store := range map[string]*auth.KeyStore{"A": storeA, "B": storeB} {
		if store.Authenticate(rawOther) == nil {
			t.Errorf("replica %s refused the unrelated key after DELETE", name)
		}
	}
	if !refusedWithinDeadline {
		t.Fatal("replica B did not refuse the revoked key within 5s of admin DELETE")
	}
}

func startRevocationSubscriber(t *testing.T, broadcaster *redisstate.KeyRevocationBroadcaster, store *auth.KeyStore, probeID string) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	ready := make(chan struct{}, 1)
	done := make(chan struct{})
	go func() {
		defer close(done)
		broadcaster.Subscribe(ctx, func(keyID string) {
			applyCredentialTestMessage(store, keyID)
			if keyID == probeID {
				select {
				case ready <- struct{}{}:
				default:
				}
			}
		})
	}()
	t.Cleanup(func() {
		cancel()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			t.Error("revocation subscriber did not stop after cancellation")
		}
	})
	probeCtx, stopProbe := context.WithTimeout(ctx, 5*time.Second)
	defer stopProbe()
	ticker := time.NewTicker(25 * time.Millisecond)
	defer ticker.Stop()
	for {
		broadcaster.Publish(probeCtx, probeID)
		select {
		case <-ready:
			return
		case <-probeCtx.Done():
			t.Fatal("revocation subscriber did not acknowledge readiness probe")
		case <-ticker.C:
		}
	}
}
