package emit

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"slices"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// GatewaySchema is gateway-servers.json's schema identifier.
const GatewaySchema = "connector-gateway-servers/v1"

// gatewayServers writes one entry per http provider (§2.4, "Gateway org
// server entry"; §2.6 step 2). Its server member holds exactly the
// MCPServerOrgConfig fields the manifest decides (tenant/config.go), keyed
// as the org config keys them: url, a template whose {variables} the org's
// values fill when the server is added (each declared in urlVariables; {connectorsHost},
// for a rest provider, is the operator's agentcc-connectors host),
// transport, era when pinned, trusted_annotations only for a reviewed
// provider, timeout_ms, forward_headers and catalog_ref. The org's
// credential is never in it: credentialModes lists the modes the server may
// take, and credentialParts says where each part of an API key goes, and
// whether the customer supplies it or the catalog does, never a value.
// A stdio provider has no entry: an org server is remote.
func gatewayServers(cat *manifest.Catalog, meta Meta) (Files, error) {
	servers := []any{}
	var errs []error
	for _, a := range cat.Apps {
		for _, p := range a.Providers {
			if p.Transport.Kind != "http" {
				continue
			}
			entry, err := gatewayEntry(a, p)
			if err != nil {
				errs = append(errs, fmt.Errorf("%s: %w", catalogRef(p), err))
				continue
			}
			servers = append(servers, entry)
		}
	}
	if err := errors.Join(errs...); err != nil {
		return nil, err
	}
	doc := envelope(GatewaySchema, meta)
	doc = append(doc, canonjson.Member{Key: "servers", Value: servers})
	data, err := jsonFile(doc)
	if err != nil {
		return nil, err
	}
	return Files{"gateway-servers.json": data}, nil
}

func gatewayEntry(a *manifest.App, p *manifest.Provider) (canonjson.Object, error) {
	ref := catalogRef(p)
	server := canonjson.Object{{Key: "url", Value: p.Transport.URL}, {Key: "transport", Value: gatewayTransport(p)}}
	if era := p.Transport.Era; era != "" && era != "auto" {
		server = append(server, canonjson.Member{Key: "era", Value: era})
	}
	if p.Trusted {
		server = append(server, canonjson.Member{Key: "trusted_annotations", Value: true})
	}
	if x := p.XGateway; x != nil {
		if x.TimeoutMs > 0 {
			server = append(server, canonjson.Member{Key: "timeout_ms", Value: int64(x.TimeoutMs)})
		}
		if len(x.ForwardHeaders) > 0 {
			server = append(server, canonjson.Member{Key: "forward_headers", Value: stringList(x.ForwardHeaders)})
		}
	}
	server = append(server, canonjson.Member{Key: "catalog_ref", Value: ref})

	entry := canonjson.Object{
		{Key: "catalogRef", Value: ref},
		{Key: "app", Value: a.ID},
		{Key: "provider", Value: p.ID},
		{Key: "default", Value: a.DefaultProvider == p.ID},
		{Key: "kind", Value: p.Kind},
		{Key: "auth", Value: p.Auth.Kind},
		{Key: "credentialModes", Value: stringList(credentialModes(p))},
	}
	if partner := p.Auth.Partner; p.Kind == manifest.KindPartner && partner != nil {
		// The integrator uses this non-secret binding, never tool-name guesses.
		entry = append(entry, canonjson.Member{Key: "partner", Value: canonjson.Object{
			{Key: "platform", Value: partner.Platform}, {Key: "toolkit", Value: partner.Toolkit},
			{Key: "version", Value: partner.Version}, {Key: "binding", Value: partner.Binding},
			{Key: "toolMode", Value: partner.ToolMode},
		}})
	}
	if parts := p.Auth.CredentialParts; len(parts) > 0 {
		list := make([]any, 0, len(parts))
		for _, part := range parts {
			if part.Secret && part.Value != "" {
				return nil, fmt.Errorf("credential part %s is secret and carries a value: a secret is never catalog data", part.Name)
			}
			from := "customer"
			if part.Value != "" {
				from = "catalog"
			}
			list = append(list, canonjson.Object{
				{Key: "in", Value: part.In}, {Key: "name", Value: part.Name},
				{Key: "secret", Value: part.Secret}, {Key: "from", Value: from},
			})
		}
		entry = append(entry, canonjson.Member{Key: "credentialParts", Value: list})
	}
	if vars := p.Transport.URLVariables; len(vars) > 0 {
		entry = append(entry, canonjson.Member{Key: "urlVariables", Value: urlVariables(vars)})
	}
	if err := checkOrgServer(server); err != nil {
		return nil, err
	}
	return append(entry, canonjson.Member{Key: "server", Value: server}), nil
}

// checkOrgServer holds the server member to the gateway's own org server
// type: it decodes with no unknown member and carries no credential.
func checkOrgServer(server canonjson.Object) error {
	data, err := canonjson.Compact(server)
	if err != nil {
		return err
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	var cfg tenant.MCPServerOrgConfig
	if err := dec.Decode(&cfg); err != nil {
		return fmt.Errorf("the server entry is not the gateway's MCPServerOrgConfig: %w", err)
	}
	if cfg.Credential != nil || cfg.Auth != nil {
		return errors.New("the server entry carries a credential")
	}
	return nil
}

// gatewayTransport is the org config's transport: "sse" for a 2024-11-05
// upstream, else "http".
func gatewayTransport(p *manifest.Provider) string {
	if p.Transport.Variant == "sse" {
		return "sse"
	}
	return "http"
}

// credentialModes maps the provider onto the org config's credential modes
// (§2.4): agent is shared, user is per_user, a partner provider is
// vendor_end_user, and a server with no auth takes none.
func credentialModes(p *manifest.Provider) []string {
	switch {
	case p.Kind == manifest.KindPartner:
		return []string{"vendor_end_user"}
	case p.Auth.Kind == manifest.AuthNone:
		return []string{"none"}
	}
	var modes []string
	for _, m := range p.AccountModes {
		switch m {
		case "agent":
			modes = append(modes, "shared")
		case "user":
			modes = append(modes, "per_user")
		}
	}
	return modes
}

func urlVariables(vars map[string]manifest.URLVariable) canonjson.Object {
	names := make([]string, 0, len(vars))
	for name := range vars {
		names = append(names, name)
	}
	slices.Sort(names)
	out := canonjson.Object{}
	for _, name := range names {
		v := vars[name]
		decl := canonjson.Object{{Key: "description", Value: v.Description}, {Key: "pattern", Value: v.Pattern}}
		if v.Default != "" {
			decl = append(decl, canonjson.Member{Key: "default", Value: v.Default})
		}
		decl = append(decl, canonjson.Member{Key: "scope", Value: v.Scope})
		out = append(out, canonjson.Member{Key: name, Value: decl})
	}
	return out
}
