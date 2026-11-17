package manifest

import (
	"fmt"
	"net/url"
	"slices"
	"strings"
)

// OSS Omega's `omega mcp login` (OSS:packages/cli/src/chat/mcp-oauth.ts)
// signs in with an authorize URL, a token URL and a client id a person types
// in, a PKCE verifier, a loopback redirect, and no client secret: it neither
// discovers the server's metadata nor registers a client. So it can sign in
// to an OAuth server only where the vendor lets a person register a public
// client beforehand (or publishes one). x-omega.publicClient states whether
// that is so, citing the vendor's pages (CN24pre-b); OSS's
// registry lists an OAuth server only when it says so.

// ReasonOAuthPublicClientNotProven names the rule that leaves a server out
// of OSS Omega's registry when its OAuth sign-in has no public client its
// record proves.
const ReasonOAuthPublicClientNotProven = "oauth_public_client_not_proven"

// What a provider's record says of a public client, as the build reports it.
const (
	PublicClientYes       = "yes"
	PublicClientNo        = "no"
	PublicClientNotStated = "not stated"
)

// PublicClientState is what the provider's record says of a public client:
// PublicClientYes, PublicClientNo or PublicClientNotStated.
func PublicClientState(p *Provider) string {
	switch {
	case p.XOmega == nil || p.XOmega.PublicClient == nil:
		return PublicClientNotStated
	case p.XOmega.PublicClient.Supported:
		return PublicClientYes
	}
	return PublicClientNo
}

// signsInWithOAuth reports whether the provider's credential comes from an
// OAuth sign-in: a declared oauth2 spec, or the server's own MCP OAuth.
func signsInWithOAuth(p *Provider) bool {
	return p.Auth.Kind == AuthOAuth2 || p.Auth.Kind == AuthMCPOAuth
}

// provenPublicClient reports whether the record proves a public client.
func provenPublicClient(p *Provider) bool {
	return PublicClientState(p) == PublicClientYes
}

// checkPublicClient is L0's rule for x-omega.publicClient: only an OAuth
// provider has one; one stated as supported is a sign-in omega mcp login can
// be given (an oauth2 spec with PKCE that takes a pre-registered client),
// proven by the vendor's own pages. A false value may cite any page: it
// lists nothing.
func checkPublicClient(a *App, p *Provider) []error {
	if p.XOmega == nil || p.XOmega.PublicClient == nil {
		return nil
	}
	const at = "/x-omega/publicClient"
	pc := p.XOmega.PublicClient
	if !signsInWithOAuth(p) {
		return []error{fmt.Errorf("%s: only an OAuth provider (auth.kind oauth2 or mcp_oauth) has a public client; this one is %s", at, p.Auth.Kind)}
	}
	if !pc.Supported {
		return nil
	}
	var errs []error
	switch o := p.Auth.OAuth2; {
	case p.Auth.Kind == AuthMCPOAuth:
		errs = append(errs, fmt.Errorf("%s/supported: omega mcp login needs the authorize and token URLs typed in, and an mcp_oauth record declares none; declare auth.kind oauth2 with them, or state supported: false", at))
	case o == nil:
		errs = append(errs, fmt.Errorf("%s/supported: an oauth2 provider declares its oauth2 spec", at))
	default:
		if !o.PKCE {
			errs = append(errs, fmt.Errorf("%s/supported: omega mcp login always sends PKCE (S256), and auth.oauth2.pkce is false", at))
		}
		if !slices.Contains(o.Registration, registrationPreregistered) {
			errs = append(errs, fmt.Errorf("%s/supported: a public client is registered beforehand, and auth.oauth2.registration names no preregistered client", at))
		}
	}
	for i, e := range pc.Evidence {
		if !VendorOwned(a, e.URL) {
			errs = append(errs, fmt.Errorf("%s/evidence/%d/url: %s is not the vendor's (%s): a public client is proven only by the vendor's own page",
				at, i, pageOwner(e.URL), strings.Join(vendorPlaces(a), ", ")))
		}
	}
	return errs
}

// VendorOwned reports whether the page is the app vendor's own: its host is
// one of the app's vendor.domains or under one, or it is on github.com
// under one of the app's vendor.githubOrgs (matched case-insensitively).
func VendorOwned(a *App, raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" {
		return false
	}
	host := strings.ToLower(u.Hostname())
	for _, d := range a.Vendor.Domains {
		d = strings.ToLower(d)
		if host == d || strings.HasSuffix(host, "."+d) {
			return true
		}
	}
	if host != "github.com" {
		return false
	}
	org, _, _ := strings.Cut(strings.TrimPrefix(u.Path, "/"), "/")
	return org != "" && slices.ContainsFunc(a.Vendor.GitHubOrgs, func(o string) bool { return strings.EqualFold(o, org) })
}

// pageOwner names who a page belongs to for a refusal: its host, or
// github.com/<org> on GitHub.
func pageOwner(raw string) string {
	u, err := url.Parse(raw)
	if err != nil {
		return raw
	}
	host := strings.ToLower(u.Hostname())
	if org, _, _ := strings.Cut(strings.TrimPrefix(u.Path, "/"), "/"); host == "github.com" && org != "" {
		return host + "/" + org
	}
	return host
}

// vendorPlaces lists where the vendor's own pages are.
func vendorPlaces(a *App) []string {
	out := slices.Clone(a.Vendor.Domains)
	for _, o := range a.Vendor.GitHubOrgs {
		out = append(out, "github.com/"+o)
	}
	return out
}

// diffPublicClient measures a public-client change on a provider whose app
// is not known: one no longer proven may leave OSS's registry (major), one
// newly proven may join it (minor).
func diffPublicClient(ch *Change, o, n *Provider) {
	switch was, is := provenPublicClient(o), provenPublicClient(n); {
	case was && !is:
		ch.need(BumpMajor, "x-omega.publicClient: OSS may no longer list the server (%s)", ReasonOAuthPublicClientNotProven)
	case !was && is:
		ch.need(BumpMinor, "x-omega.publicClient: OSS may list the server")
	}
}
