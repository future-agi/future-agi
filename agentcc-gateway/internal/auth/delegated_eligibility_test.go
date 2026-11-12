package auth

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestDelegated733SyncedEligibility(t *testing.T) {
	_, c, _, _, now := fe1Fixture()
	c.Kind = "batch"
	member := DelegatedMember{Org: c.Org, User: c.User, Workspace: "-"}
	cases := []struct {
		name     string
		snapshot *DelegationSnapshot
		age      time.Duration
		want     bool
	}{
		{"zero keys live person", &DelegationSnapshot{Complete: true, Members: []DelegatedMember{member}}, 0, true},
		{"absent", nil, 0, false},
		{"incomplete", &DelegationSnapshot{Members: []DelegatedMember{member}}, 0, false},
		{"empty authoritative", &DelegationSnapshot{Complete: true}, 0, false},
		{"refresh tick", &DelegationSnapshot{Complete: true, Members: []DelegatedMember{member}}, 60 * time.Second, true},
		{"last fresh instant", &DelegationSnapshot{Complete: true, Members: []DelegatedMember{member}}, 120 * time.Second, true},
		{"stale beyond tolerance", &DelegationSnapshot{Complete: true, Members: []DelegatedMember{member}}, 120*time.Second + time.Nanosecond, false},
		{"revoked", &DelegationSnapshot{Complete: true, Members: []DelegatedMember{member}, RevokedGrants: []string{c.ID}}, 0, false},
		{"malformed", &DelegationSnapshot{Complete: true, Members: []DelegatedMember{member}, RevokedGrants: []string{"bad"}}, 0, false},
	}
	for _, tt := range cases {
		t.Run(tt.name, func(t *testing.T) {
			keys := NewKeyStore(config.AuthConfig{})
			keys.SyncDelegations(tt.snapshot, now.Add(-tt.age))
			if got := keys.DelegatedEligible(c, now); got != tt.want {
				t.Fatalf("eligible=%v, want %v", got, tt.want)
			}
		})
	}
	keys := NewKeyStore(config.AuthConfig{})
	keys.SyncDelegations(&DelegationSnapshot{Complete: true, Members: []DelegatedMember{member}}, now)
	c.Workspace = "44444444-4444-4444-8444-444444444444"
	if keys.DelegatedEligible(c, now) {
		t.Fatal("org membership alone admitted workspace")
	}
	keys.SyncDelegations(&DelegationSnapshot{Complete: true, Members: []DelegatedMember{member, {Org: c.Org, User: c.User, Workspace: c.Workspace}}}, now)
	if !keys.DelegatedEligible(c, now) {
		t.Fatal("workspace membership rejected")
	}
	keys.SyncDelegations(&DelegationSnapshot{Complete: true}, now.Add(time.Second))
	keys.SyncDelegations(&DelegationSnapshot{Complete: true, Members: []DelegatedMember{member}}, now)
	c.Workspace = "-"
	if keys.DelegatedEligible(c, now.Add(time.Second)) {
		t.Fatal("older overlapping sync resurrected membership")
	}
}
func TestDelegated733BulkSyncWithoutAnyPersonalKey(t *testing.T) {
	_, c, _, _, _ := fe1Fixture()
	c.Kind = "batch"
	c.Iat = time.Now().Unix()
	c.Exp = c.Iat + 600
	var snapshot atomic.Pointer[DelegationSnapshot]
	snapshot.Store(&DelegationSnapshot{Complete: true, Members: []DelegatedMember{{Org: c.Org, User: c.User, Workspace: "-"}}})
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/agentcc/api-keys/bulk/" {
			t.Error("unexpected sync path")
		}
		json.NewEncoder(w).Encode(map[string]any{"status": true, "result": []any{}, "delegation": snapshot.Load()})
	}))
	defer control.Close()
	ks := NewKeyStore(config.AuthConfig{})
	if err := SyncKeysFromControlPlane(context.Background(), control.URL, "test-only-admin", ks); err != nil {
		t.Fatal(err)
	}
	if !ks.DelegatedEligible(c, time.Now()) || len(ks.List()) != 0 {
		t.Fatal("zero-key principal failed or minted a key")
	}
	snapshot.Store(&DelegationSnapshot{Complete: true, Members: []DelegatedMember{{Org: c.Org, User: c.User, Workspace: "-"}}, RevokedGrants: []string{strings.Repeat("a", 32)}})
	if err := SyncKeysFromControlPlane(context.Background(), control.URL, "test-only-admin", ks); err != nil {
		t.Fatal(err)
	}
	if ks.DelegatedEligible(c, time.Now()) {
		t.Fatal("revocation was not applied independently of empty key-list guard")
	}
}
