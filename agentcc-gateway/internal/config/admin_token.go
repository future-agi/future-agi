package config

import (
	"fmt"
	"log/slog"
	"slices"
	"strings"
	"unicode/utf8"
)

// EnvAllowInsecureAdminToken names the env var that lets a gateway start with
// an admin token validateAdminTokens refuses. Local development only: it is
// read from the environment and never from a config file, so a shipped
// config cannot turn the check off.
const EnvAllowInsecureAdminToken = "AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN"

// MinAdminTokenLength is the shortest admin token the gateway starts with:
// 32 characters, as `openssl rand -hex 16` prints. The installer and the Helm
// chart generate 64 and 48.
const MinAdminTokenLength = 32

// minAdminTokenDistinctChars catches long tokens with almost no randomness
// ("aaaa...", "abab..."); 32 random hex characters hold about 14 distinct ones.
const minAdminTokenDistinctChars = 8

// The lists below are the admin-token policy, written once here and once in
// futureagi/agentcc/admin_token.py: Django's admin endpoints accept the same
// credential. futureagi/agentcc/tests/test_admin_token_parity.py checks the two
// copies are equal and agree on testdata/admin_token_cases.json. Keep each
// entry on its own quoted string and no double quote in a comment inside a
// list: that test reads them out of this file.

// publishedAdminTokens are values published in this repository or its docs.
// A token that contains any of them is refused, padded or not. Each is long
// and carries a separator, so a generated token cannot contain one.
var publishedAdminTokens = []string{
	"agentcc-admin-secret",                    // a published sample value
	"local-dev-only-admin-token-replace-me",   // docker-compose*.yml default
	"local-dev-only-shared-secret-replace-me", // the internal API key default
	"your-secret-admin-token",                 // config.example.yaml comment
	"test-admin-secret",                       // a published test value
	"fi-internal-dev-key-change-in-prod",      // futureagi/.env.example
	"test-webhook-secret",                     // futureagi/.env.example
}

// commonAdminTokens are words anyone would try first, refused when the whole
// token is one of them (compared case-insensitively after trimming).
var commonAdminTokens = []string{
	"admin", "admin-token", "admin-secret", "secret", "password",
	"token", "test", "changeme", "default",
}

// placeholderAdminTokenMarkers are words that mark a value someone was meant
// to replace, such as the installer's CHANGEME-* placeholders. Each is either
// 7+ letters or carries a separator, so a generated token (hex from the
// installer, 48 alphanumerics from the Helm chart) all but never contains one.
var placeholderAdminTokenMarkers = []string{
	"changeme", "change-me", "change_me", "change-in-prod",
	"replaceme", "replace-me", "replace_me",
	"your-secret", "your_secret", "your-token", "your_token", "your-admin", "your_admin",
	"placeholder", "example",
	"not-for-production", "dev-only",
}

// adminTokenProblem says what is wrong with token as an admin credential, or
// returns "" when it is acceptable. It never quotes the token.
func adminTokenProblem(token string) string {
	t := strings.TrimSpace(token)
	lower := strings.ToLower(t)
	if t == "" {
		return "is empty"
	}
	for _, published := range publishedAdminTokens {
		if strings.Contains(lower, published) {
			return "is or contains a default published with the gateway"
		}
	}
	if slices.Contains(commonAdminTokens, lower) {
		return "is a default anyone would try"
	}
	for _, marker := range placeholderAdminTokenMarkers {
		if strings.Contains(lower, marker) {
			return "is a placeholder (it contains a word such as changeme or replace-me)"
		}
	}
	if utf8.RuneCountInString(t) < MinAdminTokenLength {
		return fmt.Sprintf("is shorter than %d characters", MinAdminTokenLength)
	}
	distinct := map[rune]struct{}{}
	for _, r := range t {
		distinct[r] = struct{}{}
	}
	if len(distinct) < minAdminTokenDistinctChars {
		return "has too few distinct characters to be random"
	}
	return ""
}

// validateAdminTokens refuses the admin credentials anyone could guess.
//
// admin.token is checked when admin auth is configured: the token is set, the
// config file has an admin.token key (so `token: "${AGENTCC_ADMIN_TOKEN}"`
// with the variable unset counts), AGENTCC_ADMIN_TOKEN is present in the
// environment, or a control plane is configured (Django calls the admin API
// with the same token). A gateway with none of these has no admin API: every
// admin route answers 401 or 403.
//
// control_plane.admin_token, after Load's fallback to admin.token, is checked
// whenever a control plane is configured: it is Django's AGENTCC_ADMIN_TOKEN,
// the credential Django's admin endpoints accept.
func (c *Config) validateAdminTokens() error {
	var problems []string
	if c.Admin.Token != "" || c.Admin.tokenSet || c.ControlPlane.URL != "" {
		if p := adminTokenProblem(c.Admin.Token); p != "" {
			problems = append(problems, "admin.token (AGENTCC_ADMIN_TOKEN) "+p)
		}
	}
	if c.ControlPlane.URL != "" {
		if p := adminTokenProblem(c.ControlPlane.AdminToken); p != "" {
			problems = append(problems, "control_plane.admin_token (AGENTCC_CONTROL_PLANE_TOKEN, else the admin token) "+p)
		}
	}
	if len(problems) == 0 {
		return nil
	}
	if c.Admin.AllowInsecureToken {
		slog.Warn("starting with an insecure admin token because "+EnvAllowInsecureAdminToken+" is set; local development only",
			"problems", strings.Join(problems, "; "))
		return nil
	}
	return fmt.Errorf("insecure admin token: %s. Set AGENTCC_ADMIN_TOKEN to at least %d random characters "+
		"(for example `openssl rand -hex 32`), the same value as the backend's AGENTCC_ADMIN_TOKEN, and "+
		"AGENTCC_CONTROL_PLANE_TOKEN to it too when set. %s is for local development only, "+
		"on a host no one else can reach",
		strings.Join(problems, "; "), MinAdminTokenLength, EnvAllowInsecureAdminToken)
}

// UnmarshalYAML decodes the admin block and records whether it has a token
// key, so a token that expanded to nothing still counts as configured. It
// decodes through the caller's decoder, so a strict decode (warnUnreadKeys)
// still reports a key the admin block does not read.
func (a *AdminConfig) UnmarshalYAML(unmarshal func(interface{}) error) error {
	type plain AdminConfig // drops this method, so the decode does not recurse
	p := plain(*a)
	if err := unmarshal(&p); err != nil {
		return err
	}
	*a = AdminConfig(p)
	var keys map[string]interface{}
	if unmarshal(&keys) == nil {
		if _, ok := keys["token"]; ok {
			a.tokenSet = true
		}
	}
	return nil
}
