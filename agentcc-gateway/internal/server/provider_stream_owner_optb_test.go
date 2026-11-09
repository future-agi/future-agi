package server

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

type optBOwnerProvider struct {
	providers.Provider
	id string
}

func (p optBOwnerProvider) ID() string { return p.id }

func TestOptBPublicationStreamUsesDispatchedInstance(t *testing.T) {
	cache := providers.NewOrgProviderCache(map[string]config.ProviderConfig{
		"fixture": {APIFormat: "openai", BaseURL: "http://127.0.0.1:1", APIKey: "fixture-platform"},
	}, false)
	defer cache.EvictAll()
	orgInstance, err := cache.GetOrCreate("org", "fixture", "fixture-tenant")
	if err != nil {
		t.Fatal(err)
	}
	for _, orgOwned := range []bool{false, true} {
		rc := models.AcquireRequestContext()
		rc.Provider = "fixture"
		rc.Metadata["org_id"] = "org"
		rc.CredentialSource = "org"
		rc.Metadata["billing_mode"] = "byok"
		rc.AddAttempt(models.Attempt{Provider: "previous", Model: "fixture"})
		rc.AddAttempt(models.Attempt{Provider: "fixture", Model: "fixture"})
		// A matching id is not ownership; only the org-cache construction is.
		var selected providers.Provider = optBOwnerProvider{id: "fixture_org_org"}
		if orgOwned {
			selected = orgInstance
		}
		original := &models.APIError{Status: 400, ProviderStatus: 400, Message: "opaque synthetic diagnostic", Code: "opaque_code", Type: "opaque_type"}
		out := clientStreamError(rc, original, selected)
		if orgOwned && !strings.Contains(out.Message, original.Message) {
			t.Error("BYOK stream text lost after fallback")
		}
		if !orgOwned && (strings.Contains(out.Message, original.Message) || out.Code == original.Code || out.Type == original.Type) {
			t.Error("billing metadata exempted a platform stream")
		}
		if original.Provider != "" {
			t.Error("stream publication mutated original error")
		}
		rc.Release()
	}
}
