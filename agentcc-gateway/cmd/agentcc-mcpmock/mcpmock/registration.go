package mcpmock

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/url"
	"slices"
	"time"
)

type registration struct {
	ClientID      string   `json:"client_id"`
	Secret        string   `json:"client_secret,omitempty"`
	RedirectURIs  []string `json:"redirect_uris"`
	AuthMethod    string   `json:"token_endpoint_auth_method"`
	GrantTypes    []string `json:"grant_types"`
	ResponseTypes []string `json:"response_types"`
}

type revocation struct {
	Digest   string `json:"token_sha256"`
	Hint     string `json:"token_type_hint"`
	ClientID string `json:"client_id"`
	Subject  string `json:"subject,omitempty"`
}

// DCR is fixture-only. Registrations never fetch software statements or URLs.
func (m *Mock) serveRegister(w http.ResponseWriter, r *http.Request) {
	var c registration
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10)).Decode(&c); err != nil {
		tokenError(w, "invalid_client_metadata", "expected client metadata")
		return
	}
	if len(c.RedirectURIs) == 0 || len(c.RedirectURIs) > 16 {
		tokenError(w, "invalid_redirect_uri", "redirect_uris is required")
		return
	}
	for _, uri := range c.RedirectURIs {
		if !redirectAllowed(uri) {
			tokenError(w, "invalid_redirect_uri", "invalid redirect URI")
			return
		}
	}
	if c.AuthMethod == "" {
		c.AuthMethod = "client_secret_basic"
	}
	if !slices.Contains([]string{"none", "client_secret_post", "client_secret_basic"}, c.AuthMethod) {
		tokenError(w, "invalid_client_metadata", "unsupported token authentication")
		return
	}
	if len(c.GrantTypes) == 0 {
		c.GrantTypes = []string{"authorization_code"}
	}
	if len(c.ResponseTypes) == 0 {
		c.ResponseTypes = []string{"code"}
	}
	for _, grant := range c.GrantTypes {
		if grant != "authorization_code" && grant != "refresh_token" {
			tokenError(w, "invalid_client_metadata", "unsupported grant")
			return
		}
	}
	if len(c.ResponseTypes) != 1 || c.ResponseTypes[0] != "code" {
		tokenError(w, "invalid_client_metadata", "unsupported response type")
		return
	}
	c.ClientID = "mcpmock-client-" + randomID()
	c.Secret = ""
	if c.AuthMethod != "none" {
		c.Secret = "mcpmock-secret-" + randomID()
	}
	m.oauth.mu.Lock()
	m.oauth.clients[c.ClientID] = c
	m.oauth.mu.Unlock()
	w.Header().Set("Cache-Control", "no-store")
	_ = writeJSON(w, http.StatusCreated, c)
}

// registeredRedirect reports whether uri is exactly one of the redirect URIs
// clientID registered. A client_id the mock never registered has none.
func (m *Mock) registeredRedirect(clientID, uri string) bool {
	m.oauth.mu.Lock()
	defer m.oauth.mu.Unlock()
	c, registered := m.oauth.clients[clientID]
	return registered && slices.Contains(c.RedirectURIs, uri)
}

func (m *Mock) clientGrantAllowed(clientID, grant string) bool {
	m.oauth.mu.Lock()
	defer m.oauth.mu.Unlock()
	c, registered := m.oauth.clients[clientID]
	return registered && slices.Contains(c.GrantTypes, grant)
}

// clientAuthenticated authenticates the client of a token or revocation
// request (RFC 6749 section 2.3). Only a registered client authenticates, and
// only with its registered token_endpoint_auth_method: none (client_id in the
// form, no secret), client_secret_post (id and secret in the form) or
// client_secret_basic (HTTP Basic, form-urlencoded per section 2.3.1). One
// method per request: Basic with a form secret, or with a different form
// client_id, is refused. viaHeader reports an Authorization header attempt,
// which a refusal must answer with a 401 Basic challenge.
func (m *Mock) clientAuthenticated(r *http.Request) (id string, ok, viaHeader bool) {
	id, secret := r.PostForm.Get("client_id"), r.PostForm.Get("client_secret")
	viaHeader = r.Header.Get("Authorization") != ""
	if viaHeader {
		rawID, rawSecret, isBasic := r.BasicAuth()
		basicID, idErr := url.QueryUnescape(rawID)
		basicSecret, secretErr := url.QueryUnescape(rawSecret)
		if !isBasic || idErr != nil || secretErr != nil || secret != "" || (id != "" && id != basicID) {
			return basicID, false, true
		}
		id, secret = basicID, basicSecret
	}
	m.oauth.mu.Lock()
	c, registered := m.oauth.clients[id]
	m.oauth.mu.Unlock()
	switch {
	case !registered:
		return id, false, viaHeader
	case c.AuthMethod == "none":
		return id, !viaHeader && secret == "", viaHeader
	case (c.AuthMethod == "client_secret_basic") != viaHeader:
		return id, false, viaHeader
	}
	return id, subtle.ConstantTimeCompare([]byte(secret), []byte(c.Secret)) == 1, viaHeader
}

// clientError is RFC 6749 section 5.2's invalid_client: 401, with a Basic
// challenge when the client tried the Authorization header.
func clientError(w http.ResponseWriter, viaHeader bool) {
	if viaHeader {
		w.Header().Set("WWW-Authenticate", `Basic realm="mcpmock"`)
	}
	_ = writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "invalid_client", "error_description": "client authentication failed"})
}

// RFC 7009 deliberately returns 200 for unknown or foreign tokens. Receipt
// hashes let walks compare tokens without putting raw credentials in reports.
func (m *Mock) serveRevoke(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		tokenError(w, "invalid_request", "expected form")
		return
	}
	id, ok, viaHeader := m.clientAuthenticated(r)
	if !ok {
		clientError(w, viaHeader)
		return
	}
	tok := r.PostForm.Get("token")
	if tok == "" {
		tokenError(w, "invalid_request", "token is required")
		return
	}
	digest := sha256.Sum256([]byte(tok))
	receipt := revocation{Digest: "sha256:" + hex.EncodeToString(digest[:]), Hint: r.PostForm.Get("token_type_hint"), ClientID: id}
	o := m.oauth
	o.mu.Lock()
	if rt, found := o.refresh[tok]; found && rt.clientID == id {
		o.revoked[rt.family] = true
		receipt.Subject = rt.sub
	}
	if at, found := o.access[tok]; found && at.clientID == id {
		delete(o.access, tok)
		receipt.Subject = at.sub
		if at.family != "" {
			o.revoked[at.family] = true
		}
	}
	o.revocations = append(o.revocations, receipt)
	o.mu.Unlock()
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusOK)
}

func (m *Mock) oauthReceipts(w http.ResponseWriter, _ *http.Request) {
	o := m.oauth
	o.mu.Lock()
	defer o.mu.Unlock()
	clients := make([]registration, 0, len(o.clients))
	for _, c := range o.clients {
		c.Secret = ""
		clients = append(clients, c)
	}
	slices.SortFunc(clients, func(a, b registration) int {
		if a.ClientID < b.ClientID {
			return -1
		}
		if a.ClientID > b.ClientID {
			return 1
		}
		return 0
	})
	receipts := append([]revocation{}, o.revocations...)
	_ = writeJSON(w, http.StatusOK, map[string]any{"registrations": clients, "revocations": receipts})
}

func (m *Mock) oauthLifetime(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Seconds int `json:"access_ttl_seconds"`
	}
	if json.NewDecoder(r.Body).Decode(&body) != nil || body.Seconds < 1 || body.Seconds > 900 {
		tokenError(w, "invalid_request", "access_ttl_seconds must be 1..900")
		return
	}
	m.oauth.mu.Lock()
	m.oauth.ttl = time.Duration(body.Seconds) * time.Second
	m.oauth.mu.Unlock()
	_ = writeJSON(w, http.StatusOK, body)
}
