package auth

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

type revocationCheckSetter interface {
	SetRevocationCheck(func(context.Context, string) (bool, error))
}

func revocationCheckStore(t *testing.T) *KeyStore {
	t.Helper()
	ks := NewKeyStore(config.AuthConfig{}, true)
	if ks.LoadFromHashes([]SyncedKey{
		{ID: "synthetic-target", KeyHash: HashKey("synthetic-target-credential"), Metadata: map[string]string{"org_id": "synthetic-org", "user_id": "01234567-89ab-4cde-8fab-0123456789ab"}},
		{ID: "synthetic-other", KeyHash: HashKey("synthetic-other-credential"), Metadata: map[string]string{"org_id": "synthetic-org", "user_id": "01234567-89ab-4cde-8fab-0123456789ab"}},
	}) != 2 {
		t.Fatal("synthetic fixtures not loaded")
	}
	return ks
}

func bindRevocationCheck(t *testing.T, ks *KeyStore, check func(context.Context, string) (bool, error)) {
	t.Helper()
	setter, ok := interface{}(ks).(revocationCheckSetter)
	if !ok {
		t.Fatal("cached active authentication lacks revocation freshness binding")
	}
	setter.SetRevocationCheck(check)
}

func TestCachedAuthenticationConsultsRevocationAuthority(t *testing.T) {
	ks := revocationCheckStore(t)
	var revoked, unavailable atomic.Bool
	var calls atomic.Int64
	bindRevocationCheck(t, ks, func(ctx context.Context, id string) (bool, error) {
		calls.Add(1)
		if deadline, ok := ctx.Deadline(); !ok || time.Until(deadline) > time.Second {
			t.Error("authority call must have a deadline of at most one second")
		}
		if unavailable.Load() {
			return false, errors.New("synthetic-unavailable")
		}
		return id == HashKey("synthetic-target-credential") && revoked.Load(), nil
	})
	if ks.Authenticate("synthetic-target-credential") == nil || ks.Authenticate("synthetic-other-credential") == nil {
		t.Fatal("positive controls failed")
	}
	revoked.Store(true)
	if ks.Authenticate("synthetic-target-credential") != nil {
		t.Error("cached revoked key bypassed authority")
	}
	if key, _ := ks.LookupActive("synthetic-target-credential"); key != nil {
		t.Error("LookupActive bypassed authority")
	}
	if ks.Authenticate("synthetic-other-credential") == nil {
		t.Error("unrelated key refused")
	}
	if key, meta := ks.LookupActive("synthetic-other-credential"); key == nil || meta["org_id"] != "synthetic-org" {
		t.Error("unrelated lookup metadata lost")
	}
	unavailable.Store(true)
	if ks.Authenticate("synthetic-other-credential") != nil {
		t.Error("unknown freshness used cached active authentication")
	}
	if key, _ := ks.LookupActive("synthetic-other-credential"); key != nil {
		t.Error("unknown freshness used cached active lookup")
	}
	unavailable.Store(false)
	if ks.Authenticate("synthetic-other-credential") == nil {
		t.Error("authority recovery must restore unrelated key")
	}
	// nil must not silently remove an installed production freshness check.
	interface{}(ks).(revocationCheckSetter).SetRevocationCheck(nil)
	if ks.Authenticate("synthetic-target-credential") != nil {
		t.Error("nil binding disabled revocation checks")
	}
	before := calls.Load()
	if ks.Authenticate("synthetic-unknown-credential") != nil || calls.Load() != before {
		t.Error("unknown hashes must not query authority")
	}
	if calls.Load() < 10 {
		t.Error("every cached active auth and lookup must recheck freshness")
	}
}

func TestRevocationCheckDoesNotHoldStoreLock(t *testing.T) {
	ks := revocationCheckStore(t)
	entered, release := make(chan struct{}), make(chan struct{})
	bindRevocationCheck(t, ks, func(context.Context, string) (bool, error) {
		close(entered)
		<-release
		return false, nil
	})
	done := make(chan *APIKey, 1)
	go func() { done <- ks.Authenticate("synthetic-target-credential") }()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("authority not called")
	}
	revoked := make(chan bool, 1)
	go func() { revoked <- ks.Revoke("synthetic-target") }()
	select {
	case ok := <-revoked:
		if !ok {
			t.Error("local revoke failed")
		}
	case <-time.After(time.Second):
		t.Error("authority read held the key-store lock")
	}
	close(release)
	select {
	case key := <-done:
		if key != nil {
			t.Error("concurrent local revoke lost to stale authority response")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("authentication did not finish")
	}
}

func TestRevocationCheckExpiredResultIsNotFresh(t *testing.T) {
	ks := revocationCheckStore(t)
	bindRevocationCheck(t, ks, func(ctx context.Context, _ string) (bool, error) {
		select {
		case <-ctx.Done():
			return false, nil
		case <-time.After(2 * time.Second):
			return false, errors.New("synthetic-missing-deadline")
		}
	})
	if ks.Authenticate("synthetic-target-credential") != nil {
		t.Error("expired authority result reopened cached key")
	}
}

func TestRevocationSingleInstanceCompatibility(t *testing.T) {
	ks := revocationCheckStore(t)
	if ks.Authenticate("synthetic-target-credential") == nil {
		t.Error("single-instance positive failed")
	}
	ks.Revoke("synthetic-target")
	if ks.Authenticate("synthetic-target-credential") != nil {
		t.Error("single-instance local revoke failed")
	}
	if ks.Authenticate("synthetic-other-credential") == nil {
		t.Error("single-instance unrelated key refused")
	}
}
