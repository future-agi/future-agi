package mcpmock

import (
	"encoding/json"
	"net/http"
	"net/url"
	"strings"
	"testing"
)

const (
	testClientID     = "cn24-test-registered-client"
	testClientSecret = "cn24-test-registered-secret-not-real"
)

// basicTokenCall posts form to /token as client id with secret by HTTP Basic
// (RFC 6749 2.3.1: both form-urlencoded).
func basicTokenCall(t *testing.T, base, id, secret string, form url.Values) (int, map[string]any) {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, base+"/token", strings.NewReader(form.Encode()))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.SetBasicAuth(url.QueryEscape(id), url.QueryEscape(secret))
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	out := map[string]any{}
	_ = json.NewDecoder(resp.Body).Decode(&out)
	return resp.StatusCode, out
}

// codeFor runs a whole authorization request for client id and returns the
// code, its PKCE verifier and the resource.
func codeFor(t *testing.T, m *Mock, base, id string) (code, verifier, resource string) {
	t.Helper()
	resource = m.resourceURI()
	verifier, challenge := pkcePair()
	q := authQuery(id, resource, challenge, "S256")
	return authorizeRedirect(t, base, q).Get("code"), verifier, resource
}

// A client registered with the mock in advance proves its secret on every
// token request by exactly its registered method, never both and never without
// it; a wrong secret is refused. Public clients are explicitly registered with
// PKCE and a public client that sends a secret is refused. The metadata names the
// methods it takes.
func TestOAuthConfidentialClientMustProveItsSecret(t *testing.T) {
	m, srv := testMockWith(t, "dual", Config{Auth: "oauth", OAuthClients: staticClientFixtures()})
	var as map[string]any
	getJSON(t, srv.URL+"/.well-known/oauth-authorization-server", &as)
	methods, _ := json.Marshal(as["token_endpoint_auth_methods_supported"])
	if string(methods) != `["none","client_secret_post","client_secret_basic"]` {
		t.Fatalf("token_endpoint_auth_methods_supported = %s", methods)
	}

	redeem := func(code, verifier, resource, id string) url.Values {
		return url.Values{"grant_type": {"authorization_code"}, "code": {code}, "client_id": {id},
			"redirect_uri": {testRedirect}, "code_verifier": {verifier}, "resource": {resource}}
	}
	for name, try := range map[string]func(code, verifier, resource string) (int, map[string]any){
		"no secret": func(c, v, r string) (int, map[string]any) {
			return tokenCall(t, srv.URL, redeem(c, v, r, testClientID))
		},
		"wrong secret in the form": func(c, v, r string) (int, map[string]any) {
			f := redeem(c, v, r, testClientID)
			f.Set("client_secret", "wrong-"+testClientSecret)
			return tokenCall(t, srv.URL, f)
		},
		"wrong secret by basic": func(c, v, r string) (int, map[string]any) {
			return basicTokenCall(t, srv.URL, testClientID, "wrong-"+testClientSecret, redeem(c, v, r, testClientID))
		},
		"both methods": func(c, v, r string) (int, map[string]any) {
			f := redeem(c, v, r, testClientID)
			f.Set("client_secret", testClientSecret)
			return basicTokenCall(t, srv.URL, testClientID, testClientSecret, f)
		},
	} {
		code, verifier, resource := codeFor(t, m, srv.URL, testClientID)
		if status, out := try(code, verifier, resource); status != http.StatusUnauthorized || out["error"] != "invalid_client" {
			t.Fatalf("%s: %d %v, want 401 invalid_client", name, status, out)
		}
	}

	code, verifier, resource := codeFor(t, m, srv.URL, testBasicClientID)
	status, tok := basicTokenCall(t, srv.URL, testBasicClientID, testClientSecret, redeem(code, verifier, resource, testBasicClientID))
	if status != http.StatusOK || tok["refresh_token"] == nil {
		t.Fatalf("by basic: %d %v", status, tok)
	}
	basicRefresh := url.Values{"grant_type": {"refresh_token"}, "refresh_token": {tok["refresh_token"].(string)},
		"client_id": {testBasicClientID}, "resource": {resource}}
	if status, out := basicTokenCall(t, srv.URL, testBasicClientID, testClientSecret, basicRefresh); status != http.StatusOK || out["access_token"] == nil {
		t.Fatalf("basic refresh: %d %v", status, out)
	}
	code, verifier, resource = codeFor(t, m, srv.URL, testClientID)
	post := redeem(code, verifier, resource, testClientID)
	post.Set("client_secret", testClientSecret)
	status, tok = tokenCall(t, srv.URL, post)
	if status != http.StatusOK || tok["refresh_token"] == nil {
		t.Fatalf("by post: %d %v", status, tok)
	}
	refresh := url.Values{"grant_type": {"refresh_token"}, "refresh_token": {tok["refresh_token"].(string)},
		"client_id": {testClientID}, "client_secret": {testClientSecret}, "resource": {resource}}
	if status, out := tokenCall(t, srv.URL, refresh); status != http.StatusOK || out["access_token"] == nil {
		t.Fatalf("a refresh with the secret in the form: %d %v", status, out)
	}
	unproved := url.Values{"grant_type": {"refresh_token"}, "refresh_token": {"anything"}, "client_id": {testClientID}}
	if status, out := tokenCall(t, srv.URL, unproved); status != http.StatusUnauthorized || out["error"] != "invalid_client" {
		t.Fatalf("a refresh without the secret: %d %v", status, out)
	}

	code, verifier, resource = codeFor(t, m, srv.URL, "client-1")
	if status, out := tokenCall(t, srv.URL, redeem(code, verifier, resource, "client-1")); status != http.StatusOK {
		t.Fatalf("a public client: %d %v", status, out)
	}
	code, verifier, resource = codeFor(t, m, srv.URL, "client-1")
	f := redeem(code, verifier, resource, "client-1")
	f.Set("client_secret", testClientSecret)
	if status, out := tokenCall(t, srv.URL, f); status != http.StatusUnauthorized || out["error"] != "invalid_client" {
		t.Fatalf("a public client with a secret: %d %v", status, out)
	}

	// With no clients registered, capabilities stay advertised but no unknown client authenticates.
	_, plain := testMock(t, "dual", "oauth")
	getJSON(t, plain.URL+"/.well-known/oauth-authorization-server", &as)
	if methods, _ := json.Marshal(as["token_endpoint_auth_methods_supported"]); string(methods) != `["none","client_secret_post","client_secret_basic"]` {
		t.Fatalf("with no registered client: %s", methods)
	}
}

const testBasicClientID = "cn24-test-basic-client"

func staticClientFixtures() map[string]OAuthClient {
	client := func(method, secret string) OAuthClient {
		return OAuthClient{Secret: secret, RedirectURIs: []string{testRedirect}, AuthMethod: method,
			GrantTypes: []string{"authorization_code", "refresh_token"}, ResponseTypes: []string{"code"}}
	}
	return map[string]OAuthClient{
		testClientID:      client("client_secret_post", testClientSecret),
		testBasicClientID: client("client_secret_basic", testClientSecret),
		"client-1":        client("none", ""),
	}
}

func TestPreRegisteredClientMethodIsolation(t *testing.T) {
	m, srv := testMockWith(t, "dual", Config{Auth: "oauth", OAuthClients: staticClientFixtures()})
	for _, id := range []string{testClientID, testBasicClientID} {
		code, verifier, resource := codeFor(t, m, srv.URL, id)
		form := url.Values{"grant_type": {"authorization_code"}, "code": {code}, "client_id": {id},
			"redirect_uri": {testRedirect}, "code_verifier": {verifier}, "resource": {resource}}
		var status int
		var out map[string]any
		if id == testClientID {
			status, out = basicTokenCall(t, srv.URL, id, testClientSecret, form)
		} else {
			form.Set("client_secret", testClientSecret)
			status, out = tokenCall(t, srv.URL, form)
		}
		if status != http.StatusUnauthorized || out["error"] != "invalid_client" || out["access_token"] != nil {
			t.Fatalf("wrong registered method: %d, issued token: %t", status, out["access_token"] != nil)
		}
	}
}

func TestPreRegisteredFixturesNeverAdmitUnregisteredPublicClient(t *testing.T) {
	m, srv := testMockWith(t, "dual", Config{Auth: "oauth", OAuthClients: staticClientFixtures()})
	verifier, challenge := pkcePair()
	q := authQuery("unregistered-public-control", m.resourceURI(), challenge, "S256")
	resp, err := noRedirect.Get(srv.URL + "/authorize?" + q.Encode())
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest || resp.Header.Get("Location") != "" {
		t.Fatalf("unregistered public authorization: %d, redirected: %t", resp.StatusCode, resp.Header.Get("Location") != "")
	}
	status, _, headers := formPost(t, srv.URL, "/authorize", withValues(q, "decision", "approve"))
	if status != http.StatusBadRequest || headers.Get("Location") != "" {
		t.Fatal("unregistered public approval issued a redirect")
	}
	status, out := tokenCall(t, srv.URL, url.Values{"grant_type": {"authorization_code"}, "code": {"no-code"},
		"client_id": {"unregistered-public-control"}, "redirect_uri": {testRedirect}, "code_verifier": {verifier}})
	if status != http.StatusUnauthorized || out["error"] != "invalid_client" || out["access_token"] != nil {
		t.Fatalf("unregistered public token: %d, issued token: %t", status, out["access_token"] != nil)
	}
	m.oauth.mu.Lock()
	defer m.oauth.mu.Unlock()
	if len(m.oauth.codes) != 0 || len(m.oauth.access) != 0 || len(m.oauth.refresh) != 0 {
		t.Fatal("refused unregistered client mutated issuance state")
	}
}

func TestPreRegisteredPublicClientStillRequiresPKCEAndExactRedirect(t *testing.T) {
	m, srv := testMockWith(t, "dual", Config{Auth: "oauth", OAuthClients: staticClientFixtures()})
	for _, method := range []string{"", "plain"} {
		q := authQuery("client-1", m.resourceURI(), "", method)
		resp, err := noRedirect.Get(srv.URL + "/authorize?" + q.Encode())
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode == http.StatusOK || strings.Contains(resp.Header.Get("Location"), "code=") {
			t.Fatal("public client without S256 PKCE was admitted")
		}
	}
	_, challenge := pkcePair()
	q := authQuery("client-1", m.resourceURI(), challenge, "S256")
	q.Set("redirect_uri", testRedirect+"/unregistered")
	resp, err := noRedirect.Get(srv.URL + "/authorize?" + q.Encode())
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest || resp.Header.Get("Location") != "" {
		t.Fatal("pre-registered client admitted an unregistered redirect")
	}
}

func TestPreRegisteredMetadataIsExplicitAndCopied(t *testing.T) {
	for name, mutate := range map[string]func(*OAuthClient){
		"redirect missing": func(c *OAuthClient) { c.RedirectURIs = nil },
		"redirect invalid": func(c *OAuthClient) { c.RedirectURIs = []string{"javascript:alert(1)"} },
		"auth missing":     func(c *OAuthClient) { c.AuthMethod = "" },
		"auth invalid":     func(c *OAuthClient) { c.AuthMethod = "client_secret_jwt" },
		"secret missing":   func(c *OAuthClient) { c.Secret = "" },
		"public secret":    func(c *OAuthClient) { c.AuthMethod = "none" },
		"grant missing":    func(c *OAuthClient) { c.GrantTypes = nil },
		"grant invalid":    func(c *OAuthClient) { c.GrantTypes = []string{"password"} },
		"response missing": func(c *OAuthClient) { c.ResponseTypes = nil },
		"response invalid": func(c *OAuthClient) { c.ResponseTypes = []string{"token"} },
	} {
		clients := staticClientFixtures()
		c := clients[testClientID]
		mutate(&c)
		clients[testClientID] = c
		if _, err := New(Config{Auth: "oauth", OAuthClients: clients}, &Fixtures{}); err == nil {
			t.Errorf("accepted %s metadata", name)
		}
	}
	clients := staticClientFixtures()
	m, err := New(Config{Auth: "oauth", OAuthClients: clients}, &Fixtures{})
	if err != nil {
		t.Fatal(err)
	}
	clients[testClientID].RedirectURIs[0] = "http://127.0.0.1:9/not-registered"
	clients[testClientID].GrantTypes[0] = "password"
	if !m.registeredRedirect(testClientID, testRedirect) || m.registeredRedirect(testClientID, clients[testClientID].RedirectURIs[0]) || m.clientGrantAllowed(testClientID, "password") {
		t.Fatal("caller mutation altered registered metadata")
	}
}

func TestPreRegisteredConfidentialMethodsEachCheckTheSecretWithoutConsumingCode(t *testing.T) {
	m, srv := testMockWith(t, "dual", Config{Auth: "oauth", OAuthClients: staticClientFixtures()})
	for _, id := range []string{testClientID, testBasicClientID} {
		for _, wrong := range []string{"", "wrong-fixture-secret"} {
			code, verifier, resource := codeFor(t, m, srv.URL, id)
			form := url.Values{"grant_type": {"authorization_code"}, "code": {code}, "client_id": {id},
				"redirect_uri": {testRedirect}, "code_verifier": {verifier}, "resource": {resource}}
			call := func(secret string) (int, map[string]any) {
				if id == testBasicClientID {
					return basicTokenCall(t, srv.URL, id, secret, form)
				}
				form.Set("client_secret", secret)
				return tokenCall(t, srv.URL, form)
			}
			if status, out := call(wrong); status != http.StatusUnauthorized || out["error"] != "invalid_client" || out["access_token"] != nil {
				t.Fatalf("registered method accepted a missing/wrong secret: %d", status)
			}
			if status, out := call(testClientSecret); status != http.StatusOK || out["access_token"] == nil || out["refresh_token"] == nil {
				t.Fatalf("refused authentication consumed the valid code: %d", status)
			}
		}
	}
}
