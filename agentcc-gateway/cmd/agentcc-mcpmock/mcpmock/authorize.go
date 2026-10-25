package mcpmock

import (
	"encoding/json"
	"errors"
	"html/template"
	"io"
	"net/http"
	"net/url"
	"time"
)

// authParams is one authorization request (query on GET, form on POST).
type authParams struct {
	ResponseType, ClientID, RedirectURI, State, Scope string
	Challenge, Method, Resource, Subject              string
}

func readAuthParams(v url.Values) authParams {
	return authParams{
		ResponseType: v.Get("response_type"), ClientID: v.Get("client_id"),
		RedirectURI: v.Get("redirect_uri"), State: v.Get("state"), Scope: v.Get("scope"),
		Challenge: v.Get("code_challenge"), Method: v.Get("code_challenge_method"),
		Resource: v.Get("resource"), Subject: v.Get("login_hint"),
	}
}

// redirectAllowed is OAuth 2.1's rule: HTTPS, or HTTP on a loopback host.
func redirectAllowed(raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || !u.IsAbs() || u.Host == "" || u.Fragment != "" {
		return false
	}
	switch u.Scheme {
	case "https":
		return true
	case "http":
		h := u.Hostname()
		return h == "localhost" || h == "127.0.0.1" || h == "::1"
	}
	return false
}

// validate returns the OAuth error for a bad request, and whether it may be
// sent to the redirect_uri (it may not when the client or redirect is bad).
func (m *Mock) validateAuth(p authParams) (code, desc string, redirectable bool) {
	switch {
	case p.ClientID == "" || !redirectAllowed(p.RedirectURI):
		return "invalid_request", "client_id and an https (or loopback http) redirect_uri are required", false
	case p.ResponseType != "code":
		return "unsupported_response_type", "only response_type=code", true
	case p.Method != "S256" || len(p.Challenge) < 43 || len(p.Challenge) > 128:
		return "invalid_request", "PKCE with code_challenge_method=S256 is required", true
	case p.Resource != m.resourceURI():
		return "invalid_target", "resource must be this server's canonical URI " + m.resourceURI(), true
	}
	return "", "", true
}

// redirect sends the authorization response to the client, with iss
// (RFC 9207) on success and on error.
func (m *Mock) redirect(w http.ResponseWriter, r *http.Request, p authParams, q url.Values) {
	u, _ := url.Parse(p.RedirectURI)
	values := u.Query()
	for k, vs := range q {
		values[k] = vs
	}
	if p.State != "" {
		values.Set("state", p.State)
	}
	values.Set("iss", m.cfg.PublicURL)
	u.RawQuery = values.Encode()
	http.Redirect(w, r, u.String(), http.StatusFound)
}

// serveAuthorize is the authorization endpoint. GET validates the request and
// shows a consent page; its form POSTs back with decision=approve or deny.
// The subject is login_hint (default mcpmock-user).
func (m *Mock) serveAuthorize(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid_request: unreadable form", http.StatusBadRequest)
		return
	}
	p := readAuthParams(r.Form)
	if code, desc, redirectable := m.validateAuth(p); code != "" {
		if !redirectable {
			http.Error(w, code+": "+desc, http.StatusBadRequest)
			return
		}
		m.redirect(w, r, p, url.Values{"error": {code}, "error_description": {desc}})
		return
	}
	if p.Subject == "" {
		p.Subject = defaultSubject
	}
	if r.Method == http.MethodGet {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("X-Frame-Options", "DENY")
		_ = consentPage.Execute(w, p)
		return
	}
	if r.PostForm.Get("decision") != "approve" {
		m.redirect(w, r, p, url.Values{"error": {"access_denied"}})
		return
	}
	code := "mcpmock-code-" + randomID()
	m.oauth.mu.Lock()
	m.oauth.codes[code] = &authCode{clientID: p.ClientID, redirectURI: p.RedirectURI, challenge: p.Challenge,
		resource: p.Resource, scope: p.Scope, sub: p.Subject, exp: m.oauth.now().Add(authCodeTTL)}
	m.oauth.mu.Unlock()
	m.redirect(w, r, p, url.Values{"code": {code}})
}

var consentPage = template.Must(template.New("consent").Parse(`<!doctype html>
<html><head><meta charset="utf-8"><title>mcpmock consent</title></head>
<body>
<h1>Authorize {{.ClientID}}</h1>
<p>Test authorization server (agentcc-mcpmock). Grant <b>{{.ClientID}}</b> access to <code>{{.Resource}}</code> as <b>{{.Subject}}</b>, scope <code>{{.Scope}}</code>, redirecting to <code>{{.RedirectURI}}</code>?</p>
<form method="post" action="/authorize">
<input type="hidden" name="response_type" value="{{.ResponseType}}">
<input type="hidden" name="client_id" value="{{.ClientID}}">
<input type="hidden" name="redirect_uri" value="{{.RedirectURI}}">
<input type="hidden" name="state" value="{{.State}}">
<input type="hidden" name="scope" value="{{.Scope}}">
<input type="hidden" name="code_challenge" value="{{.Challenge}}">
<input type="hidden" name="code_challenge_method" value="{{.Method}}">
<input type="hidden" name="resource" value="{{.Resource}}">
<input type="hidden" name="login_hint" value="{{.Subject}}">
<button type="submit" name="decision" value="approve">Approve</button>
<button type="submit" name="decision" value="deny">Deny</button>
</form>
</body></html>
`))

// controlMintToken mints an access token without the browser flow, for tests:
// {"aud", "sub", "scope", "ttl_s"} (aud defaults to this server, so a test
// can mint one for another audience on purpose).
func (m *Mock) controlMintToken(w http.ResponseWriter, r *http.Request) {
	if m.oauth == nil {
		controlError(w, http.StatusNotFound, "the mock is not in OAuth mode (--auth oauth)")
		return
	}
	var req struct {
		Aud   string `json:"aud"`
		Sub   string `json:"sub"`
		Scope string `json:"scope"`
		TTLS  int    `json:"ttl_s"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil && !errors.Is(err, io.EOF) {
		controlError(w, http.StatusBadRequest, "token: "+err.Error())
		return
	}
	if req.Aud == "" {
		req.Aud = m.resourceURI()
	}
	if req.Sub == "" {
		req.Sub = defaultSubject
	}
	ttl := accessTokenTTL
	if req.TTLS != 0 {
		ttl = time.Duration(req.TTLS) * time.Second // a negative ttl mints an expired token
	}
	tok := m.oauth.mintAccess(req.Aud, "mcpmock-control", req.Scope, req.Sub, ttl)
	_ = writeJSON(w, http.StatusOK, map[string]any{"access_token": tok, "token_type": "Bearer",
		"expires_in": int(ttl.Seconds()), "aud": req.Aud, "sub": req.Sub})
}
