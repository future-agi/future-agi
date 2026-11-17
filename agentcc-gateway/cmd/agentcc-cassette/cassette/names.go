package cassette

import (
	"net/url"
	"strings"
)

// The scrubber and the replay keys share these rules (through nameRules, which
// adds a recording's own names), so a location blinded at replay is exactly
// a location scrubbed at record.

// normName lowercases a name and drops everything but letters and digits:
// api_key, api-key, apiKey and API_KEY are all "apikey".
func normName(s string) string {
	var b strings.Builder
	for _, r := range strings.ToLower(s) {
		if r >= 'a' && r <= 'z' || r >= '0' && r <= '9' {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// Credential names, compared after normName. Three rules apply everywhere
// (JSON members, query and form parameters): an exact secretNames entry, a
// secretSuffixes ending, unless the name is in notSecretNames. Pagination
// cursors (pageToken, nextPageToken, syncToken, $skiptoken, ...) end like
// credentials but are not: blinding them would merge page 1 and page 2
// into one replay key.
var secretNames = setOf("token", "secret", "password", "passwd", "pwd", "passphrase", "sig", "signature",
	"clientid", "clientsecret", "codeverifier", "assertion", "clientassertion", "privatekey", "bearer",
	"credential", "credentials", "jwt", "sessionid", "authorization", "cookie", "apikey", "oauthverifier")

var secretSuffixes = []string{"token", "secret", "password", "passwd", "passphrase", "signature", "credential",
	"credentials", "apikey", "privatekey",
	// key compounds: JSON members ending in a bare "key" are mostly data
	// (Jira's issueKey and projectKey, Confluence's spaceKey, Figma's
	// fileKey are tool arguments), so only these endings count there.
	"accesskey", "secretkey", "appkey", "userkey", "signingkey", "encryptionkey", "subscriptionkey",
	"applicationkey", "masterkey", "licensekey", "clientkey", "consumerkey", "sessionkey", "authkey",
	"integrationkey", "routingkey", "servicekey", "writekey", "accountkey", "sharedkey", "hmackey", "devkey",
	"developerkey"}

var notSecretNames = setOf("pagetoken", "nextpagetoken", "prevpagetoken", "previouspagetoken", "startpagetoken",
	"newstartpagetoken", "synctoken", "nextsynctoken", "continuationtoken", "nextcontinuationtoken", "nexttoken",
	"prevtoken", "previoustoken", "paginationtoken", "skiptoken", "deltatoken", "resumetoken", "pagekey",
	"startkey", "exclusivestartkey", "lastevaluatedkey", "nextkey")

// As query or form parameters, more names are credentials: key, auth and
// appid (Google's ?key=, Firebase's ?auth=, OpenWeatherMap's ?appid=) and
// any name ending in key (GitLab's private_token aside, ?access_key=,
// ?app_key=, ?subscription-key=, ?wskey=), except notSecretParams, which
// are data in the APIs this program records (Confluence's ?spaceKey=).
var paramOnlySecrets = setOf("key", "auth", "appid")

var notSecretParams = setOf("spacekey", "projectkey", "issuekey", "issueidorkey", "projectidorkey", "filekey",
	"lookupkey", "sortkey", "partitionkey", "rowkey", "flagkey", "objectkey", "cachekey")

// oauthParams are secret only beside an OAuth companion (or in a Location):
// ?code=US is a country, ?code=...&state=... is an authorization code.
var oauthParams = setOf("code", "state", "sessionstate")

var oauthCompanions = setOf("state", "sessionstate", "granttype", "redirecturi", "codeverifier", "iss")

// oauthJSONCompanions make code and state secret in a JSON object (a JSON
// token request); state alone does not, it is an issue's or an address's.
var oauthJSONCompanions = setOf("granttype", "redirecturi", "codeverifier")

// builtinSecret is the everywhere rule, on a normalized name.
func builtinSecret(k string) bool {
	switch {
	case k == "" || notSecretNames[k]:
		return false
	case secretNames[k]:
		return true
	}
	for _, suffix := range secretSuffixes {
		if strings.HasSuffix(k, suffix) {
			return true
		}
	}
	return false
}

// builtinSecretParam is the query and form rule, on a normalized name.
func builtinSecretParam(k string, oauth bool) bool {
	switch {
	case builtinSecret(k), paramOnlySecrets[k], oauth && oauthParams[k]:
		return true
	case k == "" || notSecretNames[k] || notSecretParams[k]:
		return false
	}
	return strings.HasSuffix(k, "key")
}

// oauthQuery reports whether a raw query string carries an OAuth companion.
func oauthQuery(raw string) bool {
	for _, part := range strings.Split(raw, "&") {
		name, _, _ := strings.Cut(part, "=")
		if n, err := url.QueryUnescape(name); err == nil && oauthCompanions[normName(n)] {
			return true
		}
	}
	return false
}

// credentialHeaders are always credentials; any header whose name contains a
// credentialHeaderParts word is one too, except plainHeaders: the challenges
// a server sends (an OAuth replay needs them) and a CORS flag, none of which
// carries a secret.
var credentialHeaders = setOf("authorization", "proxy-authorization", "cookie", "set-cookie", "dpop")

var credentialHeaderParts = []string{"auth", "key", "token", "secret", "passw", "cookie",
	"credential", "signature", "session", "csrf", "xsrf"}

var plainHeaders = setOf("www-authenticate", "proxy-authenticate", "access-control-allow-credentials")

func isCredentialHeader(name string, extra map[string]bool) bool {
	n := strings.ToLower(name)
	if plainHeaders[n] {
		return false
	}
	if credentialHeaders[n] || extra[n] {
		return true
	}
	for _, part := range credentialHeaderParts {
		if strings.Contains(n, part) {
			return true
		}
	}
	return false
}

// isIDKey reports a key (JSON key, parameter or header name) whose value is
// an identifier: id, ids, uuid and guid; a _id, -id or _ids suffix; or a
// camel-case Id, ID, Ids or IDs suffix after a lower-case letter or digit
// (userId, threadID). $id and @id are schema and JSON-LD keywords, not ids.
func isIDKey(key string, extra map[string]bool) bool {
	if extra[key] {
		return true
	}
	lower := strings.ToLower(key)
	switch lower {
	case "id", "ids", "uuid", "guid":
		return true
	}
	for _, s := range []string{"_id", "-id", "_ids", "-ids"} {
		if strings.HasSuffix(lower, s) && len(lower) > len(s) {
			return true
		}
	}
	for _, s := range []string{"Id", "ID", "Ids", "IDs"} {
		if strings.HasSuffix(key, s) && len(key) > len(s) {
			c := key[len(key)-len(s)-1]
			if c >= 'a' && c <= 'z' || c >= '0' && c <= '9' {
				return true
			}
		}
	}
	return false
}

func setOf(names ...string) map[string]bool {
	m := make(map[string]bool, len(names))
	for _, n := range names {
		m[n] = true
	}
	return m
}
