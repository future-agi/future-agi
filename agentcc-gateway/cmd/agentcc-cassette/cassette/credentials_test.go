package cassette

import (
	"encoding/base64"
	"strings"
	"testing"
)

// TestScrubStripsAuthHeaders: every credential header, standard or named by
// pattern or by the recording, loses its value; the challenge and CORS
// headers, which hold no secret, keep theirs.
func TestScrubStripsAuthHeaders(t *testing.T) {
	bearer, basic, apiKey, ddKey, custom, private := fakeToken("bearer"), fakeToken("basic"),
		fakeToken("apikey"), fakeToken("ddkey"), fakeToken("custom"), fakeToken("private")
	challenge := `Bearer resource_metadata="https://mcp.example.test/.well-known/oauth-protected-resource"`
	c := newHTTPCassette(HTTPExchange{
		Request: HTTPRequest{Method: "GET", Path: "/v1/me", Headers: hdr(
			"Authorization", "Bearer "+bearer, "Proxy-Authorization", "Basic "+basic,
			"X-Api-Key", apiKey, "DD-API-KEY", ddKey, "X-Vendor-Cred", custom, "Private-Token", private,
			"Accept", "application/json")},
		Response: HTTPResponse{Status: 401, Headers: hdr("WWW-Authenticate", challenge,
			"Access-Control-Allow-Credentials", "true", "Content-Type", "application/json"),
			Body: jsonBody(map[string]string{"error": "invalid_token"})},
	})
	text := saveOK(t, c, ScrubConfig{CredentialHeaders: []string{"X-Vendor-Cred"}})
	assertAbsent(t, text, bearer, basic, apiKey, ddKey, custom, private)
	got := loadSaved(t, text).HTTP.Exchanges[0]
	q := got.Request.Headers
	if a := q.Get("Authorization"); !strings.HasPrefix(a, "Bearer scrubbed-token-") {
		t.Errorf("Authorization = %q, want the scheme kept and the token scrubbed", a)
	}
	if a := q.Get("Proxy-Authorization"); !strings.HasPrefix(a, "Basic scrubbed-token-") {
		t.Errorf("Proxy-Authorization = %q", a)
	}
	for _, name := range []string{"X-Api-Key", "Dd-Api-Key", "X-Vendor-Cred", "Private-Token"} {
		if v := q.Get(name); !strings.HasPrefix(v, "scrubbed-token-") {
			t.Errorf("%s = %q, want a token placeholder", name, v)
		}
	}
	if q.Get("Accept") != "application/json" {
		t.Errorf("Accept = %q, an ordinary header changed", q.Get("Accept"))
	}
	r := got.Response.Headers
	if r.Get("Www-Authenticate") != challenge || r.Get("Access-Control-Allow-Credentials") != "true" {
		t.Errorf("challenge headers changed: %q, %q", r.Get("Www-Authenticate"), r.Get("Access-Control-Allow-Credentials"))
	}
}

// TestScrubStripsCookies: every cookie value goes, however short, and a
// Set-Cookie keeps its name and attributes.
func TestScrubStripsCookies(t *testing.T) {
	session, csrf := fakeToken("session"), fakeToken("csrf")
	c := newHTTPCassette(HTTPExchange{
		Request: HTTPRequest{Method: "GET", Path: "/dashboard",
			Headers: hdr("Cookie", "sid="+session+"; theme=dark-mode-on; lang=en")},
		Response: HTTPResponse{Status: 200, Headers: hdr(
			"Set-Cookie", "sid="+session+"; Path=/; HttpOnly; Secure",
			"Set-Cookie", "csrftoken="+csrf+"; SameSite=Lax")},
	})
	text := saveOK(t, c, ScrubConfig{})
	assertAbsent(t, text, session, csrf, "dark-mode-on")
	got := loadSaved(t, text).HTTP.Exchanges[0]
	cookie := got.Request.Headers.Get("Cookie")
	for _, pair := range strings.Split(cookie, ";") {
		name, value, _ := strings.Cut(strings.TrimSpace(pair), "=")
		if !strings.HasPrefix(value, "scrubbed-token-") {
			t.Errorf("cookie %s kept its value %q", name, value)
		}
	}
	set := got.Response.Headers.Values("Set-Cookie")
	if len(set) != 2 || !strings.HasPrefix(set[0], "sid=scrubbed-token-") || !strings.HasSuffix(set[0], "; Path=/; HttpOnly; Secure") ||
		!strings.HasPrefix(set[1], "csrftoken=scrubbed-token-") || !strings.HasSuffix(set[1], "; SameSite=Lax") {
		t.Errorf("Set-Cookie = %q", set)
	}
	sidReq := strings.TrimPrefix(strings.Split(cookie, ";")[0], "sid=")
	if !strings.HasPrefix(set[0], "sid="+sidReq+";") {
		t.Errorf("one session value got two placeholders: %q and %q", sidReq, set[0])
	}
}

// TestScrubStripsQueryStringSecrets: credential parameters go wherever a URL
// is (the request, a Link header, a URL inside a body); pagination cursors,
// which look like tokens by name, stay.
func TestScrubStripsQueryStringSecrets(t *testing.T) {
	access, key, sig, secret, apiKey, inBody, inLink := fakeToken("access"), fakeToken("key"), fakeToken("sig"),
		fakeToken("secret"), fakeToken("apikey"), fakeToken("inbody"), fakeToken("inlink")
	c := newHTTPCassette(HTTPExchange{
		Request: HTTPRequest{Method: "GET", Path: "/v1/items",
			Query: "access_token=" + access + "&key=" + key + "&q=from%3Ame&pageToken=CURSOR123ABC&X-Amz-Signature=" + sig +
				"&client_secret=" + secret + "&api-key=" + apiKey + "&nextPageToken=NEXT456DEF&sync_token=SYNC789"},
		Response: HTTPResponse{Status: 200,
			Headers: hdr("Link", "<https://api.example.test/v1/items?page=2&access_token="+inLink+">; rel=\"next\""),
			Body: jsonBody(map[string]any{"paging": map[string]string{
				"next": "https://graph.example.test/v1/feed?access_token=" + inBody + "&after=CURSORXYZ"}})},
	})
	text := saveOK(t, c, ScrubConfig{})
	assertAbsent(t, text, access, key, sig, secret, apiKey, inBody, inLink)
	assertPresent(t, text, "q=from%3Ame", "pageToken=CURSOR123ABC", "nextPageToken=NEXT456DEF", "sync_token=SYNC789", "after=CURSORXYZ")
	got := loadSaved(t, text).HTTP.Exchanges[0]
	for _, p := range []string{"access_token=scrubbed-token-", "key=scrubbed-token-", "X-Amz-Signature=scrubbed-token-",
		"client_secret=scrubbed-token-", "api-key=scrubbed-token-"} {
		if !strings.Contains(got.Request.Query, p) {
			t.Errorf("query %q lacks %s", got.Request.Query, p)
		}
	}
}

// TestScrubStripsOAuthCodesAndTokens: an authorization code in a redirect
// (with or without state), the token request's form fields, the token
// response's tokens, a refresh and an implicit-flow fragment are all
// scrubbed, with one placeholder per value across the whole flow; ?code=
// outside OAuth is data.
func TestScrubStripsOAuthCodesAndTokens(t *testing.T) {
	clientID, state, code, verifier, clientSecret := fakeToken("cid"), fakeToken("state"), fakeToken("code"),
		fakeToken("verifier"), fakeToken("csecret")
	access, refresh, idToken, access2, implicit, state2, pkceCode := fakeToken("at"), fakeToken("rt"), fakeToken("idt"),
		fakeToken("at2"), fakeToken("implicit"), fakeToken("state2"), fakeToken("pkce")
	form := hdr("Content-Type", "application/x-www-form-urlencoded")
	jsonCT := hdr("Content-Type", "application/json")
	c := newHTTPCassette(
		HTTPExchange{
			Request: HTTPRequest{Method: "GET", Path: "/authorize", Query: "response_type=code&client_id=" + clientID +
				"&redirect_uri=http%3A%2F%2F127.0.0.1%3A9%2Fcb&state=" + state + "&code_challenge_method=S256"},
			Response: HTTPResponse{Status: 302, Headers: hdr("Location", "http://127.0.0.1:9/cb?code="+code+"&state="+state)}},
		HTTPExchange{
			Request: HTTPRequest{Method: "POST", Path: "/token", Headers: form, Body: &Body{Text: "grant_type=authorization_code&code=" +
				code + "&redirect_uri=http%3A%2F%2F127.0.0.1%3A9%2Fcb&code_verifier=" + verifier + "&client_id=" + clientID +
				"&client_secret=" + clientSecret}},
			Response: HTTPResponse{Status: 200, Headers: jsonCT, Body: jsonBody(map[string]any{"access_token": access,
				"refresh_token": refresh, "id_token": idToken, "token_type": "Bearer", "expires_in": 3600, "scope": "repo read:org"})}},
		HTTPExchange{
			Request:  HTTPRequest{Method: "POST", Path: "/token", Headers: form, Body: &Body{Text: "grant_type=refresh_token&refresh_token=" + refresh}},
			Response: HTTPResponse{Status: 200, Headers: jsonCT, Body: jsonBody(map[string]any{"access_token": access2})}},
		HTTPExchange{
			Request: HTTPRequest{Method: "GET", Path: "/authorize", Query: "response_type=token"},
			Response: HTTPResponse{Status: 302, Headers: hdr("Location",
				"https://client.example.test/cb#access_token="+implicit+"&token_type=bearer&state="+state2)}},
		HTTPExchange{
			Request:  HTTPRequest{Method: "GET", Path: "/authorize", Query: "response_type=code"},
			Response: HTTPResponse{Status: 302, Headers: hdr("Location", "/cb?code="+pkceCode)}},
		HTTPExchange{
			Request:  HTTPRequest{Method: "GET", Path: "/v1/countries", Query: "code=US"},
			Response: HTTPResponse{Status: 200, Headers: jsonCT, Body: jsonBody(map[string]string{"code": "US"})}},
	)
	text := saveOK(t, c, ScrubConfig{})
	assertAbsent(t, text, clientID, state, code, verifier, clientSecret, access, refresh, idToken, access2, implicit, state2, pkceCode)
	assertPresent(t, text, `"token_type": "Bearer"`, `"expires_in": 3600`, `"scope": "repo read:org"`, "code=US", "token_type=bearer")
	got := loadSaved(t, text).HTTP.Exchanges
	codeInRedirect := paramOf(got[0].Response.Headers.Get("Location"), "code")
	codeInForm := paramOf("?"+got[1].Request.Body.Text, "code")
	if !strings.HasPrefix(codeInRedirect, "scrubbed-token-") || codeInRedirect != codeInForm {
		t.Errorf("the code is %q in the redirect and %q in the token request: want one placeholder", codeInRedirect, codeInForm)
	}
	refreshInResponse := compactJSON(t, got[1].Response.Body.JSON)
	refreshInForm := paramOf("?"+got[2].Request.Body.Text, "refresh_token")
	if !strings.Contains(refreshInResponse, `"refresh_token":"`+refreshInForm+`"`) {
		t.Errorf("the refresh token lost its link: form %q, response %s", refreshInForm, refreshInResponse)
	}
}

func paramOf(u, name string) string {
	_, q, _ := strings.Cut(u, "?")
	for _, part := range strings.Split(q, "&") {
		if k, v, ok := strings.Cut(part, "="); ok && k == name {
			return v
		}
	}
	return ""
}

// TestScrubRefusesLeftoverCredentialShape: a credential shape no rule can
// place (free text, an ordinary key, a PEM block, Base64, a JSON escape)
// refuses the whole cassette: no file and no temporary file, and the
// refusal names rule and place, never the value. The same shape at a
// credential location is scrubbed, not refused.
func TestScrubRefusesLeftoverCredentialShape(t *testing.T) {
	pat, aws, slack := fakeShape("github-pat"), fakeShape("aws-access-key-id"), fakeShape("slack-token")
	pemBlock := fakeShape("private-key-pem") + "\nMIIEow" + strings.Repeat("A", 40) + "\n-----END RSA PRIVATE KEY-----"
	cases := map[string]struct {
		c        *Cassette
		wantRule string
	}{
		"free text in a tool result": {newMCPCassette(MCPExchange{Method: "tools/call",
			Params: raw(`{"name":"read_file","arguments":{"path":"README.md"}}`),
			Result: raw(`{"content":[{"type":"text","text":"export GITHUB_TOKEN=` + pat + `"}]}`)}), "github-pat"},
		"an ordinary json key": {newHTTPCassette(HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/notes"},
			Response: HTTPResponse{Status: 200, Body: jsonBody(map[string]string{"note": "rotate " + aws})}}), "aws-access-key-id"},
		"a pem block in a text body": {newHTTPCassette(HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/key"},
			Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "text/plain"), Body: &Body{Text: pemBlock}}}), "private-key-pem"},
		"base64 inside json": {newHTTPCassette(HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/blob"},
			Response: HTTPResponse{Status: 200, Body: jsonBody(map[string]string{
				"data": base64.StdEncoding.EncodeToString([]byte("config: " + slack + " end of config"))})}}), "slack-token"},
		"a json unicode escape": {newMCPCassette(MCPExchange{Method: "tools/call", Params: raw(`{"name":"x"}`),
			Result: raw(`{"content":[{"type":"text","text":"` + jsonEscapeFirst(fakeShape("anthropic-key"), '-') + `"}]}`)}), "anthropic-key"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			refused := saveRefused(t, tc.c, ScrubConfig{})
			msg := refused.Error()
			if !strings.Contains(msg, tc.wantRule) || !strings.Contains(msg, " at ") {
				t.Errorf("refusal %q does not name rule %s and a place", msg, tc.wantRule)
			}
			for _, secret := range []string{pat, aws, slack, "MIIEow", fakeShape("anthropic-key")[7:]} {
				if strings.Contains(msg, secret) {
					t.Errorf("the refusal message carries a value")
				}
			}
		})
	}
	t.Run("the same shape at a credential location is scrubbed, not refused", func(t *testing.T) {
		c := newHTTPCassette(HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/user",
			Headers: hdr("Authorization", "token "+pat)}, Response: HTTPResponse{Status: 200}})
		text := saveOK(t, c, ScrubConfig{})
		assertAbsent(t, text, pat)
	})
}
