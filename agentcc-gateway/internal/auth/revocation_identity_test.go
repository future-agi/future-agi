package auth

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestRevocationIdentitySurvivesSlotMove(t *testing.T) {
	const rawA = "synthetic-identity-A"
	const rawB = "synthetic-identity-B"
	ks := NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Key: rawB}, {Key: rawA}}}, false)
	ks.SetRevocationCheck(func(_ context.Context, hash string) (bool, error) {
		return hash == HashKey(rawA), nil
	})
	if ks.Authenticate(rawA) != nil {
		t.Error("credential hash guard did not refuse A after slot move")
	}
	if key, _ := ks.LookupActive(rawA); key != nil {
		t.Error("credential hash guard did not refuse A lookup after slot move")
	}
	if ks.Authenticate(rawB) == nil {
		t.Error("credential hash guard refused unrelated slot occupant")
	}
	if key, _ := ks.LookupActive(rawB); key == nil {
		t.Error("credential hash guard refused unrelated lookup")
	}
}

func TestRevocationIdentitySubscriberAndCapture(t *testing.T) {
	const rawA = "synthetic-delivery-A"
	const rawB = "synthetic-delivery-B"
	ks := NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Key: rawB}, {Key: rawA}}}, false)
	if ks.ApplyRevocation("key_1") || ks.Authenticate(rawB) == nil {
		t.Error("legacy slot message must not revoke configured replacement")
	}
	if !ks.ApplyRevocation("sha256:"+HashKey(rawA)) || ks.Authenticate(rawA) != nil {
		t.Error("credential delivery must revoke A at its new slot")
	}
	if ks.Authenticate(rawB) == nil {
		t.Error("credential delivery must preserve unrelated slot occupant")
	}
	for range 2 {
		if ok, hash := ks.RevokeWithIdentity("key_2"); !ok || hash != HashKey(rawA) {
			t.Error("atomic local revoke must retain hash for initial publish and retry")
		}
	}
	if ok, hash := ks.RevokeWithIdentity("synthetic-absent"); ok || hash != "" {
		t.Error("unknown ID must not fabricate a credential identity")
	}
}
