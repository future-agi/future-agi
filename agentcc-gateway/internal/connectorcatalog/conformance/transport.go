package conformance

import (
	"fmt"
	"net/url"
	"regexp"
	"slices"
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// placeholderValue fills a URL variable whose value L3 cannot place (a
// secret credential part), so the URL still has a concrete path.
const placeholderValue = "cn8-placeholder"

// sseEra is the only era an HTTP+SSE (variant sse) upstream speaks.
const sseEra = "2024-11-05"

var urlVariableRE = regexp.MustCompile(`\{([A-Za-z_][A-Za-z0-9_]{0,31})\}`)

// endpoint is where the harness points the gateway for an http provider: the
// provider's URL with its host replaced by the mock's and its variables
// filled.
type endpoint struct {
	// Rest is the rendered path and query, appended to the mock's base URL;
	// empty when the URL has neither.
	Rest string
	// Path is the path the gateway must post to, and the mock's --path.
	Path string
	// OrgVariables is the url_variables record: the org-scoped transport
	// variables and the values add-from-catalog would fill in.
	OrgVariables map[string]string
	// Skips are rows for variables L3 can render but not resolve as
	// production would.
	Skips []Row
}

// renderEndpoint renders an http provider's URL. A declared variable takes
// its default, else a short value its pattern matches; a credential part
// that fills a URL variable gets a placeholder (its own row says why); the
// host, {connectorsHost} included, is the mock's.
func renderEndpoint(p *manifest.Provider) (endpoint, error) {
	ep := endpoint{OrgVariables: map[string]string{}}
	tmpl := p.Transport.URL
	if p.Transport.Variant == "sse" {
		ep.Rest, ep.Path = "/sse", "/sse"
		ep.Skips = append(ep.Skips, Row{ID: "transport.path", Skip: waitSSEPath})
		return ep, nil
	}
	rest, ok := strings.CutPrefix(tmpl, "https://")
	if !ok {
		return ep, fmt.Errorf("transport.url %q is not an https URL", tmpl)
	}
	if i := strings.IndexAny(rest, "/?"); i >= 0 {
		rest = rest[i:]
	} else {
		rest = ""
	}
	values := map[string]string{}
	for _, name := range sortedKeys(p.Transport.URLVariables) {
		v := p.Transport.URLVariables[name]
		value := v.Default
		if value == "" {
			var err error
			if value, err = exampleMatching(v.Pattern); err != nil {
				return ep, fmt.Errorf("transport.urlVariables.%s: %w", name, err)
			}
		}
		values[name] = value
		if v.Scope == "user" {
			ep.Skips = append(ep.Skips, Row{ID: "url.user." + name, Skip: waitUserVariable(name)})
		} else {
			ep.OrgVariables[name] = value
		}
	}
	for _, part := range p.Auth.CredentialParts {
		if part.In == "url_variable" {
			values[part.Name] = placeholderValue
		}
	}
	var missing []string
	fill := func(part string, escape func(string) string) string {
		return urlVariableRE.ReplaceAllStringFunc(part, func(m string) string {
			value, ok := values[m[1:len(m)-1]]
			if !ok {
				missing = append(missing, m)
				return m
			}
			return escape(value)
		})
	}
	path, query, hasQuery := strings.Cut(rest, "?")
	ep.Path = fill(path, url.PathEscape)
	ep.Rest = ep.Path
	if hasQuery {
		ep.Rest += "?" + fill(query, url.QueryEscape)
	}
	if len(missing) > 0 {
		return ep, fmt.Errorf("transport.url uses %s outside the host, which no urlVariables entry or credential part declares",
			strings.Join(missing, ", "))
	}
	if ep.Path == "" {
		ep.Path = "/"
	}
	return ep, nil
}

// upstreamEra is the era the mock speaks, which every message the gateway
// sends it must carry: the provider's pinned era, else its cassette's, else
// its tools snapshot's. "" means none is pinned, and the mock speaks every
// era (dual).
func upstreamEra(p *manifest.Provider, recorded string) (string, error) {
	era := ""
	switch {
	case p.Transport.Era != "" && p.Transport.Era != "auto":
		era = p.Transport.Era
	case recorded != "":
		era = recorded
	case p.Tools.Snapshot != nil:
		era = p.Tools.Snapshot.Era
	}
	if p.Transport.Variant == "sse" {
		if era != "" && era != sseEra {
			return "", fmt.Errorf("variant sse speaks %s, but the provider pins %s", sseEra, era)
		}
		return sseEra, nil
	}
	if era != "" && !slices.Contains(config.MCPProtocolVersions(), era) {
		return "", fmt.Errorf("era %s is not one the gateway serves (%s)", era, strings.Join(config.MCPProtocolVersions(), ", "))
	}
	return era, nil
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
