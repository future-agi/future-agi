package manifest

import "slices"

// ProviderOrder returns the app's providers in emission order:
// defaultProvider first, then the rest by id.
func (a *App) ProviderOrder() []*Provider {
	out := make([]*Provider, 0, len(a.Providers))
	if d := a.Provider(a.DefaultProvider); d != nil {
		out = append(out, d)
	}
	for _, p := range a.Providers {
		if p.ID != a.DefaultProvider {
			out = append(out, p)
		}
	}
	return out
}

func excluded(flag string) bool { return flag == "exclude" }

// EligibleForEE returns the provider EE's emitter takes for the app:
// the first in ProviderOrder that authenticates with oauth2 (EE takes OAuth
// only, EE:apps.ts:211-212), uses http, is a vendor_mcp or rest provider,
// and is not excluded from EE. An app excluded from EE has none.
func EligibleForEE(app *App) (*Provider, bool) {
	if app.XOmega != nil && excluded(app.XOmega.EE) {
		return nil, false
	}
	for _, p := range app.ProviderOrder() {
		if p.Auth.Kind == AuthOAuth2 && p.Auth.OAuth2 != nil && p.Transport.Kind == "http" &&
			(p.Kind == KindVendorMCP || p.Kind == KindREST) &&
			(p.XOmega == nil || !excluded(p.XOmega.EE)) {
			return p, true
		}
	}
	return nil, false
}

// OSSServer is one entry the OSS emitter writes to OSS Omega's MCP server
// registry (OSS:packages/core/src/mcp-registry.ts McpCatalogServer).
type OSSServer struct {
	ID       string
	Provider *Provider
}

// EligibleForOSS returns the app's OSS servers in ProviderOrder: one
// per vendor_mcp provider over http or vendor_local provider over stdio
// that is not excluded from OSS. Its id is x-omega.ossId when set;
// otherwise the app id for the first and <app>-<provider> for the rest. An
// app excluded from OSS has none.
func EligibleForOSS(app *App) []OSSServer {
	if app.XOmega != nil && excluded(app.XOmega.OSS) {
		return nil
	}
	var out []OSSServer
	for _, p := range app.ProviderOrder() {
		if !ossKind(p) || (p.XOmega != nil && excluded(p.XOmega.OSS)) {
			continue
		}
		id := app.ID
		switch {
		case p.XOmega != nil && p.XOmega.OSSID != "":
			id = p.XOmega.OSSID
		case len(out) > 0:
			id = app.ID + "-" + p.ID
		}
		out = append(out, OSSServer{ID: id, Provider: p})
	}
	return out
}

func ossKind(p *Provider) bool {
	return (p.Kind == KindVendorMCP && p.Transport.Kind == "http") || (p.Kind == KindVendorLocal && p.Transport.Kind == "stdio")
}

// ReasonOAuthSelfRegistrationOnly names the rule that leaves a server out of
// OSS Omega's registry when its OAuth sign-in takes only clients that
// register themselves. OSS's `omega mcp login`
// (OSS:packages/cli/src/chat/mcp-oauth.ts) signs in with a client id the
// user registered beforehand; it neither registers a client (DCR) nor
// presents a client ID metadata document (CIMD), so it cannot sign in to
// such a server (nothing is listed that cannot connect).
// The rule lifts when OSS signs in by DCR.
const ReasonOAuthSelfRegistrationOnly = "oauth_client_self_registration_only"

// OSSLeftOutReasons explains each reason OSSLeftOutReason returns, for the
// catalog build's report.
var OSSLeftOutReasons = map[string]string{
	ReasonOAuthSelfRegistrationOnly: "its OAuth authorization server accepts only clients that register themselves " +
		"(auth.oauth2.registration lists dcr or cimd, not preregistered), while omega mcp login signs in with a client id " +
		"registered beforehand; it is listed again once OSS Omega can register a client itself",
	ReasonOAuthPublicClientNotProven: "its record does not prove a public client for its OAuth sign-in " +
		"(x-omega.publicClient.supported is not true), while omega mcp login sends a client id and a PKCE verifier and no " +
		"client secret, so it signs in only where the vendor lets a person register a client without one; it is listed " +
		"again once the record proves such a client from the vendor's own pages",
}

// registrationPreregistered is the registration value for a client
// registered with the vendor beforehand.
const registrationPreregistered = "preregistered"

// OSSLeftOutReason returns why OSS Omega's registry leaves out a server for
// the provider, or "" when it lists it. In order:
//   - ReasonOAuthSelfRegistrationOnly: an oauth2 provider whose registration
//     is declared and names no pre-registered client;
//   - ReasonOAuthPublicClientNotProven: an oauth2 or mcp_oauth provider whose
//     x-omega.publicClient does not say supported: true.
//
// A provider that authenticates another way (an API key, none, a local
// setup) is listed.
func OSSLeftOutReason(p *Provider) string {
	o := p.Auth.OAuth2
	if p.Auth.Kind == AuthOAuth2 && o != nil && len(o.Registration) > 0 && !slices.Contains(o.Registration, registrationPreregistered) {
		return ReasonOAuthSelfRegistrationOnly
	}
	if signsInWithOAuth(p) && !provenPublicClient(p) {
		return ReasonOAuthPublicClientNotProven
	}
	return ""
}

// OSSListed returns the servers OSS Omega's registry lists for the app:
// EligibleForOSS less those OSSLeftOutReason leaves out. A server left out
// keeps its id: checkOSS still checks it and no other server may take it,
// so it comes back under the same id when its reason no longer holds.
func OSSListed(app *App) []OSSServer {
	return slices.DeleteFunc(EligibleForOSS(app), func(s OSSServer) bool { return OSSLeftOutReason(s.Provider) != "" })
}
