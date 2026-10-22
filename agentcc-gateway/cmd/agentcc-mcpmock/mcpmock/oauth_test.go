package mcpmock

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strings"
	"testing"
)

const testRedirect = "http://127.0.0.1:9/callback"

var noRedirect = &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}

func pkcePair() (verifier, challenge string) {
	verifier = strings.Repeat("v", 50)
	sum := sha256.Sum256([]byte(verifier))
	return verifier, base64.RawURLEncoding.EncodeToString(sum[:])
}

func authQuery(resource, challenge, method string) url.Values {
	q := url.Values{"response_type": {"code"}, "client_id": {"client-1"}, "redirect_uri": {testRedirect},
		"state": {"st-1"}, "scope": {"mcp"}, "resource": {resource}}
	if challenge != "" {
		q.Set("code_challenge", challenge)
	}
	if method != "" {
		q.Set("code_challenge_method", method)
	}
	return q
}

// authorizeRedirect runs /authorize (GET, then the consent form's approve
// POST when the GET shows the page) and returns the redirect's query.
func authorizeRedirect(t *testing.T, base string, q url.Values) url.Values {
	t.Helper()
	resp, err := noRedirect.Get(base + "/authorize?" + q.Encode())
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode == http.StatusOK {
		form := url.Values{}
		for k, v := range q {
			form[k] = v
		}
		form.Set("decision", "approve")
		if resp, err = noRedirect.PostForm(base+"/authorize", form); err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
	}
	if resp.StatusCode != http.StatusFound {
		t.Fatalf("/authorize = %d, want a 302", resp.StatusCode)
	}
	loc, err := url.Parse(resp.Header.Get("Location"))
	if err != nil || !strings.HasPrefix(loc.String(), testRedirect) {
		t.Fatalf("redirect to %q, want %s", resp.Header.Get("Location"), testRedirect)
	}
	return loc.Query()
}

func tokenCall(t *testing.T, base string, form url.Values) (int, map[string]any) {
	t.Helper()
	resp, err := http.PostForm(base+"/token", form)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	out := map[string]any{}
	_ = json.NewDecoder(resp.Body).Decode(&out)
	return resp.StatusCode, out
}

func toolsListWith(t *testing.T, base, token string) httpReply {
	t.Helper()
	return call2026(t, base+"/mcp", "tools/list", nil, "Authorization: Bearer "+token)
}

// TestOAuthRefusesForeignAudienceAndPlainPKCE is the acceptance check: a token
// minted for another audience is refused with 401 and the PRM in
// WWW-Authenticate; an authorization request without S256 (none, or plain)
// gets an error redirect and no code.
func TestOAuthRefusesForeignAudienceAndPlainPKCE(t *testing.T) {
	m, srv := testMock(t, "2026", "oauth")
	resource := m.resourceURI()

	foreign := postControl(t, srv.URL, "/__control/oauth/token", `{"aud":"http://other.example/mcp"}`)["access_token"].(string)
	r := toolsListWith(t, srv.URL, foreign)
	if r.status != http.StatusUnauthorized || !strings.Contains(r.header.Get("WWW-Authenticate"), `resource_metadata="`+m.prmURL()+`"`) ||
		!strings.Contains(r.header.Get("WWW-Authenticate"), "invalid_token") {
		t.Fatalf("a foreign-audience token = %d %q", r.status, r.header.Get("WWW-Authenticate"))
	}
	own := postControl(t, srv.URL, "/__control/oauth/token", `{}`)["access_token"].(string)
	if r := toolsListWith(t, srv.URL, own); r.status != http.StatusOK {
		t.Fatalf("an own-audience token = %d %s", r.status, r.body)
	}
	expired := postControl(t, srv.URL, "/__control/oauth/token", `{"ttl_s":-1}`)["access_token"].(string)
	if r := toolsListWith(t, srv.URL, expired); r.status != http.StatusUnauthorized {
		t.Fatalf("an expired token = %d", r.status)
	}
	if r := call2026(t, srv.URL+"/mcp", "tools/list", nil); r.status != http.StatusUnauthorized ||
		!strings.Contains(r.header.Get("WWW-Authenticate"), "resource_metadata=") {
		t.Fatalf("no token = %d %q", r.status, r.header.Get("WWW-Authenticate"))
	}

	_, challenge := pkcePair()
	for name, q := range map[string]url.Values{
		"no PKCE":      authQuery(resource, "", ""),
		"plain PKCE":   authQuery(resource, challenge, "plain"),
		"no method":    authQuery(resource, challenge, ""),
		"wrong target": authQuery("http://other.example/mcp", challenge, "S256"),
		"bad resp type": func() url.Values {
			v := authQuery(resource, challenge, "S256")
			v.Set("response_type", "token")
			return v
		}(),
	} {
		got := authorizeRedirect(t, srv.URL, q)
		if got.Get("code") != "" || got.Get("error") == "" || got.Get("iss") != srv.URL || got.Get("state") != "st-1" {
			t.Fatalf("%s: redirect query %v, want an error with iss and state and no code", name, got)
		}
	}
	// A bad redirect_uri is never redirected to.
	q := authQuery(resource, challenge, "S256")
	q.Set("redirect_uri", "http://evil.example/cb")
	resp, err := noRedirect.Get(srv.URL + "/authorize?" + q.Encode())
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("a non-loopback http redirect_uri = %d, want 400", resp.StatusCode)
	}
}

// TestOAuthCodeFlowAndRefreshRotation: the full flow works end to end, a code
// is single-use and PKCE-bound, and a refresh token rotates; reusing a
// rotated one revokes the family.
func TestOAuthCodeFlowAndRefreshRotation(t *testing.T) {
	m, srv := testMock(t, "dual", "oauth")
	resource := m.resourceURI()
	var prm, as map[string]any
	getJSON(t, srv.URL+"/.well-known/oauth-protected-resource/mcp", &prm)
	getJSON(t, srv.URL+"/.well-known/oauth-authorization-server", &as)
	if prm["resource"] != resource || prm["authorization_servers"].([]any)[0] != srv.URL {
		t.Fatalf("PRM = %v", prm)
	}
	if as["issuer"] != srv.URL || as["code_challenge_methods_supported"].([]any)[0] != "S256" ||
		len(as["code_challenge_methods_supported"].([]any)) != 1 || as["authorization_response_iss_parameter_supported"] != true {
		t.Fatalf("AS metadata = %v", as)
	}

	verifier, challenge := pkcePair()
	got := authorizeRedirect(t, srv.URL, authQuery(resource, challenge, "S256"))
	code := got.Get("code")
	if code == "" || got.Get("iss") != srv.URL || got.Get("state") != "st-1" {
		t.Fatalf("approve redirect = %v", got)
	}
	redeem := url.Values{"grant_type": {"authorization_code"}, "code": {code}, "client_id": {"client-1"},
		"redirect_uri": {testRedirect}, "code_verifier": {"wrong-" + verifier}, "resource": {resource}}
	if status, out := tokenCall(t, srv.URL, redeem); status != 400 || out["error"] != "invalid_grant" {
		t.Fatalf("a wrong verifier = %d %v", status, out)
	}
	// The failed redemption used the code up.
	redeem.Set("code_verifier", verifier)
	if status, _ := tokenCall(t, srv.URL, redeem); status != 400 {
		t.Fatal("a code redeemed twice was accepted")
	}

	got = authorizeRedirect(t, srv.URL, authQuery(resource, challenge, "S256"))
	redeem.Set("code", got.Get("code"))
	status, tok := tokenCall(t, srv.URL, redeem)
	if status != 200 || tok["access_token"] == nil || tok["refresh_token"] == nil {
		t.Fatalf("token = %d %v", status, tok)
	}
	if r := toolsListWith(t, srv.URL, tok["access_token"].(string)); r.status != 200 {
		t.Fatalf("the issued token = %d %s", r.status, r.body)
	}

	refresh := url.Values{"grant_type": {"refresh_token"}, "refresh_token": {tok["refresh_token"].(string)}, "client_id": {"client-1"}}
	// A refresh for another resource is refused without using the token up:
	// the correct retry below still rotates instead of counting as a reuse.
	refresh.Set("resource", "http://127.0.0.1:9/other")
	if status, out := tokenCall(t, srv.URL, refresh); status != 400 || out["error"] != "invalid_target" {
		t.Fatalf("a refresh for another resource = %d %v", status, out)
	}
	refresh.Set("resource", resource)
	status, tok2 := tokenCall(t, srv.URL, refresh)
	if status != 200 || tok2["refresh_token"] == tok["refresh_token"] {
		t.Fatalf("refresh = %d %v (must rotate)", status, tok2)
	}
	if status, out := tokenCall(t, srv.URL, refresh); status != 400 || out["error"] != "invalid_grant" {
		t.Fatalf("a reused refresh token = %d %v", status, out)
	}
	refresh.Set("refresh_token", tok2["refresh_token"].(string))
	if status, _ := tokenCall(t, srv.URL, refresh); status != 400 {
		t.Fatal("the family survived a refresh-token reuse")
	}
}

// TestBearerModeAndReceivedHidesCredential: bearer mode refuses a missing or
// wrong token; /__received keeps the Authorization header's presence and
// hash, never its value, and the request's _meta.
func TestBearerModeAndReceivedHidesCredential(t *testing.T) {
	_, srv := testMock(t, "2026", "bearer:s3cret-upstream-token")
	if r := call2026(t, srv.URL+"/mcp", "tools/list", nil, "Authorization: Bearer nope"); r.status != 401 {
		t.Fatalf("a wrong bearer = %d", r.status)
	}
	params := map[string]any{"_meta": map[string]any{"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}}
	if r := call2026(t, srv.URL+"/mcp", "tools/list", params, "Authorization: Bearer s3cret-upstream-token", "X-Api-Key: k-123"); r.status != 200 {
		t.Fatalf("the right bearer = %d %s", r.status, r.body)
	}
	resp, err := http.Get(srv.URL + "/__received?last=1")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var raw json.RawMessage
	_ = json.NewDecoder(resp.Body).Decode(&raw)
	if strings.Contains(string(raw), "s3cret") || strings.Contains(string(raw), "k-123") {
		t.Fatalf("/__received holds a credential value: %s", raw)
	}
	var out struct {
		Received []Received `json:"received"`
	}
	_ = json.Unmarshal(raw, &out)
	rec := out.Received[0]
	sum := sha256.Sum256([]byte("Bearer s3cret-upstream-token"))
	if !rec.Auth.Present || rec.Auth.SHA256 != hex.EncodeToString(sum[:]) || !rec.Credentials["x-api-key"].Present {
		t.Fatalf("auth seen = %+v, credentials %+v", rec.Auth, rec.Credentials)
	}
	if rec.Method != "tools/list" || !strings.Contains(string(rec.Meta), "traceparent") {
		t.Fatalf("record = %+v", rec)
	}
}

// TestReceivedHashesAnyCredentialHeader: the gateway's api_key auth sends its
// key in whatever header the config names. /__received keeps such a header
// as presence and SHA-256 only, whether a credential word in its name or
// --credential-header marks it; --auth-header makes it the auth entry.
// Mcp-Session-Id and Idempotency-Key stay verbatim.
func TestReceivedHashesAnyCredentialHeader(t *testing.T) {
	_, srv := testMockWith(t, "2026", Config{AuthHeader: "X-Upstream-Token", CredentialHeaders: []string{"X-Tenant-Pass"}})
	r := call2026(t, srv.URL+"/mcp", "tools/list", nil, "X-Upstream-Token: SUPER-SECRET-VALUE",
		"X-Goog-Api-Key: GOOGKEY123", "X-Tenant-Pass: TENANTVAL", "Authorization: Bearer BEARERVAL",
		"Idempotency-Key: idem-1", "X-Trace: trace-1")
	if r.status != 200 {
		t.Fatalf("tools/list = %d %s", r.status, r.body)
	}
	resp, err := http.Get(srv.URL + "/__received?last=1")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	for _, secret := range []string{"SUPER-SECRET-VALUE", "GOOGKEY123", "TENANTVAL", "BEARERVAL"} {
		if strings.Contains(string(raw), secret) {
			t.Fatalf("/__received holds the credential value %q: %s", secret, raw)
		}
	}
	var out struct {
		Received []Received `json:"received"`
	}
	_ = json.Unmarshal(raw, &out)
	rec := out.Received[0]
	sum := sha256.Sum256([]byte("SUPER-SECRET-VALUE"))
	if !rec.Auth.Present || rec.Auth.SHA256 != hex.EncodeToString(sum[:]) {
		t.Fatalf("auth = %+v, want the X-Upstream-Token's presence and hash", rec.Auth)
	}
	for _, name := range []string{"x-goog-api-key", "x-tenant-pass", "authorization"} {
		if !rec.Credentials[name].Present || rec.Credentials[name].SHA256 == "" {
			t.Fatalf("credentials[%s] = %+v (all: %+v)", name, rec.Credentials[name], rec.Credentials)
		}
	}
	if rec.Headers["idempotency-key"] != "idem-1" || rec.Headers["x-trace"] != "trace-1" ||
		rec.Headers["mcp-protocol-version"] == "" {
		t.Fatalf("verbatim headers = %v", rec.Headers)
	}
	if _, err := New(Config{CredentialHeaders: []string{"Bad: name"}}, &Fixtures{}); err == nil {
		t.Fatal("a --credential-header with a colon was accepted")
	}
}
