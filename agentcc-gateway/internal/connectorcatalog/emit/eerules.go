package emit

import (
	"errors"
	"fmt"
	"net"
	"net/url"
	"regexp"
	"slices"
	"strings"
	"unicode"
	"unicode/utf16"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/bundle/canonjson"
)

// EE's settings-file rules, as Omega EE reads OMEGA_INTEGRATIONS_APPS_FILE
// at boot (packages/server/src/workspace/integrations/apps.ts,
// readSettingsApps and settingsApp, at 49321478e4).
var (
	eeAppID      = regexp.MustCompile(`^[a-z][a-z0-9-]{1,39}$`)
	eeEnvPrefix  = regexp.MustCompile(`^OMEGA_INTEGRATION_[A-Z0-9_]{2,40}$`)
	eeCategories = []string{"productivity", "developer", "communication", "data", "research"}
	eeGroups     = []string{"google", "microsoft", "other"}
)

const eeMaxText = 200 // MAX_TEXT, in UTF-16 units as JavaScript counts

// eeDefinitionKeys are IntegrationAppDefinition's members, and
// eeOAuthKeys IntegrationOAuthSpec's (types.ts:24-48 at 49321478e4).
var (
	eeDefinitionKeys = []string{"id", "name", "description", "category", "group", "oauth", "mcpUrl", "envPrefix", "source"}
	eeOAuthKeys      = []string{"authorizeUrl", "tokenUrl", "scopes", "pkce", "tokenBody", "clientAuth", "extraAuthorizeParams"}
)

// CheckEESettingsFile reads data as EE reads its settings file and refuses
// what would stop EE's boot: anything but {"apps": [...]} with at most 64
// apps, an id twice, and per app EE's rules for text, ids, categories,
// groups, env prefixes and HTTPS endpoints. It also refuses what EE would
// read differently from what the file states, so the file means exactly
// what EE does with it: text with spaces EE trims, a non-boolean pkce, a
// tokenBody or clientAuth EE would replace with its default, a member EE
// ignores, and an MCP URL EE would list as missing because it is not a
// public host (contactableMcpEndpoint, apps.ts:157-172).
func CheckEESettingsFile(data []byte) error {
	v, err := canonjson.Parse(data)
	if err != nil {
		return fmt.Errorf("EE settings file: EE could not read it as JSON: %w", err)
	}
	obj, _ := v.(canonjson.Object)
	raw, _ := mustGetMember(obj, "apps")
	apps, ok := raw.([]any)
	if obj == nil || !ok || len(apps) > EEMaxSettingsApps {
		return fmt.Errorf(`EE settings file: must hold {"apps": [...]} with at most %d apps`, EEMaxSettingsApps)
	}
	var errs []error
	seen := map[string]bool{}
	for i, app := range apps {
		id, err := checkEEApp(app, true)
		if err != nil {
			errs = append(errs, fmt.Errorf("EE settings file app %d: %w", i+1, err))
			continue
		}
		if seen[id] {
			errs = append(errs, fmt.Errorf("EE settings file lists app %q twice", id))
		}
		seen[id] = true
	}
	return errors.Join(errs...)
}

// checkEEDefinition holds an ee-apps.json entry to IntegrationAppDefinition:
// EE's rules for each member, and source "built-in".
func checkEEDefinition(def canonjson.Object) error {
	if _, err := checkEEApp(def, false); err != nil {
		return err
	}
	if s, _ := mustGetMember(def, "source"); s != "built-in" {
		return errors.New(`source must be "built-in"`)
	}
	return nil
}

// checkEEApp applies settingsApp's rules to one app and returns its id. In
// a settings file group and envPrefix may be absent, mcpUrl is required and
// source absent; in a definition all but mcpUrl are present.
func checkEEApp(raw any, settings bool) (string, error) {
	app, ok := raw.(canonjson.Object)
	if !ok {
		return "", errors.New("must be an object")
	}
	var errs []error
	fail := func(format string, args ...any) { errs = append(errs, fmt.Errorf(format, args...)) }
	for _, m := range app {
		if !slices.Contains(eeDefinitionKeys, m.Key) || (settings && m.Key == "source") {
			fail("member %q is not one EE reads", m.Key)
		}
	}
	text := func(key string, required bool) (string, bool) {
		v, present := mustGetMember(app, key)
		if !present {
			if required {
				fail("%q must be short text", key)
			}
			return "", false
		}
		s, ok := v.(string)
		switch {
		case !ok || jsTrim(s) == "" || len(utf16.Encode([]rune(s))) > eeMaxText:
			fail("%q must be short text", key)
		case jsTrim(s) != s:
			fail("%q has spaces at an end, which EE trims", key)
		}
		return s, ok
	}
	id, _ := text("id", true)
	if !eeAppID.MatchString(id) {
		fail(`"id" must be lowercase letters, digits and dashes`)
	}
	if c, _ := text("category", true); !slices.Contains(eeCategories, c) {
		fail(`"category" is not one of %s`, strings.Join(eeCategories, ", "))
	}
	if g, ok := text("group", !settings); ok && !slices.Contains(eeGroups, g) {
		fail(`"group" is not one of %s`, strings.Join(eeGroups, ", "))
	}
	if p, ok := text("envPrefix", !settings); ok && !eeEnvPrefix.MatchString(p) {
		fail(`"envPrefix" must look like OMEGA_INTEGRATION_NAME`)
	}
	text("name", true)
	text("description", !settings)
	if u, ok := text("mcpUrl", settings); ok {
		if !eeSafeEndpoint(u) {
			fail(`"mcpUrl" must be an HTTPS URL`)
		} else if !eePublicHost(u) {
			fail(`"mcpUrl" must name a public host; EE would list the app as missing one`)
		}
	}
	errs = append(errs, checkEEOAuth(app)...)
	return id, errors.Join(errs...)
}

func checkEEOAuth(app canonjson.Object) []error {
	var errs []error
	fail := func(format string, args ...any) { errs = append(errs, fmt.Errorf(format, args...)) }
	raw, _ := mustGetMember(app, "oauth")
	oauth, ok := raw.(canonjson.Object)
	if !ok {
		return []error{errors.New(`"oauth" must be an object`)}
	}
	for _, m := range oauth {
		if !slices.Contains(eeOAuthKeys, m.Key) {
			fail("oauth member %q is not one EE reads", m.Key)
		}
	}
	for _, key := range []string{"authorizeUrl", "tokenUrl"} {
		if s, ok := mustGetString(oauth, key); !ok || !eeSafeEndpoint(s) {
			fail(`"oauth.%s" must be an HTTPS URL`, key)
		}
	}
	if _, ok := mustGetString(oauth, "scopes"); !ok {
		fail(`"oauth.scopes" must be text; EE reads anything else as no scopes`)
	}
	if v, _ := mustGetMember(oauth, "pkce"); v != true && v != false {
		fail(`"oauth.pkce" must be true or false; EE reads anything but false as true`)
	}
	if s, _ := mustGetString(oauth, "tokenBody"); s != "form" && s != "json" {
		fail(`"oauth.tokenBody" must be form or json; EE reads anything else as form`)
	}
	if s, _ := mustGetString(oauth, "clientAuth"); s != "body" && s != "basic" {
		fail(`"oauth.clientAuth" must be body or basic; EE reads anything else as body`)
	}
	if v, present := mustGetMember(oauth, "extraAuthorizeParams"); present {
		params, ok := v.(canonjson.Object)
		for _, m := range params {
			if _, s := m.Value.(string); !s {
				ok = false
			}
		}
		if !ok {
			fail(`"oauth.extraAuthorizeParams" must map names to text`)
		}
	}
	return errs
}

// eeSafeEndpoint is EE's safeEndpoint without development loopback: an
// https URL with a host, no credentials and no fragment.
func eeSafeEndpoint(s string) bool {
	u, err := url.Parse(s)
	return err == nil && u.Scheme == "https" && u.Hostname() != "" && u.User == nil && u.Fragment == "" && !strings.Contains(s, "#")
}

// eePublicHost holds the host to what EE's connector store accepts at
// attach (normalizeCustomConnectorOrigin, through contactableMcpEndpoint):
// a dotted DNS name, not an IP literal, localhost or a .local, .internal
// or .localhost name.
func eePublicHost(s string) bool {
	u, err := url.Parse(s)
	if err != nil {
		return false
	}
	host := strings.ToLower(u.Hostname())
	if net.ParseIP(host) != nil || !strings.Contains(host, ".") || host == "localhost" {
		return false
	}
	for _, suffix := range []string{".local", ".internal", ".localhost"} {
		if strings.HasSuffix(host, suffix) {
			return false
		}
	}
	return true
}

// jsTrim is JavaScript's String.prototype.trim: it removes ECMAScript
// WhiteSpace and LineTerminator code points at both ends.
func jsTrim(s string) string {
	return strings.TrimFunc(s, func(r rune) bool {
		switch r {
		case '\t', '\v', '\f', ' ', 0xa0, 0xfeff, '\n', '\r', 0x2028, 0x2029:
			return true
		}
		return unicode.Is(unicode.Zs, r)
	})
}

func mustGetMember(o canonjson.Object, key string) (any, bool) { return o.Get(key) }

func mustGetString(o canonjson.Object, key string) (string, bool) {
	v, _ := o.Get(key)
	s, ok := v.(string)
	return s, ok
}
