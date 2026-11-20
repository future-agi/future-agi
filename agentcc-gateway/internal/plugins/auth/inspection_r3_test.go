package auth

import (
	"context"
	"testing"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestInspectionPrincipalR3AuthenticatedBinding(t *testing.T) {
	keys := authpkg.NewKeyStore(config.AuthConfig{Enabled: true, Keys: []config.AuthKeyConfig{
		{Name: "reader", Key: "synthetic-reader", Metadata: map[string]string{"org_id": "reader-org"}},
		{Name: "other", Key: "synthetic-other", Metadata: map[string]string{"org_id": "other-org"}},
		{Name: "no-org", Key: "synthetic-no-org"},
	}})
	plugin := New(keys, true)
	state := models.NewInspectionState(models.InspectionContract{Operation: "internal.test"})
	ctx := models.WithInspection(context.Background(), state)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["authorization"] = "Bearer synthetic-reader"
	rc.Metadata["org_id"] = "forged-creator-org"
	rc.Metadata["auth_key_id"] = "forged-creator-key"
	if result := plugin.ProcessRequest(ctx, rc); result.Error != nil {
		t.Fatal(result.Error)
	}
	orgID, keyID := state.Principal()
	if orgID != "reader-org" || keyID != "key_1" || rc.Inspection != state {
		t.Fatalf("bound %q/%q attached=%v", orgID, keyID, rc.Inspection == state)
	}
	for _, key := range []string{"synthetic-other", "synthetic-no-org"} {
		rc.Metadata["authorization"] = "Bearer " + key
		if result := plugin.ProcessRequest(ctx, rc); result.Error == nil || result.Error.Status != 403 {
			t.Fatalf("changed principal accepted: %+v", result)
		}
		if org, id := state.Principal(); org != orgID || id != keyID {
			t.Fatal("failed authentication changed the bound principal")
		}
	}
}

func TestInspectionPrincipalR3NoBindingBeforeAccessChecks(t *testing.T) {
	for _, tc := range []struct {
		name, key, model, ip string
		enabled              bool
	}{
		{"invalid", "missing", "allowed", "127.0.0.1", true},
		{"model-denied", "synthetic", "denied", "127.0.0.1", true},
		{"network-denied", "synthetic", "allowed", "192.0.2.1", true},
		{"disabled", "synthetic", "allowed", "127.0.0.1", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			keys := authpkg.NewKeyStore(config.AuthConfig{Enabled: true, Keys: []config.AuthKeyConfig{{Name: "restricted", Key: "synthetic", Models: []string{"allowed"}, AllowedIPs: []string{"127.0.0.1"}, Metadata: map[string]string{"org_id": "org"}}}})
			state := models.NewInspectionState(models.InspectionContract{Operation: "internal.test"})
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Inspection = state
			rc.Model = tc.model
			rc.Metadata["authorization"] = "Bearer " + tc.key
			rc.Metadata["client_ip"] = tc.ip
			result := New(keys, tc.enabled).ProcessRequest(context.Background(), rc)
			if tc.enabled && result.Error == nil {
				t.Fatal("access denial missing")
			}
			if org, key := state.Principal(); org != "" || key != "" {
				t.Fatalf("principal bound before access checks: %q/%q", org, key)
			}
		})
	}
}

func TestInspectionPrincipalR3NoOrgLegacyKey(t *testing.T) {
	keys := authpkg.NewKeyStore(config.AuthConfig{Enabled: true, Keys: []config.AuthKeyConfig{{Name: "legacy", Key: "synthetic"}}})
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Inspection = models.NewInspectionState(models.InspectionContract{})
	rc.Metadata["authorization"] = "Bearer synthetic"
	if result := New(keys, true).ProcessRequest(context.Background(), rc); result.Error != nil {
		t.Fatal(result.Error)
	}
	if org, key := rc.Inspection.Principal(); org != "" || key != "" {
		t.Fatalf("non-tenant key unexpectedly selected: %q/%q", org, key)
	}
}
