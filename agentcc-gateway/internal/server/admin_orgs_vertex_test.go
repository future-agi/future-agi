package server

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestRedactOrgConfigMasksVertexServiceAccount(t *testing.T) {
	secret := `{"private_key":"sensitive"}`
	input := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"vertex": {ServiceAccountJSON: secret, Enabled: true},
	}}
	redacted := redactOrgConfig(input)
	if redacted.Providers["vertex"].ServiceAccountJSON != "****" {
		t.Fatal("service account was not masked")
	}
	if input.Providers["vertex"].ServiceAccountJSON != secret {
		t.Fatal("redaction mutated the live provider credential")
	}
}
