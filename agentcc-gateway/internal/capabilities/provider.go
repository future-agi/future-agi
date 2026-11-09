package capabilities

import (
	"net/url"
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// Credential types (capabilities-v1 credential_type, the attempt's enum).
const (
	CredentialManaged = "managed"
	CredentialBYOK    = "byok"
)

// Provider describes one provider for the matrix.
type Provider struct {
	ID string
	// Interfaces are the adapter interfaces it implements: InterfacesOf a
	// built adapter, or FormatInterfaces for one the gateway has not built.
	Interfaces InterfaceSet
	APIFormat  string
	// Upstream names the API behind an OpenAI-compatible adapter: the
	// config's provider type, else "openai" or "mistral" from the base URL's
	// host, else "" (unknown).
	Upstream string
	// Models are the model ids config lists for it.
	Models         []string
	Managed        bool
	ResaleTermsRef string
	CredentialType string
}

// FromRegistry describes every provider in reg, reading each one's api_format,
// type, models and resale terms from cfg (config.yaml's providers, from which
// reg was built). A provider registered at runtime has no config entry and is
// described by its adapter alone.
func FromRegistry(reg *providers.Registry, cfg map[string]config.ProviderConfig) []Provider {
	if reg == nil {
		return nil
	}
	ids := reg.ProviderIDs()
	sort.Strings(ids)
	out := make([]Provider, 0, len(ids))
	for _, id := range ids {
		adapter, ok := reg.GetProvider(id)
		if !ok {
			continue
		}
		pc := cfg[id]
		p := Provider{
			ID:             id,
			Interfaces:     InterfacesOf(adapter),
			APIFormat:      configFormat(pc),
			Upstream:       UpstreamOf(pc.Type, pc.BaseURL),
			Models:         pc.Models,
			Managed:        reg.IsManaged(id),
			ResaleTermsRef: pc.ResaleTermsRef,
			CredentialType: CredentialBYOK,
		}
		if p.Managed {
			p.CredentialType = CredentialManaged
		}
		out = append(out, p)
	}
	return out
}

// configFormat is pc's api_format, or its preset's when it names only a type.
func configFormat(pc config.ProviderConfig) string {
	if pc.APIFormat != "" {
		return pc.APIFormat
	}
	return providers.KnownProviders[pc.Type].APIFormat
}

// UpstreamOf names the API a provider's adapter calls: its config type when
// set, else the vendor its base URL's host belongs to, else "".
func UpstreamOf(providerType, baseURL string) string {
	if providerType != "" {
		return strings.ToLower(providerType)
	}
	u, err := url.Parse(baseURL)
	if err != nil {
		return ""
	}
	switch strings.ToLower(u.Hostname()) {
	case "api.openai.com":
		return "openai"
	case "api.mistral.ai":
		return "mistral"
	}
	return ""
}
