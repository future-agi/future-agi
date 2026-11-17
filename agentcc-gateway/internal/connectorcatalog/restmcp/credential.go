package restmcp

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"slices"
	"sort"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// credentialSpec is where a provider's credential arrives on an MCP request
// and where it goes on the vendor request: the same place for both.
type credentialSpec struct {
	app    string
	bearer bool       // oauth2: Authorization: Bearer <the user's vendor token>
	scopes []string   // oauth2 scopes, named in the challenge
	parts  []credPart // api_key: each part the caller must send
	consts []credPart // api_key: non-secret constants from the manifest
}

// reservedHeaders carry the MCP transport or the HTTP framing, on the
// request in or out, so no credential part may be one of them.
var reservedHeaders = map[string]bool{
	"Host": true, "Content-Length": true, "Content-Type": true, "Content-Encoding": true,
	"Transfer-Encoding": true, "Connection": true, "Accept": true, "Accept-Encoding": true,
	"User-Agent": true, "Mcp-Session-Id": true, "Mcp-Protocol-Version": true,
	"Mcp-Method": true, "Mcp-Name": true, "Origin": true,
}

// credPart is one credential part: a header or a query parameter.
type credPart struct {
	in, name, value string
	secret          bool
}

// credential is one request's credential, ready to place on the vendor
// request. It never prints its values.
type credential struct {
	header  http.Header
	query   url.Values
	secrets []string // every value, for scrubbing vendor text
}

func (c *credential) String() string   { return "credential{redacted}" }
func (c *credential) GoString() string { return c.String() }

// challenge is a 401: the WWW-Authenticate value and a message for the body.
type challenge struct {
	header, message string
}

// compileCredential reads the provider's auth block. The runtime serves
// oauth2 (the bearer is the user's vendor token) and api_key with header or
// query parts; every other kind, and a part in a URL variable (a rest
// provider's URL has none), is refused.
func compileCredential(app string, auth manifest.Auth) (*credentialSpec, error) {
	spec := &credentialSpec{app: app}
	switch auth.Kind {
	case manifest.AuthOAuth2:
		spec.bearer = true
		if auth.OAuth2 != nil {
			spec.scopes = auth.OAuth2.Scopes
		}
		return spec, nil
	case manifest.AuthAPIKey:
		if len(auth.CredentialParts) == 0 {
			return nil, fmt.Errorf("auth kind api_key declares no credentialParts")
		}
		for _, p := range auth.CredentialParts {
			part := credPart{in: p.In, name: p.Name, value: p.Value, secret: p.Secret}
			switch {
			case p.In != "header" && p.In != "query":
				return nil, fmt.Errorf("credential part %s is in %s: the runtime places parts in a header or a query parameter only", p.Name, p.In)
			case p.In == "header" && !validHeaderName(p.Name):
				return nil, fmt.Errorf("credential part %s is not a valid header name", p.Name)
			case p.In == "header" && reservedHeaders[http.CanonicalHeaderKey(p.Name)]:
				return nil, fmt.Errorf("credential part %s is a header the transport sets itself", p.Name)
			case !p.Secret && p.Value != "":
				spec.consts = append(spec.consts, part)
			default:
				spec.parts = append(spec.parts, part)
			}
		}
		if len(spec.parts) == 0 {
			return nil, fmt.Errorf("auth kind api_key declares no part the caller sends")
		}
		return spec, nil
	}
	return nil, fmt.Errorf("auth kind %s: the runtime serves oauth2 and api_key providers only", auth.Kind)
}

// queryClash names a query input of t that goes out under the name of a
// credential part (or constant) the provider places in the query, compared
// without case: the vendor would get a model-chosen value next to the
// user's key under the key's name, and one that reads the first would run
// the call as whatever account the model named.
func (s *credentialSpec) queryClash(t *Tool) string {
	taken := map[string]bool{}
	for _, p := range append(append([]credPart(nil), s.parts...), s.consts...) {
		if p.in == "query" {
			taken[strings.ToLower(p.name)] = true
		}
	}
	if len(taken) == 0 {
		return ""
	}
	for _, name := range t.schema.names() {
		in := t.schema.inputs[name]
		if name == bodyInput || contains(t.pathParams, name) || !taken[strings.ToLower(in.wireName)] {
			continue
		}
		return fmt.Sprintf("input %s is sent as query parameter %s, the name of a credential part", name, in.wireName)
	}
	return ""
}

// extract reads the request's credential, or says why it cannot.
func (s *credentialSpec) extract(r *http.Request) (*credential, *challenge) {
	c := &credential{header: http.Header{}, query: url.Values{}}
	if s.bearer {
		token, ok := bearerToken(r.Header.Get("Authorization"))
		if !ok {
			return nil, s.bearerChallenge(r.Header.Get("Authorization") != "")
		}
		c.header.Set("Authorization", "Bearer "+token)
		c.addSecret(token)
		return c, nil
	}
	query := r.URL.Query()
	for _, p := range s.parts {
		v := r.Header.Get(p.name)
		if p.in == "query" {
			v = query.Get(p.name)
		}
		if strings.TrimSpace(v) == "" {
			return nil, &challenge{
				header:  fmt.Sprintf(`APIKey realm=%s, in=%s, name=%s`, strconv.Quote(s.app), strconv.Quote(p.in), strconv.Quote(p.name)),
				message: fmt.Sprintf("missing credential: send the %s %s %s", s.app, p.in, p.name),
			}
		}
		c.place(p.in, p.name, v)
		c.addSecret(v)
		if _, rest, ok := strings.Cut(v, " "); ok && rest != "" {
			c.addSecret(rest) // a prefixed value: its bare part too
		}
	}
	for _, p := range s.consts {
		c.place(p.in, p.name, p.value)
	}
	return c, nil
}

func (c *credential) place(in, name, value string) {
	if in == "query" {
		c.query.Set(name, value)
		return
	}
	c.header.Set(name, value)
}

// bearerChallenge is RFC 6750's: no error code when no credential was sent,
// invalid_request when one was sent but is not a bearer token.
func (s *credentialSpec) bearerChallenge(malformed bool) *challenge {
	h := "Bearer realm=" + strconv.Quote(s.app)
	if len(s.scopes) > 0 {
		h += ", scope=" + strconv.Quote(strings.Join(s.scopes, " "))
	}
	msg := "missing credential: send the user's " + s.app + " access token as Authorization: Bearer <token>"
	if malformed {
		h += `, error="invalid_request"`
		msg = "the Authorization header is not a bearer token"
	}
	return &challenge{header: h, message: msg}
}

// bearerToken parses "Bearer <token>" (the scheme is case-insensitive).
// The token is RFC 6750's b64token: one or more of ALPHA DIGIT -._~+/
// followed by any number of =.
func bearerToken(h string) (string, bool) {
	scheme, token, ok := strings.Cut(h, " ")
	if !ok || !strings.EqualFold(scheme, "Bearer") {
		return "", false
	}
	token = strings.TrimLeft(token, " ")
	body := strings.TrimRight(token, "=")
	if body == "" {
		return "", false
	}
	for _, r := range body {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9':
		case strings.ContainsRune("-._~+/", r):
		default:
			return "", false
		}
	}
	return token, true
}

// validHeaderName reports whether name is an HTTP token (RFC 9110).
func validHeaderName(name string) bool {
	if name == "" {
		return false
	}
	for _, r := range name {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9':
		case strings.ContainsRune("!#$%&'*+-.^_`|~", r):
		default:
			return false
		}
	}
	return true
}

// redacted replaces a credential value in any text the caller or a log sees.
const redacted = "[redacted]"

// addSecret keeps v for scrubbing, with the forms a vendor's JSON may spell
// it in: JSON-escaped, with and without Go's escaping of <, > and &, each
// also with / escaped as \/ (PHP's json_encode does that by default). The
// longest forms come first, so a shorter one never splits a longer one.
func (c *credential) addSecret(v string) {
	forms := []string{v}
	for _, escapeHTML := range []bool{false, true} {
		var buf bytes.Buffer
		enc := json.NewEncoder(&buf)
		enc.SetEscapeHTML(escapeHTML)
		_ = enc.Encode(v)
		quoted := strings.TrimSuffix(buf.String(), "\n")
		inner := quoted[1 : len(quoted)-1]
		forms = append(forms, inner, strings.ReplaceAll(inner, "/", `\/`))
	}
	for _, f := range forms {
		if !slices.Contains(c.secrets, f) {
			c.secrets = append(c.secrets, f)
		}
	}
	sort.SliceStable(c.secrets, func(i, j int) bool { return len(c.secrets[i]) > len(c.secrets[j]) })
}

// scrub replaces every credential value in text.
func (c *credential) scrub(text string) string {
	if c == nil {
		return text
	}
	for _, s := range c.secrets {
		if len(s) >= 4 {
			text = strings.ReplaceAll(text, s, redacted)
		}
	}
	return text
}

// scrubBytes is scrub for a vendor's answer, before anything reads it: a
// text cut later (a result over response.maxBytes, a long vendor message)
// can then never keep part of a credential. It copies b only when b holds
// one.
func (c *credential) scrubBytes(b []byte) []byte {
	if c == nil {
		return b
	}
	for _, s := range c.secrets {
		if len(s) >= 4 && bytes.Contains(b, []byte(s)) {
			b = bytes.ReplaceAll(b, []byte(s), []byte(redacted))
		}
	}
	return b
}
