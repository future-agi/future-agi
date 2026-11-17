package auth

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Shared with the FA bulk producer (L4-C); the empty key list is intentional.
func TestDelegated733BulkProducerContract(t *testing.T) {
	raw, err := os.ReadFile("../../../api_contracts/delegation/v1/bulk_snapshot.json")
	if err != nil {
		t.Fatal(err)
	}
	var wire struct {
		Delegation struct {
			FetchedAt time.Time `json:"fetched_at"`
		} `json:"delegation"`
	}
	if err := json.Unmarshal(raw, &wire); err != nil || wire.Delegation.FetchedAt.IsZero() {
		t.Fatalf("producer timestamp: %v", err)
	}
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write(raw) }))
	defer control.Close()
	keys := NewKeyStore(config.AuthConfig{})
	now := wire.Delegation.FetchedAt
	keys.now = func() time.Time { return now }
	_, claims, _, _, _ := fe1Fixture()
	claims.Kind, claims.Iat, claims.Exp = "batch", now.Unix(), now.Add(time.Hour).Unix()
	claims.Workspace = "44444444-4444-4444-8444-444444444444"
	if err := SyncKeysFromControlPlane(context.Background(), control.URL, "test-only-admin", keys); err != nil {
		t.Fatal(err)
	}
	if !keys.DelegatedEligible(claims, now) || len(keys.List()) != 0 {
		t.Fatal("producer fixture must admit zero-key workspace member without minting")
	}
	claims.ID = strings.Repeat("b", 32)
	if keys.DelegatedEligible(claims, now) {
		t.Fatal("producer revocation ignored")
	}
}

func TestDelegated733HealthyRefreshAndStalledFA(t *testing.T) {
	_, claims, _, _, start := fe1Fixture()
	claims.Kind, claims.Exp = "batch", start.Add(time.Hour).Unix()
	keys := NewKeyStore(config.AuthConfig{})
	var clock atomic.Int64
	clock.Store(start.UnixNano())
	keys.now = func() time.Time { return time.Unix(0, clock.Load()) }
	var latency atomic.Int64
	var fail atomic.Bool
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		clock.Add(latency.Load()) // Simulate response latency without a real sleep.
		if fail.Load() {
			w.WriteHeader(503)
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"status": true, "result": []any{}, "delegation": &DelegationSnapshot{Complete: true, Members: []DelegatedMember{{Org: claims.Org, User: claims.User, Workspace: "-"}}}})
	}))
	defer control.Close()
	sync := func() {
		t.Helper()
		if err := SyncKeysFromControlPlane(context.Background(), control.URL, "test-only-admin", keys); err != nil {
			t.Fatal(err)
		}
	}
	sync()
	// Back-to-back ticks at exactly the permitted 60s interval cannot cancel a
	// healthy batch before its replacement arrives, including a slow response.
	for tick := 1; tick <= 3; tick++ {
		clock.Store(start.Add(time.Duration(tick) * time.Minute).UnixNano())
		if !keys.DelegatedEligible(claims, keys.now()) {
			t.Fatalf("healthy refresh gap at tick %d", tick)
		}
		latency.Store(int64(14 * time.Second))
		sync()
		if !keys.DelegatedEligible(claims, keys.now()) {
			t.Fatal("freshly applied slow response rejected")
		}
	}
	applied := keys.now()
	fail.Store(true)
	latency.Store(0)
	clock.Store(applied.Add(120 * time.Second).UnixNano())
	if err := SyncKeysFromControlPlane(context.Background(), control.URL, "test-only-admin", keys); err == nil {
		t.Fatal("stalled FA succeeded")
	}
	if !keys.DelegatedEligible(claims, keys.now()) {
		t.Fatal("freshness must start at APPLY and include exactly 2 intervals")
	}
	clock.Add(1)
	if keys.DelegatedEligible(claims, keys.now()) {
		t.Fatal("genuinely stale snapshot served after tolerance")
	}
}

func TestDelegated733ConfiguredFreshnessTolerance(t *testing.T) {
	_, c, _, _, now := fe1Fixture()
	c.Kind = "batch"
	for _, interval := range []time.Duration{0, 15 * time.Second, time.Minute, 5 * time.Minute} {
		keys := NewKeyStore(config.AuthConfig{})
		keys.SetDelegationSyncInterval(interval)
		keys.SyncDelegations(&DelegationSnapshot{Complete: true, Members: []DelegatedMember{{Org: c.Org, User: c.User, Workspace: "-"}}}, now)
		want := 120 * time.Second
		if interval == 15*time.Second {
			want = 30 * time.Second
		}
		if !keys.DelegatedEligible(c, now.Add(want)) {
			t.Fatalf("interval=%s rejected tolerance boundary", interval)
		}
		if got := keys.DelegationRejection(c, now.Add(want+time.Nanosecond)); got != DelegationStale {
			t.Fatalf("interval=%s reason=%s", interval, got)
		}
	}
}

func TestDelegated733LateOldResponseCannotResurrectMembership(t *testing.T) {
	_, c, _, _, now := fe1Fixture()
	c.Kind = "batch"
	keys := NewKeyStore(config.AuthConfig{})
	live := &DelegationSnapshot{Complete: true, Members: []DelegatedMember{{Org: c.Org, User: c.User, Workspace: "-"}}}
	keys.syncDelegations(live, now, now.Add(time.Second))
	keys.syncDelegations(&DelegationSnapshot{Complete: true}, now.Add(60*time.Second), now.Add(61*time.Second))
	// Older request completes last. Its later application clock must not make
	// it newer than the already-applied authoritative removal.
	keys.syncDelegations(live, now.Add(50*time.Second), now.Add(62*time.Second))
	if got := keys.DelegationRejection(c, now.Add(62*time.Second)); got != DelegationMemberRemoved {
		t.Fatalf("old response resurrected membership: %s", got)
	}
}

func TestDelegated733RefreshAppliesRevocationWithoutTolerance(t *testing.T) {
	for _, revoked := range []bool{false, true} {
		_, c, _, _, now := fe1Fixture()
		c.Kind = "batch"
		keys := NewKeyStore(config.AuthConfig{})
		live := &DelegationSnapshot{Complete: true, Members: []DelegatedMember{{Org: c.Org, User: c.User, Workspace: "-"}}}
		keys.SyncDelegations(live, now)
		next := &DelegationSnapshot{Complete: true}
		if revoked {
			next.Members = live.Members
			next.RevokedGrants = []string{c.ID}
		}
		// FA commits just after t0. The very next sync (t0+60s) must stop items;
		// the outage tolerance must NEVER defer an applied removal/revocation.
		keys.SyncDelegations(next, now.Add(time.Minute))
		if keys.DelegatedEligible(c, now.Add(time.Minute)) {
			t.Fatal("revocation deferred by stale tolerance")
		}
	}
}
