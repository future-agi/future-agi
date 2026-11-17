package providers

import (
	"errors"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ResolveKnownWithRouting serves only catalogue or configured models: a
// "<provider>/<model>" match or the only configured provider serves a model
// only when that provider lists it or known has it for that provider. Router
// targets and the model map resolve as ResolveWithRouting does.

func knownTestRegistry(t *testing.T, providerIDs ...string) *Registry {
	t.Helper()
	off := false
	cfg := config.DefaultConfig()
	for _, id := range providerIDs {
		cfg.Providers[id] = config.ProviderConfig{BaseURL: "http://" + id + ".invalid", APIKey: "k", APIFormat: "openai", Models: []string{id + "-listed"}, AutoDiscover: &off}
	}
	cfg.ModelMap = map[string]string{"mapped-alias": providerIDs[0]}
	r, err := NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { r.Close() })
	return r
}

func wantModelNotFound(t *testing.T, model string, err error) {
	t.Helper()
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Status != 404 || apiErr.Code != "model_not_found" {
		t.Fatalf("%s: err = %v, want 404 model_not_found", model, err)
	}
}

func TestResolveKnownWithRoutingServesOnlyKnownModels(t *testing.T) {
	r := knownTestRegistry(t, "alpha", "beta")
	catalogue := func(providerID, model string) bool { return providerID == "beta" && model == "in-catalogue" }
	for _, tc := range []struct {
		model, provider string
	}{
		{"alpha/alpha-listed", "alpha"},
		{"beta/in-catalogue", "beta"},
		{"mapped-alias", "alpha"},
		{"beta-listed", "beta"},
	} {
		res, err := r.ResolveKnownWithRouting(tc.model, catalogue)
		if err != nil || res.Provider.ID() != tc.provider {
			t.Fatalf("%s: (%v, %v), want provider %s", tc.model, res, err, tc.provider)
		}
	}
	for _, model := range []string{"alpha/not-listed", "beta/alpha-listed", "alpha/in-catalogue", "nowhere"} {
		_, err := r.ResolveKnownWithRouting(model, catalogue)
		wantModelNotFound(t, model, err)
	}
	// No catalogue: only configured models.
	_, err := r.ResolveKnownWithRouting("beta/in-catalogue", nil)
	wantModelNotFound(t, "beta/in-catalogue without a catalogue", err)
}

func TestResolveKnownWithRoutingSoleProviderServesOnlyKnownModels(t *testing.T) {
	r := knownTestRegistry(t, "solo")
	catalogue := func(providerID, model string) bool { return providerID == "solo" && model == "vendor/in-catalogue" }
	for _, model := range []string{"solo-listed", "vendor/in-catalogue"} {
		if res, err := r.ResolveKnownWithRouting(model, catalogue); err != nil || res.Provider.ID() != "solo" {
			t.Fatalf("%s: (%v, %v), want solo", model, res, err)
		}
	}
	for _, model := range []string{"not-listed", "vendor/not-listed"} {
		_, err := r.ResolveKnownWithRouting(model, catalogue)
		wantModelNotFound(t, model, err)
	}
	// ResolveWithRouting keeps the only-provider default.
	if res, err := r.ResolveWithRouting("not-listed"); err != nil || res.Provider.ID() != "solo" {
		t.Fatalf("ResolveWithRouting(not-listed) = (%v, %v), want solo", res, err)
	}
}
