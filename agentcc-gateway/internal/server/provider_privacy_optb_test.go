package server

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestOptBProviderErrorUsesOrgOrGlobalRedactor(t *testing.T) {
	for _, orgPolicy := range []bool{false, true} {
		t.Run(map[bool]string{false: "global", true: "org"}[orgPolicy], func(t *testing.T) {
			cfg := config.DefaultConfig()
			cfg.Privacy = config.PrivacyConfig{Enabled: true, Mode: "patterns", Patterns: []config.RedactPatternConfig{{Name: "global", Pattern: "operator-private"}}}
			store := tenant.NewStore()
			if orgPolicy {
				store.Set("fixture", &tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{Enabled: true, Mode: "patterns", Patterns: []*tenant.RedactPatternConfig{{Name: "org", Pattern: "tenant-private"}}}})
			}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatal(err)
			}
			defer registry.Close()
			s := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), store, nil)
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Provider = "fixture"
			rc.CredentialSource = "org"
			rc.Metadata["org_id"] = "fixture"
			rc.Metadata["billing_mode"] = "byok"
			_, org := s.handlers.resolveOrgConfig(rc)
			s.handlers.applyOrgPrivacyOverrides(org, rc)
			value := "operator-private"
			if orgPolicy {
				value = "tenant-private"
			}
			original := &models.APIError{Status: 400, ProviderStatus: 400, Provider: "fixture", Message: value, Code: value, Type: value, Metadata: map[string]any{"nested": []any{value}}}
			out := providerFailure(rc, original)
			data, err := json.Marshal(out)
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(string(data), value) {
				t.Fatal("configured redactor missed a provider error field")
			}
			if original.Message != value {
				t.Fatal("redaction modified internal provider error")
			}
		})
	}
}
