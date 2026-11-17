package emit

import (
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// OSSRegistrySchema is oss-registry.json's schema identifier.
const OSSRegistrySchema = "connector-oss-registry/v1"

// OSSBuiltIn is one server OSS Omega's MCP_SERVER_CATALOG lists today
// (packages/core/src/mcp-registry.ts at 2afe4a0cc7), with the app and
// transport it belongs to and, for the five hosted ones, its URL. EE keys
// its old-plane OAuth descriptors on these ids and the Console hard-codes
// the hosted four (§2.4), so the registry never renames one.
type OSSBuiltIn struct{ ID, App, Transport, URL string }

// OSSBuiltIns are the 15 servers, in mcp-registry.ts's order.
var OSSBuiltIns = []OSSBuiltIn{
	{"filesystem", "filesystem", "stdio", ""},
	{"git", "git", "stdio", ""},
	{"memory", "memory", "stdio", ""},
	{"fetch", "fetch", "stdio", ""},
	{"time", "time", "stdio", ""},
	{"everything", "everything", "stdio", ""},
	{"github", "github", "stdio", ""},
	{"brave-search", "brave-search", "stdio", ""},
	{"playwright", "playwright", "stdio", ""},
	{"context7", "context7", "stdio", ""},
	{"github-remote", "github", "http", "https://api.githubcopilot.com/mcp/"},
	{"linear", "linear", "http", "https://mcp.linear.app/mcp"},
	{"notion", "notion", "http", "https://mcp.notion.com/mcp"},
	{"sentry-remote", "sentry", "http", "https://mcp.sentry.dev/mcp"},
	{"stripe", "stripe", "http", "https://mcp.stripe.com"},
}

// ossID is an OSS server id: the appId rule, at most 40 characters (§2.4).
var ossID = regexp.MustCompile(`^[a-z][a-z0-9-]{1,39}$`)

// ossRegistry writes one McpCatalogServer (mcp-registry.ts:38-74) per
// server OSS lists (manifest.OSSListed), keys in its order: id, name,
// description, transport, command and args (stdio) or url (http), trusted,
// auth (http only: oauth, api_key or none), hosted, and tags, the app's tags
// and categories together. Every OSS-eligible server is checked, the ones
// left out (OSSLeftOutServers) included, so a left-out server keeps its id.
func ossRegistry(cat *manifest.Catalog, meta Meta) (Files, error) {
	servers := []any{}
	var errs []error
	emitted := map[string]string{} // id -> app, left-out servers included
	for _, a := range cat.Apps {
		for _, s := range manifest.EligibleForOSS(a) {
			if err := checkOSSServer(a, s, emitted); err != nil {
				errs = append(errs, err)
				continue
			}
			if manifest.OSSLeftOutReason(s.Provider) != "" {
				continue
			}
			servers = append(servers, ossServer(a, s))
		}
	}
	if err := errors.Join(errs...); err != nil {
		return nil, err
	}
	doc := append(envelope(OSSRegistrySchema, meta), canonjson.Member{Key: "servers", Value: servers})
	data, err := jsonFile(doc)
	if err != nil {
		return nil, err
	}
	return Files{"oss-registry.json": data}, nil
}

// OSSLeftOut is a server OSS could receive that oss-registry.json leaves
// out, and why (manifest.OSSLeftOutReason).
type OSSLeftOut struct {
	ID           string   // its OSS id, which no other server may take
	Ref          string   // <app>/<provider>@<version>
	Registration []string // auth.oauth2.registration, as the record states it
	PublicClient string   // what the record says of a public client (manifest.PublicClientState)
	Reason       string   // a key of manifest.OSSLeftOutReasons
}

// OSSLeftOutServers lists the servers oss-registry.json leaves out, in the
// registry's order, for the catalog build's report.
func OSSLeftOutServers(cat *manifest.Catalog) []OSSLeftOut {
	var out []OSSLeftOut
	for _, a := range cat.Apps {
		for _, s := range manifest.EligibleForOSS(a) {
			reason := manifest.OSSLeftOutReason(s.Provider)
			if reason == "" {
				continue
			}
			var reg []string
			if o := s.Provider.Auth.OAuth2; o != nil {
				reg = slices.Clone(o.Registration)
			}
			out = append(out, OSSLeftOut{ID: s.ID, Ref: catalogRef(s.Provider), Registration: reg,
				PublicClient: manifest.PublicClientState(s.Provider), Reason: reason})
		}
	}
	return out
}

func ossServer(a *manifest.App, s manifest.OSSServer) canonjson.Object {
	p := s.Provider
	srv := canonjson.Object{
		{Key: "id", Value: s.ID},
		{Key: "name", Value: a.Name},
		{Key: "description", Value: a.Description},
		{Key: "transport", Value: p.Transport.Kind},
	}
	if p.Transport.Kind == "stdio" {
		srv = append(srv, canonjson.Member{Key: "command", Value: p.Transport.Command})
		srv = append(srv, canonjson.Member{Key: "args", Value: stringList(orNone(p.Transport.Args))})
	} else {
		srv = append(srv, canonjson.Member{Key: "url", Value: p.Transport.URL})
	}
	srv = append(srv, canonjson.Member{Key: "trusted", Value: p.Trusted})
	if p.Transport.Kind == "http" {
		auth, _ := ossAuth(p.Auth.Kind)
		srv = append(srv, canonjson.Member{Key: "auth", Value: auth})
	}
	srv = append(srv, canonjson.Member{Key: "hosted", Value: p.Hosted})
	tags := slices.Concat(a.Tags, a.Categories)
	slices.Sort(tags)
	return append(srv, canonjson.Member{Key: "tags", Value: stringList(slices.Compact(tags))})
}

func orNone(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}

// ossAuth maps the auth kind onto McpCatalogServer.auth (§2.4); ok is
// false for a kind OSS has no word for.
func ossAuth(kind string) (string, bool) {
	switch kind {
	case manifest.AuthOAuth2, manifest.AuthMCPOAuth:
		return "oauth", true
	case manifest.AuthAPIKey, manifest.AuthBasic, manifest.AuthServiceAccount:
		return "api_key", true
	case manifest.AuthNone:
		return "none", true
	}
	return "", false
}

// checkOSSServer keeps each id valid, unique and unrenamed: a built-in id
// stays with its app and transport, and a built-in hosted URL with its id.
func checkOSSServer(a *manifest.App, s manifest.OSSServer, emitted map[string]string) error {
	p := s.Provider
	where := fmt.Sprintf("%s/%s", a.ID, p.ID)
	if !ossID.MatchString(s.ID) {
		return fmt.Errorf("%s: OSS id %q is not a valid server id of at most 40 characters", where, s.ID)
	}
	if other, dup := emitted[s.ID]; dup {
		return fmt.Errorf("%s: OSS id %s is also emitted for app %s", where, s.ID, other)
	}
	emitted[s.ID] = a.ID
	if p.Transport.Kind == "http" && strings.Contains(p.Transport.URL, "{") {
		return fmt.Errorf("%s: OSS needs a fixed URL, and %s has variables", where, p.Transport.URL)
	}
	if p.Kind != manifest.KindVendorMCP && p.Kind != manifest.KindVendorLocal {
		return fmt.Errorf("%s: OSS takes vendor_mcp and vendor_local providers only, not %s", where, p.Kind)
	}
	if _, ok := ossAuth(p.Auth.Kind); p.Transport.Kind == "http" && !ok {
		return fmt.Errorf("%s: OSS describes an http server's auth as oauth, api_key or none, and has none for %s: set x-omega.oss: exclude", where, p.Auth.Kind)
	}
	for _, b := range OSSBuiltIns {
		if s.ID == b.ID && (a.ID != b.App || p.Transport.Kind != b.Transport) {
			return fmt.Errorf("%s: OSS id %s is OSS's %s server for app %s; it would be renamed", where, b.ID, b.Transport, b.App)
		}
		if b.URL != "" && p.Transport.URL == b.URL && s.ID != b.ID {
			return fmt.Errorf("%s: OSS reaches %s as %s, not %s; set x-omega.ossId: %s", where, b.URL, b.ID, s.ID, b.ID)
		}
	}
	return nil
}
