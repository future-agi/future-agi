package manifest

import (
	"fmt"
	"regexp"
	"slices"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// kindRule is what each provider kind fixes (the schema's allOf).
type kindRule struct {
	transport  string
	publishers []string
	tools      string
	auth       []string // nil: any but partner
}

var kindRules = map[string]kindRule{
	KindVendorMCP:   {"http", []string{"vendor", "community"}, ToolsUpstreamMCP, nil},
	KindREST:        {"http", []string{"futureagi"}, ToolsOperations, nil},
	KindPartner:     {"http", []string{"partner"}, ToolsPartner, []string{AuthPartner}},
	KindVendorLocal: {"stdio", []string{"vendor", "community"}, ToolsUpstreamMCP, []string{AuthLocalSetup, AuthNone}},
}

// credentialHeaders carry a credential, so a provider never forwards them
// from the caller. The gateway refuses more (config.ForwardHeaderRefused);
// checkProvider applies both.
var credentialHeaders = []string{"authorization", "proxy-authorization", "cookie", "x-api-key", "api-key"}

// pricePattern finds a price figure; a manifest records the pricing model
// only, and prices live in the gateway-only overlay.
var pricePattern = regexp.MustCompile(`(?i)([$€£¥]\s*[0-9])|([0-9][0-9.,]*\s*(usd|eur|gbp|cents?|dollars?)\b)`)

// checkProvider holds a provider's rules that need no other record.
func checkProvider(p *Provider) []error {
	var errs []error
	fail := func(format string, args ...any) { errs = append(errs, fmt.Errorf(format, args...)) }

	rule := kindRules[p.Kind]
	if p.Transport.Kind != rule.transport {
		fail("/transport/kind: a %s provider uses %s", p.Kind, rule.transport)
	}
	if !slices.Contains(rule.publishers, p.Publisher) {
		fail("/publisher: a %s provider is published by %s", p.Kind, strings.Join(rule.publishers, " or "))
	}
	if p.Tools.Source != rule.tools {
		fail("/tools/source: a %s provider's tools come from %s", p.Kind, rule.tools)
	}
	switch {
	case rule.auth != nil && !slices.Contains(rule.auth, p.Auth.Kind):
		fail("/auth/kind: a %s provider authenticates with %s", p.Kind, strings.Join(rule.auth, " or "))
	case rule.auth == nil && p.Auth.Kind == AuthPartner:
		fail("/auth/kind: only a partner provider authenticates with partner")
	}
	if p.Kind == KindREST && (p.Upstream == nil || p.Upstream.Spec == nil) {
		fail("/upstream/spec: is required for a rest provider")
	}

	isHTTP := p.Transport.Kind == "http"
	if isHTTP != p.Hosted {
		if isHTTP {
			fail("/hosted: an http provider is hosted")
		} else {
			fail("/hosted: a stdio provider is not hosted")
		}
	}
	if p.Trusted && (p.Quality.Review == nil || p.Quality.Tier == "listed") {
		fail("/trusted: true only after a maintainer review, at tier verified or optimized")
	}
	if p.VendorStatus.Label != "unknown" && p.VendorStatus.Source == "" {
		fail("/vendorStatus/source: is required when the label is %s", p.VendorStatus.Label)
	}
	errs = append(errs, checkLifecycle(p.Lifecycle.Status, p.Lifecycle.SunsetAt)...)
	if p.Lifecycle.ReplacedBy == p.ID {
		fail("lifecycle.replacedBy names the provider itself")
	}
	errs = append(errs, checkTransport(p)...)
	errs = append(errs, checkAuthBlocks(p)...)
	errs = append(errs, checkCredentialParts(p)...)
	errs = append(errs, checkOAuthVariables(p)...)

	if p.Tools.Generated != "" && p.Tools.Source != ToolsOperations {
		fail("/tools/generated: only an operations source has generated tools")
	}
	if x := p.XGateway; x != nil {
		for _, h := range x.ForwardHeaders {
			switch {
			case slices.Contains(credentialHeaders, strings.ToLower(h)) || isCredentialPartHeader(p, h):
				fail("/x-gateway/forwardHeaders: %s carries a credential and is never forwarded", h)
			case config.ForwardHeaderRefused(h):
				fail("/x-gateway/forwardHeaders: the gateway never forwards %s (x-agentcc-*, x-omega-* and its own MCP headers), so its org-config check would refuse the server", h)
			}
		}
	}
	if pricePattern.MatchString(p.Pricing.Notes) {
		fail("/pricing/notes: holds a price; prices live in the gateway-only overlay")
	}
	return errs
}

func checkTransport(p *Provider) []error {
	var errs []error
	fail := func(format string, args ...any) { errs = append(errs, fmt.Errorf(format, args...)) }
	t := p.Transport
	if t.Kind == "http" {
		if t.URL == "" {
			fail("/transport/url: is required for http")
		}
		for _, f := range []struct {
			name string
			set  bool
		}{{"command", t.Command != ""}, {"args", t.Args != nil}, {"env", t.Env != nil}, {"package", t.Package != nil}} {
			if f.set {
				fail("/transport/%s: only a stdio transport has a %s", f.name, f.name)
			}
		}
	} else {
		if t.Command == "" {
			fail("/transport/command: is required for stdio")
		}
		for _, f := range []struct {
			name string
			set  bool
		}{{"url", t.URL != ""}, {"urlVariables", t.URLVariables != nil}, {"variant", t.Variant != ""}} {
			if f.set {
				fail("/transport/%s: only an http transport has a %s", f.name, f.name)
			}
		}
	}
	if t.URL == "" {
		return errs
	}

	declared := map[string]string{} // variable -> where it is declared
	for _, name := range sortedNames(t.URLVariables) {
		declared[name] = "transport.urlVariables"
	}
	for _, part := range p.Auth.CredentialParts {
		if part.In != "url_variable" {
			continue
		}
		if where, dup := declared[part.Name]; dup {
			fail("{%s} is declared twice: in %s and as a url_variable credential part", part.Name, where)
		}
		declared[part.Name] = "auth.credentialParts"
	}
	used := templateVars(t.URL)
	for _, v := range used {
		switch {
		case v == connectorsHost && p.Kind != KindREST:
			fail("transport.url uses {connectorsHost}, which only a rest provider may use")
		case v != connectorsHost && declared[v] == "":
			fail("transport.url uses {%s}, which is not declared", v)
		}
	}
	for _, name := range sortedNames(t.URLVariables) {
		if !slices.Contains(used, name) {
			fail("transport.urlVariables declares %s, which transport.url does not use", name)
		}
	}
	if p.Kind == KindREST {
		if want := "https://{connectorsHost}/" + p.App + "/mcp"; t.URL != want {
			fail("a rest provider's transport.url is %s", want)
		}
	}
	errs = append(errs, checkVariableDefaults("/transport/urlVariables", t.URLVariables)...)
	if v := p.Auth.Verify; v != nil && v.HTTP != nil {
		for _, name := range templateVars(v.HTTP.URL) {
			if declared[name] == "" {
				fail("auth.verify.http.url uses {%s}, which is not declared", name)
			}
		}
	}
	return errs
}

func checkVariableDefaults(path string, vars map[string]URLVariable) []error {
	var errs []error
	for _, name := range sortedNames(vars) {
		v := vars[name]
		re, err := regexp.Compile(v.Pattern)
		if err == nil && v.Default != "" && !re.MatchString(v.Default) {
			errs = append(errs, fmt.Errorf("%s/%s/default: default %s does not match its pattern", path, name, v.Default))
		}
	}
	return errs
}

func checkAuthBlocks(p *Provider) []error {
	var errs []error
	a := p.Auth
	for _, b := range []struct {
		kind, key string
		set       bool
		wrong     string
	}{
		{AuthOAuth2, "oauth2", a.OAuth2 != nil, "only an oauth2 provider has an oauth2 block"},
		{AuthAPIKey, "credentialParts", a.CredentialParts != nil, "only an api_key credential has parts"},
		{AuthPartner, "partner", a.Partner != nil, "only a partner provider has a partner block"},
	} {
		switch {
		case a.Kind == b.kind && !b.set:
			errs = append(errs, fmt.Errorf("/auth/%s: is required when auth.kind is %s", b.key, b.kind))
		case a.Kind != b.kind && b.set:
			errs = append(errs, fmt.Errorf("/auth/%s: %s", b.key, b.wrong))
		}
	}
	if a.ClientApproval != nil && a.Kind != AuthOAuth2 && a.Kind != AuthMCPOAuth {
		errs = append(errs, fmt.Errorf("/auth/clientApproval: only an OAuth provider needs client approval"))
	}
	if v := a.Verify; v != nil && (v.Tool == "") == (v.HTTP == nil) {
		errs = append(errs, fmt.Errorf("/auth/verify: names exactly one of a tool or an http check"))
	}
	return errs
}

// checkCredentialParts holds the credential-part rules: each part says where it goes, a
// secret part never has a value, and an api_key credential has a secret.
func checkCredentialParts(p *Provider) []error {
	var errs []error
	fail := func(format string, args ...any) { errs = append(errs, fmt.Errorf(format, args...)) }
	parts := p.Auth.CredentialParts
	seen := map[string]bool{}
	secret := false
	for i, part := range parts {
		at := fmt.Sprintf("/auth/credentialParts/%d", i)
		secret = secret || part.Secret
		if part.Secret && part.Value != "" {
			fail("%s/value: a secret part never carries a value", at)
		}
		if part.Prefix != "" && part.In != "header" {
			fail("%s/prefix: only a header part has a prefix", at)
		}
		name := part.Name
		switch part.In {
		case "header":
			if !stringDefs["headerName"].pattern.MatchString(name) {
				fail("%s/name: %q is not a header name", at, name)
			} else if err := checkHeaderName(name); err != nil {
				fail("%s/name: %v", at, err)
			}
			name = strings.ToLower(name)
		case "query":
			if !stringDefs["queryName"].pattern.MatchString(name) {
				fail("%s/name: %q is not a query parameter name", at, name)
			}
		case "url_variable":
			if !stringDefs["varName"].pattern.MatchString(name) {
				fail("%s/name: %q is not a variable name", at, name)
			} else if !slices.Contains(templateVars(p.Transport.URL), name) {
				fail("credential part %s is a url_variable but transport.url has no {%s}", name, name)
			}
		}
		key := part.In + " " + name
		if seen[key] {
			fail("credential part %s appears twice", key)
		}
		seen[key] = true
	}
	if len(parts) > 0 && !secret {
		fail("an api_key credential needs at least one secret part")
	}
	return errs
}

func isCredentialPartHeader(p *Provider, header string) bool {
	for _, part := range p.Auth.CredentialParts {
		if part.In == "header" && strings.EqualFold(part.Name, header) {
			return true
		}
	}
	return false
}

// checkOAuthVariables requires each variable an OAuth URL uses to be
// declared in auth.oauth2.urlVariables, and each declared one to be used.
// A variable declared there and in transport.urlVariables is one value, so
// both declarations agree. A variable in the host of tokenUrl or
// revocation.url chooses where the client secret is sent, and a client is
// shared by an org's users (Django sends an org's client for the server
// whatever registration says), so a user never fills one.
func checkOAuthVariables(p *Provider) []error {
	o := p.Auth.OAuth2
	if o == nil {
		return nil
	}
	var errs []error
	urls := []struct{ field, url string }{{"authorizeUrl", o.AuthorizeURL}, {"tokenUrl", o.TokenURL}}
	if o.Revocation != nil {
		urls = append(urls, struct{ field, url string }{"revocation.url", o.Revocation.URL})
	}
	used := map[string]bool{}
	for _, u := range urls {
		for _, v := range templateVars(u.url) {
			used[v] = true
			if _, ok := o.URLVariables[v]; !ok {
				errs = append(errs, fmt.Errorf("auth.oauth2.%s uses {%s}, which is not declared", u.field, v))
			}
		}
		if u.field == "authorizeUrl" {
			continue // the authorize URL carries the client id only
		}
		for _, v := range templateVars(templateHost(u.url)) {
			if o.URLVariables[v].Scope == "user" {
				errs = append(errs, fmt.Errorf("auth.oauth2.%s: {%s} is in the host the client secret is sent to, so it is filled per org (scope: org), never per user", u.field, v))
			}
		}
	}
	for _, name := range sortedNames(o.URLVariables) {
		if !used[name] {
			errs = append(errs, fmt.Errorf("auth.oauth2.urlVariables declares %s, which no OAuth URL uses", name))
		}
		if tv, ok := p.Transport.URLVariables[name]; ok {
			if ov := o.URLVariables[name]; tv.Pattern != ov.Pattern || tv.Scope != ov.Scope || tv.Default != ov.Default {
				errs = append(errs, fmt.Errorf("auth.oauth2.urlVariables.%s and transport.urlVariables.%s are one value, so their pattern, scope and default agree", name, name))
			}
		}
	}
	return append(errs, checkVariableDefaults("/auth/oauth2/urlVariables", o.URLVariables)...)
}
