package server

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A request for a model that catalog/deprecations.json
// lists past its shutdown date is refused with 410 model_expired before any
// upstream is called, on a chat route (eligibility rule 4) and on the image
// route (which runs no eligibility), even when no catalog entry carries an
// expiration date.
func TestCalendarShutdownRefusedBeforeUpstream(t *testing.T) {
	const gone, live = "google/gemini-2.5-flash-image", "google/gemini-3.1-flash-image"
	g := newEligGateway(t, eligOpts{org: optedIn(), orModels: map[string]*catalog.Model{
		gone: eligModel(gone, 0.3e-6), live: eligModel(live, 0.3e-6),
	}})

	expectRefused(t, g.chat(t, eligChat(gone, "")), http.StatusGone, eligibility.CodeModelExpired, eligibility.CodeModelExpired)
	if n := len(g.or.received()); n != 0 {
		t.Fatalf("a shut-down model reached the upstream %d time(s)", n)
	}

	w := g.chat(t, eligChat(live, ""))
	if w.Code != http.StatusOK {
		t.Fatalf("the replacement %s: status %d, body %s", live, w.Code, w.Body)
	}
	g.snapshot(t)

	// The image handler runs no eligibility; it asks shutDownModel, by the
	// serving provider's type, never its gateway id.
	h := g.srv.handlers
	if apiErr := h.shutDownModel(nil, "operator", "operator/dall-e-3"); apiErr == nil ||
		apiErr.Status != http.StatusGone || apiErr.Code != eligibility.CodeModelExpired {
		t.Fatalf("dall-e-3 on the operator's openai provider: %+v, want 410 model_expired", apiErr)
	}
	if apiErr := h.shutDownModel(nil, "operator", "gpt-image-2"); apiErr != nil {
		t.Fatalf("gpt-image-2 refused: %+v", apiErr)
	}
	org := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"azure-eastus": {Enabled: true, APIKey: "k", BaseURL: "https://acme-eastus.openai.azure.com", APIFormat: "azure"},
	}}
	if host := h.deprecationHost(org, "azure-eastus"); host != "azure" {
		t.Fatalf("an org's Azure provider named azure-eastus has host %q, want azure", host)
	}
	if host := h.deprecationHost(nil, "openrouter"); host != "openai" {
		t.Fatalf("the configured openrouter provider (api_format openai) has host %q", host)
	}
}
