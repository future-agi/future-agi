package server

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

func TestDXW4PlatformAttributionIdempotent(t *testing.T) {
	rc := &models.RequestContext{Provider: "fixture", RequestID: "w4"}
	e := models.ErrUpstreamProvider(502, "opaque-provider-text")
	once := providerFailure(rc, e).(*models.APIError)
	if providerFailure(rc, once) != once || attributeProviderFailure(once, "other", credentialOrg) != once {
		t.Fatal("publication was attributed again")
	}
	wrapped := routing.SkipHop(once, "deadline")
	if providerFailure(rc, wrapped) != wrapped {
		t.Fatal("attribution lost the wrapper")
	}
	once.Message = "changed-provider-text"
	if out := providerFailure(rc, once).(*models.APIError); strings.Contains(out.Message, "changed-provider-text") {
		t.Fatal("modified publication bypassed projection")
	}
	spoofed := models.ErrUpstreamProvider(502, "spoofed-provider-text")
	spoofed.Metadata = map[string]any{"provider": "fixture"}
	if out := providerFailure(rc, spoofed).(*models.APIError); strings.Contains(out.Message, "spoofed-provider-text") || out.Metadata != nil {
		t.Fatal("metadata impersonated completed publication")
	}
}
