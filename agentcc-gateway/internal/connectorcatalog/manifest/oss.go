package manifest

import (
	"fmt"
	"slices"
)

// ossBuiltIns are the servers OSS Omega's MCP_SERVER_CATALOG lists
// (OSS:packages/core/src/mcp-registry.ts at 2afe4a0cc7), each with the app
// and transport it belongs to, and the URL of the hosted ones. EE keys its
// old-plane OAuth descriptors and the Console its hosted connectors on these
// ids, so the catalog never renames one (§2.4).
var ossBuiltIns = []struct{ ID, App, Transport, URL string }{
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

// ossMaxID is the longest OSS server id §2.4 allows.
const ossMaxID = 40

// checkOSS keeps OSS server ids valid, unique and unrenamed.
func checkOSS(cat *Catalog) []error {
	var errs []error
	fail := func(path, format string, args ...any) {
		errs = append(errs, fmt.Errorf("%s: OSS: %s", path, fmt.Sprintf(format, args...)))
	}
	emittedBy := map[string]*Provider{}
	for _, a := range cat.Apps {
		servers := EligibleForOSS(a)
		for _, p := range a.Providers {
			if p.XOmega != nil && p.XOmega.OSSID != "" && !slices.ContainsFunc(servers, func(s OSSServer) bool { return s.Provider == p }) {
				fail(p.path, "x-omega.ossId is set but OSS never receives this provider")
			}
		}
		for _, s := range servers {
			p := s.Provider
			switch {
			case len(s.ID) > ossMaxID:
				fail(p.path, "OSS id %s is longer than %d characters; set x-omega.ossId", s.ID, ossMaxID)
			case !stringDefs["appId"].pattern.MatchString(s.ID):
				fail(p.path, "OSS id %s does not match appId; set x-omega.ossId", s.ID)
			}
			if first, dup := emittedBy[s.ID]; dup {
				fail(p.path, "OSS id %s is emitted twice: by %s and here", s.ID, first.path)
			}
			emittedBy[s.ID] = p
			if p.Transport.Kind == "http" && len(templateVars(p.Transport.URL)) > 0 {
				fail(p.path, "OSS needs a fixed URL; its url has variables; set x-omega.oss: exclude")
			}
			for _, b := range ossBuiltIns {
				if s.ID == b.ID && (a.ID != b.App || p.Transport.Kind != b.Transport) {
					fail(p.path, "OSS id %s belongs to OSS's %s server for app %s", b.ID, b.Transport, b.App)
				}
				if b.URL != "" && p.Transport.URL == b.URL && s.ID != b.ID {
					fail(p.path, "OSS calls %s %s; set x-omega.ossId: %s", b.URL, b.ID, b.ID)
				}
			}
		}
		for _, b := range ossBuiltIns {
			if b.App != a.ID {
				continue
			}
			kept, present := false, false
			for _, s := range servers {
				if s.Provider.Transport.Kind == b.Transport {
					present = true
					kept = kept || s.ID == b.ID
				}
			}
			if present && !kept {
				fail(a.path, "OSS's %s server for app %s is %s; no provider keeps that id", b.Transport, b.App, b.ID)
			}
		}
	}
	return errs
}
