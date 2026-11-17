package tenant

import (
	"errors"
	"fmt"
	"maps"
	"slices"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
)

// CodeMCPCredentialPlacementUnsupported is the 400 code for an org config
// whose pushed MCP credential secret says to put its value where this
// gateway does not.
const CodeMCPCredentialPlacementUnsupported = "mcp_credential_placement_unsupported"

// MCPSecretHeader is the header the gateway sends a pushed MCP secret in
// when the secret states no placement: credtemplate.SecretHeader,
// Authorization, as a bearer token, for type bearer or when neither the
// secret nor the server credential (referenceHeader) names a header;
// otherwise the secret's header, or failing that referenceHeader.
func MCPSecretHeader(sec *MCPCredentialSecret, referenceHeader string) string {
	return credtemplate.SecretHeader(sec.Type, sec.Header, referenceHeader)
}

// TemplateSecret is a pushed secret with its server credential's header,
// as credential templates read it.
func TemplateSecret(sec *MCPCredentialSecret, referenceHeader string) credtemplate.Secret {
	out := credtemplate.Secret{Type: sec.Type, Header: sec.Header, ReferenceHeader: referenceHeader,
		In: sec.In, Name: sec.Name, Value: sec.Value}
	for _, p := range sec.Parts {
		out.Parts = append(out.Parts, credtemplate.SecretPart{In: p.In, Name: p.Name, Value: p.Value})
	}
	return out
}

// UnappliedMCPSecret returns the field of a pushed MCP credential secret
// that a header-only path cannot apply as the secret states it, and why;
// "" when it applies the secret exactly as stated. A managed tool
// credential (mcp.managed_tools.credentials) still has only that path, so
// it refuses:
//   - parts: a credential in more than one part, which it would send only
//     in part;
//   - in other than header: query and url_variable, and any value outside
//     the contract's enum (the generated Go decode does not check enums);
//   - a name, under a header placement (in header or unset), that is not
//     that header (compared case-insensitively).
//
// An org server's secret is placed by a credential template instead
// (unappliedServerSecret). A refused secret is never sent, as a bearer
// header or otherwise. The reason never holds the secret's value.
func UnappliedMCPSecret(sec *MCPCredentialSecret, referenceHeader string) (field, reason string) {
	if sec == nil {
		return "", ""
	}
	if len(sec.Parts) > 0 {
		return "parts", "a credential in more than one part is not applied to a managed tool server, so it is " +
			"refused, never sent in part"
	}
	if sec.In != "" && sec.In != "header" {
		return "in", fmt.Sprintf("%q is a placement a managed tool server does not apply: it sends a pushed MCP "+
			"credential only in a header, so the credential is refused, never sent as a bearer header", sec.In)
	}
	if sec.Name != "" {
		if header := MCPSecretHeader(sec, referenceHeader); !strings.EqualFold(sec.Name, header) {
			return "name", fmt.Sprintf("%q is not the header this gateway sends the credential in (%s): a "+
				"header placement's name must be its header", sec.Name, header)
		}
	}
	return "", ""
}

// unappliedServerSecret is the field of an org server, under
// mcp.servers.<id>, whose pushed secret the gateway cannot place, and why;
// "" when it can. A secret that states a placement, or of a server
// added from the catalog, is checked by the same rules the MCP pool builds
// the server with (credtemplate.ForCatalogSecret: placement, names, values,
// the server's URL, and for a catalog_ref the parts cat declares, its major
// version and its constant parts; with cat nil a catalog_ref is refused, as
// the pool refuses it). A secret pushed as before keeps the header path.
func unappliedServerSecret(s *MCPServerOrgConfig, cat credtemplate.Catalog) (field, reason string) {
	sec := s.Credential.Secret
	if sec == nil {
		return "", ""
	}
	ts := TemplateSecret(sec, s.Credential.Header)
	if !credtemplate.Applies(ts, s.CatalogRef) {
		return "", ""
	}
	var ce *credtemplate.Error
	if _, err := credtemplate.ForCatalogSecret(s.URL, ts, cat, s.CatalogRef); errors.As(err, &ce) {
		if ce.Field == "url" || ce.Field == "catalog_ref" {
			return ce.Field, ce.Reason
		}
		return "credential.secret." + ce.Field, ce.Reason
	} else if err != nil {
		return "credential.secret", "cannot be placed"
	}
	return "", ""
}

// MCPPlacementError names, as a dotted path into the org config, a pushed MCP
// credential secret field the gateway cannot apply (UnappliedMCPSecret).
type MCPPlacementError struct {
	Field  string
	Reason string
}

func (e *MCPPlacementError) Error() string { return e.Field + ": " + e.Reason }

// UnappliedMCPCredentialPlacement returns the first pushed MCP credential
// secret of the config whose placement the gateway cannot apply, servers in
// id order and then managed tool credentials in id order, or nil. cat is the
// connector catalog the gateway loaded (nil: none, and a catalog_ref server
// with a shared secret is refused). Disabled servers and a disabled mcp
// section are checked too: enabling them later would send the secret. The
// admin PUT and bulk load refuse such a config (400
// CodeMCPCredentialPlacementUnsupported); the MCP pool refuses such a server
// however its config arrived (tenancy.orgSpec), by the same rules.
func (c *OrgConfig) UnappliedMCPCredentialPlacement(cat credtemplate.Catalog) *MCPPlacementError {
	if c == nil || c.MCP == nil {
		return nil
	}
	for _, id := range slices.Sorted(maps.Keys(c.MCP.Servers)) {
		s := c.MCP.Servers[id]
		if s == nil || s.Credential == nil {
			continue
		}
		if field, reason := unappliedServerSecret(s, cat); field != "" {
			return &MCPPlacementError{Field: "mcp.servers." + id + "." + field, Reason: reason}
		}
	}
	if mt := c.MCP.ManagedTools; mt != nil {
		for _, id := range slices.Sorted(maps.Keys(mt.Credentials)) {
			cred := mt.Credentials[id]
			if cred == nil {
				continue
			}
			if field, reason := UnappliedMCPSecret(cred.Secret, ""); field != "" {
				return &MCPPlacementError{Field: "mcp.managed_tools.credentials." + id + ".secret." + field, Reason: reason}
			}
		}
	}
	return nil
}
