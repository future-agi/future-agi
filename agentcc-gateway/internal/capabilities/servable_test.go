package capabilities

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

var servableNow = time.Date(2026, 10, 16, 12, 0, 0, 0, time.UTC)

// servableCatalog is a managed OpenRouter snapshot with one model in each
// catalog state: active, blocked (by config), deprecated past its expiration
// date (answered 410 model_expired), deprecated but not yet expired, and
// degraded.
func servableCatalog(t *testing.T) *catalog.Catalog {
	t.Helper()
	s := catalog.NewStore()
	if err := s.Configure(catalog.Options{BlockedModels: []string{"vendor/blocked"}, Now: func() time.Time { return servableNow }}); err != nil {
		t.Fatal(err)
	}
	s.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: "bundled", FetchedAt: servableNow, Models: map[string]*catalog.Model{
		"vendor/active": {SupportedParameters: []string{"tools"}, InputModalities: []string{"text"}},
		"vendor/blocked": {SupportedParameters: []string{"tools", "reasoning"},
			InputModalities: []string{"text", "audio"}},
		"vendor/expired": {SupportedParameters: []string{"tools", "structured_outputs"},
			InputModalities: []string{"text", "image"}, ExpirationDate: "2026-10-01"},
		"vendor/expiring": {SupportedParameters: []string{"tools"}, InputModalities: []string{"text"},
			ExpirationDate: "2026-10-20"},
		"vendor/degraded": {SupportedParameters: []string{"tools"}, InputModalities: []string{"text", "image"}},
	}})
	if err := s.SetModelState("vendor/degraded", catalog.StateDegraded, "upstream errors"); err != nil {
		t.Fatal(err)
	}
	cat := s.Load()
	for id, want := range map[string]string{"vendor/blocked": catalog.StateBlocked, "vendor/expired": catalog.StateDeprecated,
		"vendor/expiring": catalog.StateDeprecated, "vendor/degraded": catalog.StateDegraded, "vendor/active": catalog.StateActive} {
		if m, _ := cat.Get("openrouter", id); m.State != want {
			t.Fatalf("%s state = %s, want %s", id, m.State, want)
		}
	}
	return cat
}

// TestCapabilitiesSkipUnservableModels: a blocked or expired model's
// features never widen the managed provider's row; a degraded one's do.
func TestCapabilitiesSkipUnservableModels(t *testing.T) {
	in := Inputs{Providers: builtinProviders(t), Catalog: servableCatalog(t), Audience: AudienceAdmin, Now: servableNow}
	c := cellOf(t, Build(in), EndpointChat, "openrouter")
	if c.Source != SourceCatalog || !c.Features.Tools {
		t.Fatalf("cell = %+v", c)
	}
	// Audio input and reasoning come only from the blocked model, structured
	// outputs only from the expired one.
	if c.Features.AudioInput || c.Features.Reasoning {
		t.Errorf("a blocked model widened the row: %+v", c.Features)
	}
	if c.Features.JSONSchema {
		t.Errorf("an expired model widened the row: %+v", c.Features)
	}
	// Vision comes from the expired model and the degraded one: the degraded
	// model still counts.
	if !c.Features.Vision {
		t.Errorf("a degraded model does not count: %+v", c.Features)
	}
}

// TestManagedAllowedIgnoresBlockedAndExpired: managed_allowed needs a
// servable priced model; a blocked or expired one does not count, a degraded
// or not-yet-expired one does.
func TestManagedAllowedIgnoresBlockedAndExpired(t *testing.T) {
	cat := servableCatalog(t)
	for model, want := range map[string]bool{
		"vendor/blocked": false, "vendor/expired": false, "vendor/degraded": true, "vendor/expiring": true, "vendor/active": true,
	} {
		only := model
		priced := func(endpoint, id string) bool { return id == only }
		in := Inputs{Providers: builtinProviders(t), Catalog: cat, Priced: priced, Audience: AudienceAdmin, Now: servableNow}
		if got := cellOf(t, Build(in), EndpointChat, "openrouter").ManagedAllowed; got != want {
			t.Errorf("only %s priced: managed_allowed = %v, want %v", model, got, want)
		}
		// The per-model view says the same about the model itself.
		v := BuildModel(in, model)
		if got := v.Providers["openrouter"].Endpoints[EndpointChat].ManagedAllowed; got != want {
			t.Errorf("?model=%s: managed_allowed = %v, want %v", model, got, want)
		}
	}
}
