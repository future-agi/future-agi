package providers

import (
	"context"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Provenance must be attached to the constructed instance without changing
// what that instance is. Callers and existing tests assert the concrete
// adapter (for example *speech.Provider) on org-cache instances, so wrapping
// the adapter in another type is not an acceptable way to carry the marker.
func TestOptBR5OrgProvenanceKeepsConcreteAdapterType(t *testing.T) {
	for _, format := range []string{"openai", "anthropic", "azure", "bedrock", "cohere", "gemini", "google", "deepgram", "cartesia"} {
		t.Run(format, func(t *testing.T) {
			cfg := config.ProviderConfig{APIFormat: format, BaseURL: "http://127.0.0.1:1", APIKey: "synthetic-platform"}
			var tenantCfg *tenant.ProviderConfig
			if format == "bedrock" {
				cfg.AWSAccessKeyID, cfg.AWSSecretAccessKey = "fixture-platform-access", "fixture-platform-secret"
				tenantCfg = &tenant.ProviderConfig{AWSAccessKeyID: "fixture-tenant-access", AWSSecretAccessKey: "fixture-tenant-secret"}
			}
			global, err := createProvider("edge", cfg)
			if err != nil {
				t.Fatal(err)
			}
			defer global.Close()
			cache := NewOrgProviderCache(map[string]config.ProviderConfig{"edge": cfg}, false)
			defer cache.EvictAll()
			org, err := cache.GetOrCreateWithTenantConfig("acme", "edge", "synthetic-tenant", tenantCfg)
			if err != nil {
				t.Fatal(err)
			}
			if !IsOrgInstance(org, "acme") {
				t.Fatal("org-cache construction lost its provenance")
			}
			if reflect.TypeOf(org) != reflect.TypeOf(global) {
				t.Fatalf("org instance is %T; the adapter type must be preserved (registry builds %T)", org, global)
			}
		})
	}
}

// The org cache is the only authority, and it withdraws that authority when it
// closes and removes an instance: a closed instance must not keep claiming a
// tenant's credentials, and a replacement instance is bound on its own.
func TestOptBR5OrgProvenanceFollowsCacheLifetime(t *testing.T) {
	cfg := config.ProviderConfig{APIFormat: "openai", BaseURL: "http://127.0.0.1:1", APIKey: "synthetic-platform"}
	cache := NewOrgProviderCache(map[string]config.ProviderConfig{"edge": cfg}, false)
	defer cache.EvictAll()
	first, err := cache.GetOrCreate("acme", "edge", "synthetic-tenant")
	if err != nil {
		t.Fatal(err)
	}
	other, err := cache.GetOrCreate("beta", "edge", "synthetic-other")
	if err != nil {
		t.Fatal(err)
	}
	if !IsOrgInstance(first, "acme") || IsOrgInstance(first, "beta") || !IsOrgInstance(other, "beta") {
		t.Fatal("instances are not bound to the org that built them")
	}
	cache.Evict("acme")
	if IsOrgInstance(first, "acme") {
		t.Error("an evicted instance kept its provenance")
	}
	if !IsOrgInstance(other, "beta") {
		t.Error("evicting one org dropped another org's provenance")
	}
	second, err := cache.GetOrCreate("acme", "edge", "synthetic-tenant")
	if err != nil {
		t.Fatal(err)
	}
	if second == first || !IsOrgInstance(second, "acme") || IsOrgInstance(first, "acme") {
		t.Error("the replacement instance was not bound on its own")
	}
	cache.EvictAll()
	if IsOrgInstance(second, "acme") || IsOrgInstance(other, "beta") {
		t.Error("EvictAll left provenance behind")
	}
}

type r5LookalikeValue struct {
	id   string
	tags []string // makes the dynamic type unhashable
}

func (p r5LookalikeValue) ID() string { return p.id }
func (r5LookalikeValue) ChatCompletion(context.Context, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	return nil, nil
}
func (r5LookalikeValue) StreamChatCompletion(context.Context, *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	return nil, nil
}
func (r5LookalikeValue) ListModels(context.Context) ([]models.ModelObject, error) { return nil, nil }
func (r5LookalikeValue) Close() error                                             { return nil }

type r5LookalikePointer struct{ r5LookalikeValue }

// A provider that merely names itself like an org instance, whatever its Go
// type, is never an org instance, and asking never panics.
func TestOptBR5OrgProvenanceIgnoresLookalikesOfAnyType(t *testing.T) {
	for _, p := range []Provider{
		r5LookalikeValue{id: "edge_org_acme"},
		&r5LookalikePointer{r5LookalikeValue{id: "edge_org_acme"}},
	} {
		if IsOrgInstance(p, "acme") {
			t.Errorf("%T named like an org instance was trusted", p)
		}
	}
}
