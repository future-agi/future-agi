package emit

import (
	"errors"
	"fmt"
	"regexp"
	"slices"
	"unicode/utf8"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// The official MCP Registry's server.json, schema 2025-12-11 (a copy read
// 2026-10-19: connectors/scaling-evidence/registry-docs/
// server.schema.2025-12-11.json, sha256 3fba0959...4114de0).
const (
	ServerJSONSchemaURL = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
	serverJSONMaxDesc   = 100 // ServerDetail.description maxLength
	serverJSONMaxTitle  = 100 // ServerDetail.title maxLength
	serverJSONMaxName   = 200 // ServerDetail.name maxLength
	serverJSONMaxVer    = 255 // ServerDetail.version maxLength
)

// RegistryNamespace is the namespace of the servers FutureAGI publishes:
// com.futureagi/<app-id>. The MCP Registry takes a domain namespace only as
// the reverse-DNS of a domain the publisher controls, and FutureAGI's is
// futureagi.com (AM-C21; io.futureagi would need futureagi.io). Publishing
// still needs the namespace proof first (OA-DIR).
const RegistryNamespace = "com.futureagi"

// ConnectorMetaKey is the _meta member M29's mirror reads (AM-C7), in the
// namespace FutureAGI publishes under (AM-C22, for consistency with AM-C21's
// RegistryNamespace). The gateway's own MCP _meta keys (agentctx, capability,
// the mcpmock) keep io.futureagi/; no code reads this one yet.
const ConnectorMetaKey = RegistryNamespace + "/connector"

var (
	serverJSONName = regexp.MustCompile(`^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$`)
	serverJSONURL  = regexp.MustCompile(`^https?://[^\s]+$`)
	templateVar    = regexp.MustCompile(`\{([A-Za-z_][A-Za-z0-9_]*)\}`)
)

// connectorsHostVar is the variable a rest provider's URL is served from.
const connectorsHostVar = "connectorsHost"

// RuntimeServesProtectedResourceMetadata says whether agentcc-connectors
// serves OAuth protected-resource metadata (RFC 9728) for the servers it
// runs: at a /.well-known/oauth-protected-resource path, or named by
// resource_metadata in its 401's WWW-Authenticate. It serves neither (its
// 401 says only Bearer realm="<app>"), so a registry MCP client cannot learn
// how to get the vendor token a rest provider needs, and emit/server-json
// leaves every rest provider out (IR-CN14c-6, IR-CN15c-5): nothing is
// published that an ordinary MCP client cannot sign in to. The runtime's
// TestServerJSONRuleMatchesTheRuntime (cmd/agentcc-connectors) fails when
// the runtime and this constant disagree.
const RuntimeServesProtectedResourceMetadata = false

// ReasonNoProtectedResourceMetadata names the rule that leaves a rest
// provider out of emit/server-json while the runtime serves no
// protected-resource metadata.
const ReasonNoProtectedResourceMetadata = "runtime_serves_no_protected_resource_metadata"

// ServerJSONLeftOutReasons explains each reason a server is left out of
// emit/server-json, for the catalog build's report.
var ServerJSONLeftOutReasons = map[string]string{
	ReasonNoProtectedResourceMetadata: "agentcc-connectors serves no OAuth protected-resource metadata (RFC 9728) " +
		"and its 401 names none, so an MCP registry client cannot learn how to get the vendor token this server " +
		"needs; it is published once the runtime serves that metadata (and OA-DIR's registration is done)",
}

// ServerJSONLeftOut is a server emit/server-json could describe but leaves
// out, and why.
type ServerJSONLeftOut struct {
	App    string // the app id, the file it would be (server-json/<app>.json)
	Name   string // its registry name, com.futureagi/<app>
	Ref    string // <app>/<provider>@<version>
	Reason string // a key of ServerJSONLeftOutReasons
}

// serverJSONLeftOutReason returns why emit/server-json leaves out a rest
// provider, or "" when it is written. runtimePRM is whether the runtime
// serves protected-resource metadata.
func serverJSONLeftOutReason(p *manifest.Provider, runtimePRM bool) string {
	if p.Kind == manifest.KindREST && !runtimePRM {
		return ReasonNoProtectedResourceMetadata
	}
	return ""
}

// ServerJSONLeftOutServers lists the servers emit/server-json leaves out, in
// app order, for the catalog build's report.
func ServerJSONLeftOutServers(cat *manifest.Catalog) []ServerJSONLeftOut {
	var out []ServerJSONLeftOut
	for _, a := range cat.Apps {
		for _, p := range a.Providers {
			if p.Kind != manifest.KindREST {
				continue
			}
			if reason := serverJSONLeftOutReason(p, RuntimeServesProtectedResourceMetadata); reason != "" {
				out = append(out, ServerJSONLeftOut{App: a.ID, Name: RegistryNamespace + "/" + a.ID, Ref: catalogRef(p), Reason: reason})
			}
		}
	}
	return out
}

// serverJSON writes server-json/<app-id>.json for each app with a rest
// provider: a server FutureAGI runs (agentcc-connectors, §2.6), and the
// only kind it publishes (§2.4). The remote's URL keeps {connectorsHost},
// declared as a required variable: the runtime is reached only through the
// gateway on hosted, or at an operator's own deployment. The runtime takes
// the vendor credential as its bearer (§2.6), so no header is declared.
// Every rest provider is checked, but one a registry client could not sign
// in to is not written (serverJSONLeftOutReason).
func serverJSON(cat *manifest.Catalog, meta Meta) (Files, error) {
	return serverJSONFiles(cat, meta, RuntimeServesProtectedResourceMetadata)
}

func serverJSONFiles(cat *manifest.Catalog, meta Meta, runtimePRM bool) (Files, error) {
	files := Files{}
	var errs []error
	for _, a := range cat.Apps {
		var rest []*manifest.Provider
		for _, p := range a.Providers {
			if p.Kind == manifest.KindREST {
				rest = append(rest, p)
			}
		}
		switch len(rest) {
		case 0:
			continue
		case 1:
		default:
			errs = append(errs, fmt.Errorf("app %s has %d rest providers; agentcc-connectors serves one per app, at /%s/mcp", a.ID, len(rest), a.ID))
			continue
		}
		doc, err := serverDetail(a, rest[0])
		if err != nil {
			errs = append(errs, fmt.Errorf("app %s: %w", a.ID, err))
			continue
		}
		if serverJSONLeftOutReason(rest[0], runtimePRM) != "" {
			continue
		}
		data, err := jsonFile(doc)
		if err != nil {
			errs = append(errs, err)
			continue
		}
		files["server-json/"+a.ID+".json"] = data
	}
	return files, errors.Join(errs...)
}

func serverDetail(a *manifest.App, p *manifest.Provider) (canonjson.Object, error) {
	name := RegistryNamespace + "/" + a.ID
	var errs []error
	fail := func(format string, args ...any) { errs = append(errs, fmt.Errorf(format, args...)) }
	if n := utf8.RuneCountInString(a.Description); n < 1 || n > serverJSONMaxDesc {
		fail("server.json allows a description of 1 to %d characters; app %s has %d", serverJSONMaxDesc, a.ID, n)
	}
	if n := utf8.RuneCountInString(a.Name); n < 1 || n > serverJSONMaxTitle {
		fail("server.json allows a title of 1 to %d characters; app %s's name has %d", serverJSONMaxTitle, a.ID, n)
	}
	if len(name) < 3 || len(name) > serverJSONMaxName || !serverJSONName.MatchString(name) {
		fail("name %q does not match server.json's name rule", name)
	}
	if len(p.Version) > serverJSONMaxVer {
		fail("version %q is longer than %d characters", p.Version, serverJSONMaxVer)
	}
	url := p.Transport.URL
	if !serverJSONURL.MatchString(url) {
		fail("remote url %q does not match server.json's URL rule", url)
	}
	variables, err := remoteVariables(url, p.Transport.URLVariables)
	if err != nil {
		errs = append(errs, err)
	}
	if err := errors.Join(errs...); err != nil {
		return nil, err
	}
	remoteType := "streamable-http"
	if p.Transport.Variant == "sse" {
		remoteType = "sse"
	}
	remote := canonjson.Object{{Key: "type", Value: remoteType}, {Key: "url", Value: url}}
	if len(variables) > 0 {
		remote = append(remote, canonjson.Member{Key: "variables", Value: variables})
	}
	return canonjson.Object{
		{Key: "$schema", Value: ServerJSONSchemaURL},
		{Key: "name", Value: name},
		{Key: "title", Value: a.Name},
		{Key: "description", Value: a.Description},
		{Key: "version", Value: p.Version},
		{Key: "remotes", Value: []any{remote}},
		{Key: "_meta", Value: canonjson.Object{{Key: ConnectorMetaKey, Value: canonjson.Object{
			{Key: "kind", Value: p.Kind}, {Key: "provider", Value: p.ID}, {Key: "tier", Value: p.Quality.Tier},
		}}}},
	}, nil
}

// remoteVariables declares every {variable} of the URL as a server.json
// Input: {connectorsHost}, and each URL variable the provider declares.
func remoteVariables(url string, declared map[string]manifest.URLVariable) (canonjson.Object, error) {
	var names []string
	for _, m := range templateVar.FindAllStringSubmatch(url, -1) {
		if !slices.Contains(names, m[1]) {
			names = append(names, m[1])
		}
	}
	slices.Sort(names)
	out := canonjson.Object{}
	for _, name := range names {
		if name == connectorsHostVar {
			out = append(out, canonjson.Member{Key: name, Value: canonjson.Object{
				{Key: "description", Value: "The host of an agentcc-connectors deployment, FutureAGI's connector runtime."},
				{Key: "isRequired", Value: true},
			}})
			continue
		}
		v, ok := declared[name]
		if !ok {
			return nil, fmt.Errorf("url variable {%s} is not declared", name)
		}
		in := canonjson.Object{{Key: "description", Value: v.Description}, {Key: "isRequired", Value: v.Default == ""}}
		if v.Default != "" {
			in = append(in, canonjson.Member{Key: "default", Value: v.Default})
		}
		out = append(out, canonjson.Member{Key: name, Value: in})
	}
	return out, nil
}
