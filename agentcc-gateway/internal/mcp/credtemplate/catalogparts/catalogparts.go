// Package catalogparts reads the credential parts a connector-manifest/v1
// catalog (CN1's loaded *manifest.Catalog) declares, as credtemplate's
// Catalog. It is apart from credtemplate so that credtemplate imports only
// the standard library.
package catalogparts

import (
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
)

// FromManifest is c as a credtemplate.Catalog; nil gives nil, so a gateway
// with no catalog loaded refuses every catalog_ref (credtemplate
// ForCatalogSecret).
func FromManifest(c *manifest.Catalog) credtemplate.Catalog {
	if c == nil {
		return nil
	}
	return manifestCatalog{c: c}
}

type manifestCatalog struct {
	c *manifest.Catalog
}

// CredentialParts returns the provider's version and auth.credentialParts.
func (m manifestCatalog) CredentialParts(app, provider string) (string, []credtemplate.Declared, bool) {
	a := m.c.App(app)
	if a == nil {
		return "", nil, false
	}
	p := a.Provider(provider)
	if p == nil {
		return "", nil, false
	}
	parts := make([]credtemplate.Declared, len(p.Auth.CredentialParts))
	for i, cp := range p.Auth.CredentialParts {
		parts[i] = credtemplate.Declared{
			In: credtemplate.In(cp.In), Name: cp.Name, Secret: cp.Secret, Value: cp.Value, Prefix: cp.Prefix,
		}
	}
	return p.Version, parts, true
}
