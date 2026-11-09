package server

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestProviderConfigurationOperatorIDs(t *testing.T) {
	// This fixture tests config reporting, not upstream model discovery.
	off := false
	srv := r7ConfigServer(t, func(cfg *config.Config) {
		cfg.Providers = map[string]config.ProviderConfig{
			"operator-openai": {APIFormat: "openai", BaseURL: "https://synthetic.invalid", APIKey: "synthetic-secret", AutoDiscover: &off},
		}
	})
	out, raw := r7GetConfig(t, srv)
	var ids []string
	if err := json.Unmarshal(out["provider_ids"], &ids); err != nil {
		t.Fatal(err)
	}
	if len(ids) != 1 || ids[0] != "operator-openai" || strings.Contains(raw, "synthetic-secret") || strings.Contains(raw, "synthetic.invalid") {
		t.Fatalf("expected only operator provider IDs, got %s", raw)
	}
}

func TestProviderConfigurationErrorNamesMissingEndpoint(t *testing.T) {
	cache := providers.NewOrgProviderCache(nil, false)
	_, err := cache.GetOrCreateWithTenantConfig("synthetic-org", "typo-provider", "synthetic-key", &tenant.ProviderConfig{APIFormat: "openai"})
	if err == nil {
		t.Fatal("provider with no endpoint was built")
	}
	apiErr := orgProviderError("model", "typo-provider", err)
	if apiErr.Status != http.StatusBadRequest || apiErr.Code != "provider_configuration_error" ||
		!strings.Contains(apiErr.Message, "typo-provider") || !strings.Contains(apiErr.Message, "base_url") ||
		strings.Contains(apiErr.Message, "synthetic-key") || strings.Contains(apiErr.Message, "unsupported protocol scheme") {
		t.Fatalf("wrong public configuration error: %+v", apiErr)
	}
}
