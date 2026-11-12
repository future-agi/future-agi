package manifest

import (
	"fmt"
	"maps"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf16"
	"unicode/utf8"
)

// EEApp is the IntegrationAppDefinition EE's emitter writes for an app
// (EE:packages/server/src/workspace/integrations/types.ts:36-48 at
// 49321478e4), less source, which the emitter sets. L0 checks exactly this
// value against EE's boot rules, so the catalog emitters write it as projected.
type EEApp struct {
	ID          string  `json:"id"`
	Name        string  `json:"name"`
	Description string  `json:"description"`
	Category    string  `json:"category"`
	Group       string  `json:"group"`
	OAuth       EEOAuth `json:"oauth"`
	MCPURL      string  `json:"mcpUrl,omitempty"`
	EnvPrefix   string  `json:"envPrefix"`
}

// EEOAuth is EE's IntegrationOAuthSpec (EE:types.ts:24-33).
type EEOAuth struct {
	AuthorizeURL         string            `json:"authorizeUrl"`
	TokenURL             string            `json:"tokenUrl"`
	Scopes               string            `json:"scopes"`
	PKCE                 bool              `json:"pkce"`
	TokenBody            string            `json:"tokenBody"`
	ClientAuth           string            `json:"clientAuth"`
	ExtraAuthorizeParams map[string]string `json:"extraAuthorizeParams,omitempty"`
}

// eeBuiltIns are the apps EE ships (EE:apps.ts:34-79 at 49321478e4). Each
// keeps its id and env prefix, so no deployment's connections or env
// variables are renamed (omega-coordination.md, 05:40 item 1b).
var eeBuiltIns = []struct{ ID, EnvPrefix string }{
	{"gmail", "OMEGA_INTEGRATION_GMAIL"},
	{"google-calendar", "OMEGA_INTEGRATION_GOOGLE_CALENDAR"},
	{"google-drive", "OMEGA_INTEGRATION_GOOGLE_DRIVE"},
	{"slack", "OMEGA_INTEGRATION_SLACK"},
	{"github", "OMEGA_INTEGRATION_GITHUB"},
	{"linear", "OMEGA_INTEGRATION_LINEAR"},
	{"notion", "OMEGA_INTEGRATION_NOTION"},
	{"atlassian", "OMEGA_INTEGRATION_ATLASSIAN"},
}

// eeSharedGooglePrefix is the env prefix of EE's shared Google OAuth client:
// a built-in app in group google falls back to its variables when its own
// are unset (EE:apps.ts:113-119). An app whose own prefix it was would read
// that client as its own, so no app takes it.
const eeSharedGooglePrefix = "OMEGA_INTEGRATION_GOOGLE"

// eeGoogleTokenURL is Google's token endpoint, the one EE's built-in Google
// apps use (EE:apps.ts:23-25).
const eeGoogleTokenURL = "https://oauth2.googleapis.com/token"

// eeMaxSettingsApps is EE's cap on the settings file (EE:apps.ts:184); EE
// refuses to boot past it.
const eeMaxSettingsApps = 64

// eeMaxText is EE's MAX_TEXT, counted as JavaScript does, in UTF-16 units
// (EE:apps.ts:85, :200).
const eeMaxText = 200

var eePostureName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`) // EE:attachments.ts:305

// eeEnvPrefix is EE's default env prefix for an id (EE:apps.ts:209).
func eeEnvPrefix(id string) string {
	return "OMEGA_INTEGRATION_" + strings.ToUpper(strings.ReplaceAll(id, "-", "_"))
}

// EEAppFor projects the app onto EE's definition through its EE-eligible
// provider; ok is false when it has none. A rest provider gives EE no
// mcpUrl, so the operator supplies <envPrefix>_MCP_URL, as for EE's Google
// apps today.
func EEAppFor(app *App) (EEApp, bool) {
	p, ok := EligibleForEE(app)
	if !ok {
		return EEApp{}, false
	}
	ee, _ := eeProject(app, p)
	return ee, true
}

// eeProject builds the EE definition and lists what EE could not use.
func eeProject(app *App, p *Provider) (EEApp, []string) {
	var problems []string
	id, prefix := app.ID, ""
	if x := app.XOmega; x != nil {
		if x.EEID != "" {
			id = x.EEID
		}
		prefix = x.EnvPrefix
	}
	if prefix == "" {
		prefix = eeEnvPrefix(id)
	}
	o := p.Auth.OAuth2
	fill := func(field, s string) string {
		return expand(s, func(name string) string {
			if v, ok := o.URLVariables[name]; ok && v.Default != "" {
				return v.Default
			}
			problems = append(problems, fmt.Sprintf("EE needs a default for {%s} in auth.oauth2.%s, or set x-omega.ee: exclude", name, field))
			return "{" + name + "}"
		})
	}
	ee := EEApp{
		ID: id, Name: app.Name, Description: app.Description, Category: app.EECategory, Group: app.Group, EnvPrefix: prefix,
		OAuth: EEOAuth{
			AuthorizeURL: fill("authorizeUrl", o.AuthorizeURL), TokenURL: fill("tokenUrl", o.TokenURL),
			Scopes: strings.Join(o.Scopes, " "), PKCE: o.PKCE, TokenBody: o.TokenBody, ClientAuth: o.ClientAuth,
			ExtraAuthorizeParams: maps.Clone(o.ExtraAuthorizeParams),
		},
	}
	if p.Kind == KindVendorMCP && len(templateVars(p.Transport.URL)) == 0 {
		ee.MCPURL = p.Transport.URL
	}
	return ee, problems
}

// checkEE applies EE's boot rules (EE:apps.ts:83-85, 149-172, 184, 197-221;
// attachments.ts:19, 305) to every app EE would receive, so a generated EE
// app cannot fail EE's boot. It keeps EE's built-in ids and env prefixes,
// one env prefix per app, Google's token endpoint for an app in group
// google, and the 64-app cap on the settings file.
func checkEE(cat *Catalog) []error {
	var errs []error
	fail := func(path, format string, args ...any) {
		errs = append(errs, fmt.Errorf("%s: EE: %s", path, fmt.Sprintf(format, args...)))
	}
	ids, prefixes := map[string]string{}, map[string]string{} // EE id, env prefix -> app id
	settings := 0
	for _, a := range cat.Apps {
		p, ok := EligibleForEE(a)
		if !ok {
			continue
		}
		ee, problems := eeProject(a, p)
		for _, msg := range problems {
			fail(p.path, "%s", msg)
		}
		if o := p.Auth.OAuth2; o.TokenResponse != nil {
			fail(p.path, "EE reads the token at the top level; set x-omega.ee: exclude until OM5 adds tokenResponse")
		}
		if p.Auth.OAuth2.ResourceIndicator {
			fail(p.path, "EE does not send the RFC 8707 resource; set x-omega.ee: exclude")
		}
		if !stringDefs["appId"].pattern.MatchString(ee.ID) {
			fail(a.path, "id %q does not match EE's APP_ID", ee.ID)
		}
		if !stringDefs["envPrefix"].pattern.MatchString(ee.EnvPrefix) {
			fail(a.path, "envPrefix %q does not match EE's ENV_PREFIX", ee.EnvPrefix)
		}
		for _, f := range []struct{ name, value string }{{"name", ee.Name}, {"description", ee.Description}} {
			switch {
			case untrimmed(f.value):
				fail(a.path, "EE trims %s (JavaScript's trim); give it without spaces or invisible characters at either end", f.name)
			case len(utf16.Encode([]rune(f.value))) > eeMaxText:
				fail(a.path, "%s is longer than EE's %d characters", f.name, eeMaxText)
			}
		}
		if ee.Group == "google" && ee.OAuth.TokenURL != eeGoogleTokenURL {
			fail(p.path, "app %s is in group google, so EE may give it the shared Google OAuth client (EE:apps.ts:117-118), whose secret goes only to Google's token endpoint: tokenUrl must be %s, or set the app's group to other",
				a.ID, eeGoogleTokenURL)
		}
		for _, u := range []struct{ name, value string }{{"auth.oauth2.authorizeUrl", ee.OAuth.AuthorizeURL}, {"auth.oauth2.tokenUrl", ee.OAuth.TokenURL}} {
			if len(templateVars(u.value)) == 0 {
				if err := checkURLSyntax(u.value); err != nil {
					fail(p.path, "%s: %v", u.name, err)
				}
			}
		}
		if ee.MCPURL != "" {
			if err := checkHTTPSURL(ee.MCPURL); err != nil {
				fail(p.path, "mcpUrl: %v", err)
			}
			if n := len(utf16.Encode([]rune(ee.MCPURL))); n > eeMaxText {
				fail(p.path, "mcpUrl is %d characters; EE's settings file allows %d (EE:apps.ts:218)", n, eeMaxText)
			}
			settings++
		}
		for _, t := range p.AllTools() {
			if !eePostureName.MatchString(t.Name) {
				fail(p.path, "tool %s does not match EE's posture rule", t.Name)
			}
		}
		if n := len(p.AllTools()); n > maxServerTools {
			fail(p.path, "lists %d tools; EE keeps at most %d", n, maxServerTools)
		}
		errs = append(errs, checkEEIDs(a, ee, ids, prefixes)...)
	}
	if settings > eeMaxSettingsApps {
		errs = append(errs, fmt.Errorf("EE's settings file holds at most %d apps; %d qualify (EE-eligible with a fixed vendor MCP URL): set x-omega.ee: exclude on the rest",
			eeMaxSettingsApps, settings))
	}
	return errs
}

// checkEEIDs keeps EE ids and env prefixes unique, keeps EE's built-in apps
// unrenamed, and keeps every other app off their prefixes.
//
// EE reads an app's OAuth client and endpoints from <envPrefix>_CLIENT_ID,
// _CLIENT_SECRET, _MCP_URL, _AUTHORIZE_URL, _TOKEN_URL and _SCOPES
// (EE:apps.ts:113-133) and posts the secret to the app's own tokenUrl
// (EE:oauth.ts:197-232). It checks app ids for repeats, never prefixes
// (EE:apps.ts:185-189), so an app that took another's prefix would send that
// app's client to its own token endpoint. Only an equal prefix shares a
// variable: none of the six names, with its underscore, ends with another,
// so two prefixes share none even when one nests in the other, as EE's own
// OMEGA_INTEGRATION_GOOGLE does in OMEGA_INTEGRATION_GOOGLE_CALENDAR.
func checkEEIDs(a *App, ee EEApp, ids, prefixes map[string]string) []error {
	var errs []error
	fail := func(format string, args ...any) {
		errs = append(errs, fmt.Errorf("%s: EE: %s", a.path, fmt.Sprintf(format, args...)))
	}
	if other, dup := ids[ee.ID]; dup {
		fail("EE id %s is emitted by apps %s and %s", ee.ID, other, a.ID)
	}
	ids[ee.ID] = a.ID
	if other, dup := prefixes[ee.EnvPrefix]; dup {
		fail("env prefix %s is emitted by apps %s and %s; EE would give both one OAuth client; set x-omega.envPrefix", ee.EnvPrefix, other, a.ID)
	}
	prefixes[ee.EnvPrefix] = a.ID
	if ee.EnvPrefix == eeSharedGooglePrefix {
		fail("env prefix %s is EE's shared Google OAuth client (EE:apps.ts:117-118); set x-omega.envPrefix", ee.EnvPrefix)
	}
	for _, b := range eeBuiltIns {
		switch {
		case ee.ID == b.ID && a.ID != b.ID:
			fail("EE id %s belongs to EE's built-in app %s", ee.ID, b.ID)
		case a.ID == b.ID && ee.ID != b.ID:
			fail("EE's built-in app %s must keep its id; x-omega.eeId %s would rename it", b.ID, ee.ID)
		case a.ID == b.ID && ee.EnvPrefix != b.EnvPrefix:
			fail("EE's built-in app %s must keep envPrefix %s", b.ID, b.EnvPrefix)
		case ee.EnvPrefix == b.EnvPrefix && ee.ID != b.ID:
			fail("env prefix %s belongs to EE's built-in app %s, whose OAuth client it would read; set x-omega.envPrefix", ee.EnvPrefix, b.ID)
		}
	}
	return errs
}

// untrimmed reports whether s is empty or EE's text() would change it: EE
// trims with JavaScript's String.prototype.trim (EE:apps.ts:197-200), which
// removes ECMAScript's WhiteSpace (TAB, VT, FF, SP, U+FEFF and every Zs) and
// LineTerminator (LF, CR, U+2028, U+2029) code points. Go's TrimSpace differs
// from it (it keeps U+FEFF and removes U+0085), so this refuses every
// Unicode space and every format character at either end, which covers
// both.
func untrimmed(s string) bool {
	if s == "" {
		return true
	}
	first, _ := utf8.DecodeRuneInString(s)
	last, _ := utf8.DecodeLastRuneInString(s)
	return trimmedByJS(first) || trimmedByJS(last)
}

func trimmedByJS(r rune) bool {
	return unicode.IsSpace(r) || unicode.Is(unicode.Cf, r) // White_Space holds every Zs, U+2028 and U+2029; Cf holds U+FEFF
}
