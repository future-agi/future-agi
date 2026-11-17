package mcpmock

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"slices"
	"strings"
	"testing"
)

func TestRegistrationRevokeAndPerUser(t *testing.T) {
	m, srv := testMock(t, "2026", "oauth")
	response, err := http.Post(srv.URL+"/register", "application/json", strings.NewReader(`{"redirect_uris":["`+testRedirect+`"],"token_endpoint_auth_method":"none","grant_types":["authorization_code","refresh_token"]}`))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	var client registration
	if err := json.NewDecoder(response.Body).Decode(&client); err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != 201 || client.ClientID == "" || client.Secret != "" {
		t.Fatalf("bad registration: %+v", client)
	}
	verifier, challenge := pkcePair()
	issued := map[string]map[string]any{}
	for _, sub := range []string{"member-a1", "member-a2"} {
		q := authQuery(client.ClientID, m.resourceURI(), challenge, "S256")
		q.Set("login_hint", sub)
		out := authorizeRedirect(t, srv.URL, q)
		status, token := tokenCall(t, srv.URL, url.Values{"grant_type": {"authorization_code"}, "code": {out.Get("code")}, "client_id": {client.ClientID}, "redirect_uri": {testRedirect}, "code_verifier": {verifier}})
		if status != 200 {
			t.Fatalf("token: %d %v", status, token)
		}
		actual, why := m.oauth.check(token["access_token"].(string), m.resourceURI())
		if actual != sub || why != "" {
			t.Fatalf("subject=%q why=%q", actual, why)
		}
		issued[sub] = token
	}
	if issued["member-a1"]["access_token"] == issued["member-a2"]["access_token"] {
		t.Fatal("shared user token")
	}
	revoke := func(tok string) {
		t.Helper()
		r, err := http.PostForm(srv.URL+"/revoke", url.Values{"client_id": {client.ClientID}, "token": {tok}, "token_type_hint": {"refresh_token"}})
		if err != nil {
			t.Fatal(err)
		}
		r.Body.Close()
		if r.StatusCode != 200 {
			t.Fatalf("revoke=%d", r.StatusCode)
		}
	}
	revoke(issued["member-a1"]["refresh_token"].(string))
	revoke("unknown-fixture")
	if r := toolsListWith(t, srv.URL, issued["member-a1"]["access_token"].(string)); r.status != 401 {
		t.Fatal("revoked access still works")
	}
	if r := toolsListWith(t, srv.URL, issued["member-a2"]["access_token"].(string)); r.status != 200 {
		t.Fatal("other subject revoked")
	}
	recorder := httptest.NewRecorder()
	m.oauthReceipts(recorder, httptest.NewRequest("GET", "/", nil))
	var receipts struct {
		Registrations []registration `json:"registrations"`
		Revocations   []revocation   `json:"revocations"`
	}
	if json.Unmarshal(recorder.Body.Bytes(), &receipts) != nil || len(receipts.Registrations) != 1 || len(receipts.Revocations) != 2 || receipts.Revocations[0].Subject != "member-a1" {
		t.Fatalf("receipts: %s", recorder.Body.String())
	}
	if strings.Contains(recorder.Body.String(), issued["member-a1"]["refresh_token"].(string)) {
		t.Fatal("raw token in receipt")
	}
	q := authQuery(client.ClientID, m.resourceURI(), challenge, "S256")
	q.Set("redirect_uri", "http://localhost:9/foreign")
	if code, _, redirectable := m.validateAuth(readAuthParams(q)); code == "" || redirectable {
		t.Fatal("DCR redirect mismatch allowed")
	}
}

func TestRegistrationValidationAndMetadata(t *testing.T) {
	m, _ := testMock(t, "2026", "oauth")
	for _, body := range []string{`{}`, `{"redirect_uris":["file:///x"]}`, `{"redirect_uris":["http://127.0.0.1/cb"],"token_endpoint_auth_method":"unsupported"}`, `{"redirect_uris":["http://localhost/cb"],"grant_types":["password"]}`} {
		w := httptest.NewRecorder()
		m.serveRegister(w, httptest.NewRequest("POST", "/register", strings.NewReader(body)))
		if w.Code != 400 {
			t.Fatalf("accepted %s", body)
		}
	}
	w := httptest.NewRecorder()
	m.Handler().ServeHTTP(w, httptest.NewRequest("GET", "/.well-known/oauth-authorization-server", nil))
	for _, endpoint := range []string{"registration_endpoint", "revocation_endpoint"} {
		if !strings.Contains(w.Body.String(), endpoint) {
			t.Fatal(endpoint)
		}
	}
	w = httptest.NewRecorder()
	m.serveRegister(w, httptest.NewRequest("POST", "/register", strings.NewReader(`{"redirect_uris":["http://localhost/cb"],"token_endpoint_auth_method":"client_secret_post"}`)))
	var c registration
	_ = json.Unmarshal(w.Body.Bytes(), &c)
	for _, secret := range []string{"wrong", c.Secret} {
		req := httptest.NewRequest("POST", "/token", strings.NewReader(url.Values{"client_id": {c.ClientID}, "client_secret": {secret}}.Encode()))
		req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		_ = req.ParseForm()
		_, ok, _ := m.clientAuthenticated(req)
		if ok != (secret == c.Secret) {
			t.Fatal("client secret not checked")
		}
	}
	w = httptest.NewRecorder()
	m.oauthLifetime(w, httptest.NewRequest("POST", "/", strings.NewReader(`{"access_ttl_seconds":1}`)))
	if w.Code != 200 || m.oauth.ttl.Seconds() != 1 {
		t.Fatal("TTL control failed")
	}
}

// formPost POSTs a form without following redirects, with HTTP Basic client
// credentials when basic is id and secret. It never returns token values.
func formPost(t *testing.T, base, path string, form url.Values, basic ...string) (int, map[string]any, http.Header) {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, base+path, strings.NewReader(form.Encode()))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	if len(basic) == 2 {
		req.SetBasicAuth(basic[0], basic[1])
	}
	resp, err := noRedirect.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	out := map[string]any{}
	_ = json.NewDecoder(resp.Body).Decode(&out)
	return resp.StatusCode, out, resp.Header
}

func registerClient(t *testing.T, base, method string, grants ...string) registration {
	t.Helper()
	body, err := json.Marshal(registration{RedirectURIs: []string{testRedirect}, AuthMethod: method, GrantTypes: grants})
	if err != nil {
		t.Fatal(err)
	}
	resp, err := http.Post(base+"/register", "application/json", strings.NewReader(string(body)))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var c registration
	if err := json.NewDecoder(resp.Body).Decode(&c); err != nil || resp.StatusCode != http.StatusCreated {
		t.Fatalf("register %s = %d", method, resp.StatusCode)
	}
	return c
}

func withValues(v url.Values, kv ...string) url.Values {
	out := url.Values{}
	for k, vs := range v {
		out[k] = append([]string{}, vs...)
	}
	for i := 0; i+1 < len(kv); i += 2 {
		out.Set(kv[i], kv[i+1])
	}
	return out
}

// TestUnregisteredClientsAreRefused: in a DCR walk the mock is a strict
// authorization server. A client_id it never registered gets no consent page,
// no code, no token and no revocation, whatever it sends; a registered client
// must use its registered authentication method and secret, one method only;
// and a redirect_uri must equal a registered one exactly.
func TestUnregisteredClientsAreRefused(t *testing.T) {
	m, srv := testMock(t, "2026", "oauth")
	verifier, challenge := pkcePair()
	unknown := authQuery("never-registered", m.resourceURI(), challenge, "S256")
	resp, err := noRedirect.Get(srv.URL + "/authorize?" + unknown.Encode())
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest || resp.Header.Get("Location") != "" {
		t.Errorf("authorize GET, unregistered client = %d (redirected: %t), want 400 and no redirect",
			resp.StatusCode, resp.Header.Get("Location") != "")
	}
	status, _, header := formPost(t, srv.URL, "/authorize", withValues(unknown, "decision", "approve"))
	code := ""
	if loc, err := url.Parse(header.Get("Location")); err == nil {
		code = loc.Query().Get("code")
	}
	if status != http.StatusBadRequest || code != "" {
		t.Errorf("authorize approve, unregistered client = %d (code issued: %t), want 400", status, code != "")
	}
	if code == "" {
		code = "mcpmock-code-none"
	}
	status, out, _ := formPost(t, srv.URL, "/token", url.Values{"grant_type": {"authorization_code"}, "code": {code},
		"client_id": {"never-registered"}, "redirect_uri": {testRedirect}, "code_verifier": {verifier}})
	if status != http.StatusUnauthorized || out["error"] != "invalid_client" || out["access_token"] != nil {
		t.Errorf("token, unregistered client = %d %v (token issued: %t), want 401 invalid_client",
			status, out["error"], out["access_token"] != nil)
	}

	basic, post, public := registerClient(t, srv.URL, "client_secret_basic"), registerClient(t, srv.URL, "client_secret_post"),
		registerClient(t, srv.URL, "none")
	status, out, _ = formPost(t, srv.URL, "/token", url.Values{"grant_type": {"refresh_token"}, "refresh_token": {"unknown-fixture"},
		"client_id": {post.ClientID}, "client_secret": {"wrong-fixture"}})
	if status != http.StatusUnauthorized || out["error"] != "invalid_client" {
		t.Errorf("token, wrong client secret = %d %v, want 401 invalid_client", status, out["error"])
	}
	revoke := url.Values{"token": {"unknown-fixture"}, "token_type_hint": {"access_token"}}
	for _, c := range []struct {
		name  string
		form  url.Values
		basic []string
		want  int
	}{
		{"unregistered, wrong secret", withValues(revoke, "client_id", "another-never-registered", "client_secret", "wrong-fixture"), nil, 401},
		{"unregistered, public", withValues(revoke, "client_id", "never-registered"), nil, 401},
		{"unregistered, Basic", revoke, []string{"never-registered", "wrong-fixture"}, 401},
		{"no client", revoke, nil, 401},
		{"basic client, right secret", revoke, []string{basic.ClientID, basic.Secret}, 200},
		{"basic client, wrong secret", revoke, []string{basic.ClientID, "wrong-fixture"}, 401},
		{"basic client, secret in the form", withValues(revoke, "client_id", basic.ClientID, "client_secret", basic.Secret), nil, 401},
		{"basic client, another client_id in the form", withValues(revoke, "client_id", post.ClientID), []string{basic.ClientID, basic.Secret}, 401},
		{"basic client, a form secret as well", withValues(revoke, "client_secret", basic.Secret), []string{basic.ClientID, basic.Secret}, 401},
		{"post client, right secret", withValues(revoke, "client_id", post.ClientID, "client_secret", post.Secret), nil, 200},
		{"post client, no secret", withValues(revoke, "client_id", post.ClientID), nil, 401},
		{"post client, secret over Basic", revoke, []string{post.ClientID, post.Secret}, 401},
		{"public client", withValues(revoke, "client_id", public.ClientID), nil, 200},
		{"public client sending a secret", withValues(revoke, "client_id", public.ClientID, "client_secret", "wrong-fixture"), nil, 401},
		{"public client over Basic", revoke, []string{public.ClientID, ""}, 401},
	} {
		status, out, header := formPost(t, srv.URL, "/revoke", c.form, c.basic...)
		if status != c.want || (c.want == 401 && out["error"] != "invalid_client") {
			t.Errorf("revoke, %s = %d %v, want %d", c.name, status, out["error"], c.want)
		}
		if c.want == 401 && c.basic != nil && !strings.HasPrefix(header.Get("WWW-Authenticate"), "Basic ") {
			t.Errorf("revoke, %s: a Basic attempt got WWW-Authenticate %q", c.name, header.Get("WWW-Authenticate"))
		}
	}
	var received struct {
		Revocations []revocation `json:"revocations"`
	}
	getJSON(t, srv.URL+"/__control/oauth/received", &received)
	ids := []string{}
	for _, r := range received.Revocations {
		ids = append(ids, r.ClientID)
	}
	if want := []string{basic.ClientID, post.ClientID, public.ClientID}; !slices.Equal(ids, want) {
		t.Errorf("revocation receipts from %v, want only the authenticated %v", ids, want)
	}

	for _, redirect := range []string{testRedirect + "/", testRedirect + "?next=1", "http://localhost:9/callback",
		"http://127.0.0.1:19/callback", "HTTP://127.0.0.1:9/callback"} {
		q := withValues(authQuery(public.ClientID, m.resourceURI(), challenge, "S256"), "redirect_uri", redirect)
		resp, err := noRedirect.Get(srv.URL + "/authorize?" + q.Encode())
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusBadRequest || resp.Header.Get("Location") != "" {
			t.Errorf("redirect_uri %q for a client registered with %q = %d, want 400 and no redirect", redirect, testRedirect, resp.StatusCode)
		}
	}
	q := authQuery(public.ClientID, m.resourceURI(), challenge, "S256")
	if got := authorizeRedirect(t, srv.URL, q); got.Get("code") == "" || got.Get("state") != "st-1" {
		t.Errorf("the registered client and redirect_uri got no code: error %q", got.Get("error"))
	}
}
