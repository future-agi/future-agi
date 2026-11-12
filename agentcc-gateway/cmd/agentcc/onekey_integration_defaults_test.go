package main

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// These rows cover activation, not the safety checks that protect an explicitly
// enabled deployment. Credit reservation and adapter validation must stay on.
func TestShippedIntegrationActivationOff(t *testing.T) {
	for _, shipped := range shippedGatewayConfigs(t) {
		t.Run(shipped.name, func(t *testing.T) {
			cfg, err := loadExample(t, shipped.text)
			if err != nil {
				t.Fatal(err)
			}
			for id, provider := range cfg.Providers {
				if provider.Managed {
					t.Errorf("provider %q activates Future AGI models in a shipped config", id)
				}
			}
			// No shipped model chain may implicitly activate the mixed
			// catalogue/token fallback profile, even if its providers are BYOK.
			if len(cfg.Routing.ModelFallbacks) != 0 {
				t.Error("shipped config enables a model fallback profile")
			}
			if cfg.Routing.Failover.Enabled || cfg.Routing.Retry.Enabled {
				t.Error("shipped config enables provider failover or retries")
			}
			// No shipped config may let a request's attempts cross billing
			// mode (managed to byok or back): MIXED-HOP-GUARD-1.
			if cfg.Managed.MixedBillingChains {
				t.Error("shipped config enables mixed managed/byok billing chains")
			}
		})
	}
	// Non-vacuous control: the key the OFF row reads is the one the loader
	// fills, so a misspelt or dropped yaml tag fails here rather than
	// leaving the row above trivially true.
	for _, tc := range []struct {
		text string
		want bool
	}{
		{"managed:\n  mixed_billing_chains: true\n", true},
		{"managed:\n  mixed_billing_chains: false\n", false},
		{"managed:\n  require_credits: true\n", false},
	} {
		cfg, err := loadExample(t, tc.text)
		if err != nil {
			t.Fatalf("loading %q: %v", tc.text, err)
		}
		if cfg.Managed.MixedBillingChains != tc.want {
			t.Errorf("%q loads managed.mixed_billing_chains=%v, want %v", tc.text, cfg.Managed.MixedBillingChains, tc.want)
		}
	}
	for _, file := range declarationFiles(t) {
		if strings.Contains(readRepoFile(t, file), newOrgDeclaration) {
			t.Errorf("%s enables the new-organization activation declaration", file)
		}
	}
}

func TestShippedIntegrationNewOrgFeaturesOff(t *testing.T) {
	for _, org := range []*tenant.OrgConfig{nil, {}, {ManagedModels: &tenant.ManagedModelsConfig{}, Routing: &tenant.RoutingConfig{}}} {
		policy := org.ManagedPolicy()
		if policy.Enabled || policy.FallbackToManaged || policy.Hedging || policy.AllowDataCollection {
			t.Errorf("an organization without opt-in enables a new managed feature: %+v", policy)
		}
		if org != nil && org.Routing.FallbackOn() {
			t.Error("an organization without a configured chain enables model fallback")
		}
	}
}

func TestShippedIntegrationManagedFormatAllowlist(t *testing.T) {
	allowed := map[string]bool{"openai": true, "azure": true, "anthropic": true, "gemini": true, "google": true, "cohere": true, "bedrock": true}
	for _, shipped := range shippedGatewayConfigs(t) {
		t.Run(shipped.name, func(t *testing.T) {
			cfg, err := loadExample(t, shipped.text)
			if err != nil {
				t.Fatal(err)
			}
			for id, provider := range cfg.Providers {
				if provider.Managed && !allowed[provider.APIFormat] {
					t.Errorf("managed provider %q uses an unverified native format %q", id, provider.APIFormat)
				}
			}
		})
	}
	// Non-vacuous controls: the shipped configurations have managed off, but
	// opting in must still reject formats outside the usage-presence matrices.
	for _, format := range []string{"openai", "azure", "anthropic", "gemini", "google", "cohere", "bedrock", "deepgram", "cartesia", "vllm", "OpenAI", "unverified-native"} {
		t.Run(format, func(t *testing.T) {
			cfg := config.DefaultConfig()
			cfg.Providers["synthetic"] = config.ProviderConfig{BaseURL: "http://127.0.0.1:9", APIFormat: format, Managed: true}
			err := cfg.Validate()
			if allowed[format] {
				if err != nil {
					t.Fatalf("presence-verified format refused: %v", err)
				}
			} else if err == nil || !strings.Contains(err.Error(), "managed") || !strings.Contains(err.Error(), format) {
				t.Fatalf("unverified managed format %q must be refused, got %v", format, err)
			}
		})
	}
}
