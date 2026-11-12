package redisstate_test

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/server"
)

// Bind by structural interfaces so this exact test compiles on the unfixed
// base. There, missed-delivery behavior (not an absent symbol) is the RED.
func bindDisconnectAuthority(t *testing.T, ks *auth.KeyStore, b *redisstate.KeyRevocationBroadcaster) {
	t.Helper()
	setter, hasSetter := interface{}(ks).(interface {
		SetRevocationCheck(func(context.Context, string) (bool, error))
	})
	reader, hasReader := interface{}(b).(interface {
		CheckKeyRevocation(context.Context, string) (bool, error)
	})
	if hasSetter && hasReader && os.Getenv("TEST_REVOCATION_BYPASS_AUTHORITY") != "1" {
		setter.SetRevocationCheck(reader.CheckKeyRevocation)
	} else {
		t.Log("fallibility: cached authority not bound; missed-delivery assertion remains active")
	}
}

func TestRevocationDisconnectDuringRevoke(t *testing.T) {
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("TEST_REDIS_ADDR unset: isolated synthetic Redis required")
	}
	host, _, err := net.SplitHostPort(addr)
	if err != nil || (host != "127.0.0.1" && host != "localhost" && host != "::1") {
		t.Fatal("test requires loopback synthetic Redis")
	}
	for _, phase := range []string{"disconnected", "reconnected"} {
		t.Run(phase, func(t *testing.T) {
			suffix := strconv.FormatInt(time.Now().UnixNano(), 10)
			target, other := "synthetic-target-"+suffix, "synthetic-other-"+suffix
			raw, rawOther := "synthetic-revocation-credential-"+suffix, "synthetic-unrelated-credential-"+suffix
			stores := make([]*auth.KeyStore, 3)
			broadcasters := make([]*redisstate.KeyRevocationBroadcaster, 3)
			clients := make([]*redisstate.Client, 3)
			for i := range stores {
				client, err := redisstate.NewClient(redisstate.Config{Address: addr, Timeout: time.Second})
				if err != nil {
					t.Fatal("isolated synthetic Redis not ready")
				}
				clients[i] = client
				t.Cleanup(func() { _ = client.Close() })
				stores[i] = auth.NewKeyStore(config.AuthConfig{}, true)
				meta := map[string]string{"org_id": "synthetic-org", "user_id": "01234567-89ab-4cde-8fab-0123456789ab"}
				if stores[i].LoadFromHashes([]auth.SyncedKey{{ID: target, KeyHash: auth.HashKey(raw), Metadata: meta}, {ID: other, KeyHash: auth.HashKey(rawOther), Metadata: meta}}) != 2 {
					t.Fatal("fixtures not loaded")
				}
				broadcasters[i] = redisstate.NewKeyRevocationBroadcaster(client)
				bindDisconnectAuthority(t, stores[i], broadcasters[i])
			}
			// Cleanup only this test's synthetic tombstones, never FLUSHDB or shared data.
			markers := []interface{}{target, other}
			t.Cleanup(func() {
				ctx, cancel := context.WithTimeout(context.Background(), time.Second)
				defer cancel()
				_ = clients[0].Redis().SRem(ctx, "agentcc:key:revoked", markers...).Err()
				_ = clients[0].Redis().SRem(ctx, "agentcc:key:revoked:sha256:v1", auth.HashKey(raw)).Err()
			})
			var stopB func()
			for i := range stores {
				probe := target + "-ready-" + strconv.Itoa(i)
				markers = append(markers, probe)
				stop := disconnectSubscriber(t, broadcasters[i], stores[i], probe)
				if i == 1 {
					stopB = stop
				}
				if stores[i].Authenticate(raw) == nil || stores[i].Authenticate(rawOther) == nil {
					t.Fatal("pre-revoke positive control failed")
				}
			}
			if stores[0].Get(target) == stores[1].Get(target) {
				t.Fatal("replicas share mutable keys")
			}
			// Subscriber B is gone BEFORE revocation; Redis command access stays live.
			// A and C remain subscribed. No periodic control-plane sync is running.
			stopB()
			const admin = "synthetic-delivery-floor-admin"
			h := server.NewKeyHandlers(stores[0], admin)
			h.SetRevocationPublisher(broadcasters[0])
			req := httptest.NewRequest(http.MethodDelete, "/-/keys/"+target+"?key_id="+target, nil)
			req.Header.Set("Authorization", "Bearer "+admin)
			started := time.Now()
			ctx, cancel := context.WithDeadline(context.Background(), started.Add(5*time.Second))
			defer cancel()
			w := httptest.NewRecorder()
			h.RevokeKey(w, req.WithContext(ctx))
			if w.Code != http.StatusOK {
				t.Fatalf("admin DELETE status=%d want=200", w.Code)
			}
			if phase == "reconnected" {
				probe := target + "-reconnected"
				markers = append(markers, probe)
				disconnectSubscriber(t, broadcasters[1], stores[1], probe)
			}
			refused := make([]bool, 3)
			for {
				all := true
				for i, ks := range stores {
					if !refused[i] {
						noAuth := ks.Authenticate(raw) == nil
						active, _ := ks.LookupActive(raw)
						if noAuth && active == nil {
							elapsed := time.Since(started)
							refused[i] = elapsed <= 5*time.Second
							t.Logf("replica %d phase %s refused after %.3f ms", i, phase, float64(elapsed)/float64(time.Millisecond))
						}
					}
					if ks.Authenticate(rawOther) == nil {
						t.Fatalf("replica %d refused unrelated positive control", i)
					}
					all = all && refused[i]
				}
				if all {
					break
				}
				if time.Since(started) >= 5*time.Second {
					t.Fatalf("every replica must refuse within 5s during subscriber loss/reconnect; refused=%v", refused)
				}
				time.Sleep(10 * time.Millisecond)
			}
			// A later connection acknowledgement or import must not erase the revoke.
			stores[1].LoadFromHashes([]auth.SyncedKey{{ID: target, KeyHash: auth.HashKey(raw)}})
			if stores[1].Authenticate(raw) != nil {
				t.Error("reconnect/import reopened missed revocation")
			}
		})
	}
}

func disconnectSubscriber(t *testing.T, b *redisstate.KeyRevocationBroadcaster, ks *auth.KeyStore, probe string) func() {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done, ready := make(chan struct{}), make(chan struct{}, 1)
	go func() {
		defer close(done)
		b.Subscribe(ctx, func(id string) {
			applyCredentialTestMessage(ks, id)
			if id == probe {
				select {
				case ready <- struct{}{}:
				default:
				}
			}
		})
	}()
	var once sync.Once
	stop := func() {
		once.Do(func() {
			cancel()
			select {
			case <-done:
			case <-time.After(2 * time.Second):
				t.Error("subscriber cancellation did not complete")
			}
		})
	}
	t.Cleanup(stop)
	probeCtx, stopProbe := context.WithTimeout(ctx, 2*time.Second)
	defer stopProbe()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		b.Publish(probeCtx, probe)
		select {
		case <-ready:
			return stop
		case <-probeCtx.Done():
			t.Fatal("subscriber readiness not acknowledged")
			return stop
		case <-ticker.C:
		}
	}
}
