package credentials

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
)

// The client speaks mcp-upstream-token-v1 as its golden states it: the
// request is $defs/Request, the golden success is a token and the golden
// refusal a typed refusal with its connect hint.
func TestUpstreamTokenClientMatchesGolden(t *testing.T) {
	golden := schematest.ReadExample(t, "mcp-upstream-token-v1.json")
	d := newFakeDjango(t)
	c := tokenClientFor(t, d)
	if c.Endpoint() != d.URL+"/agentcc/internal/mcp/upstream-token/" {
		t.Fatalf("endpoint %q", c.Endpoint())
	}
	req := golden["request"].(map[string]any)
	k := Key{OrgID: req["org_id"].(string), UserID: req["user_id"].(string), ServerID: req["server_id"].(string)}

	success := golden["success"].(map[string]any)
	exp, _ := time.Parse(time.RFC3339, success["expires_at"].(string))
	d.give(k, success["access_token"].(string), exp, int(success["credential_version"].(float64)))
	tok, err := c.Fetch(context.Background(), k)
	if err != nil || tok.AccessToken != success["access_token"] || !tok.ExpiresAt.Equal(exp) || tok.CredentialVersion != 4 {
		t.Fatalf("golden success read as %v, %v", tok, err)
	}
	d.mu.Lock()
	sent := d.bodies[len(d.bodies)-1]
	d.mu.Unlock()
	if errs := schematest.ValidateDef(t, "mcp-upstream-token-v1.schema.json", "Request", sent); len(errs) > 0 {
		t.Fatalf("the request breaks $defs/Request: %v", errs)
	}

	refusal := golden["refusal"].(map[string]any)
	d.refuse(k, refusal["error"].(string), refusal["connect_hint"].(string))
	_, err = c.Fetch(context.Background(), k)
	var r *Refusal
	if !errors.As(err, &r) || r.Reason != refusal["error"] || r.ConnectHint != refusal["connect_hint"] {
		t.Fatalf("golden refusal read as %v", err)
	}
	if errs := schematest.Validate(t, "mcp-upstream-token-v1.schema.json", golden); len(errs) > 0 {
		t.Fatalf("the golden breaks its schema: %v", errs)
	}
}

// The gateway authenticates to Django with a secret of its own, sent in
// X-Agentcc-MCP-Internal and never as Authorization; the client refuses to
// start with a secret equal to an admin token; a refused secret is a
// failure of the channel, never a refusal of the user's credential.
func TestInternalSecretNotAdminToken(t *testing.T) {
	d := newFakeDjango(t)
	c := tokenClientFor(t, d)
	d.give(keyA1, "m25-test-token-a1", time.Now().Add(time.Hour), 1)
	if _, err := c.Fetch(context.Background(), keyA1); err != nil {
		t.Fatal(err)
	}
	h := d.sentHeaders()[0]
	if h.Get(InternalSecretHeader) != testDjangoSecret || h.Get("Authorization") != "" || h.Get("X-Api-Key") != "" {
		t.Fatalf("headers sent: secret %v, Authorization %q, x-api-key %q", h.Get(InternalSecretHeader) == testDjangoSecret, h.Get("Authorization"), h.Get("X-Api-Key"))
	}

	for _, admin := range [][]string{{testDjangoSecret}, {"", testDjangoSecret}} {
		_, err := NewTokenClient(TokenClientOptions{BaseURL: d.URL, Secret: testDjangoSecret, AdminTokens: admin})
		if err == nil || !strings.Contains(err.Error(), "admin token") || strings.Contains(err.Error(), testDjangoSecret) {
			t.Fatalf("a secret equal to the admin token: %v", err)
		}
	}
	if _, err := NewTokenClient(TokenClientOptions{BaseURL: d.URL}); err == nil {
		t.Fatal("a client without a secret")
	}
	for _, base := range []string{"", "django:8000", "ftp://django", "http://user:pw@django", "http://django/?x=1"} {
		if _, err := NewTokenClient(TokenClientOptions{BaseURL: base, Secret: testDjangoSecret}); err == nil {
			t.Fatalf("base URL %q was accepted", base)
		}
	}

	wrong, err := NewTokenClient(TokenClientOptions{BaseURL: d.URL, Secret: "m25-test-some-other-secret"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = wrong.Fetch(context.Background(), keyA1)
	var r *Refusal
	if !errors.Is(err, ErrTokenChannel) || errors.As(err, &r) {
		t.Fatalf("a refused secret: %v, want a channel failure", err)
	}
}

// The secret goes only to the configured address: a redirect is not
// followed, so its target never sees the secret.
func TestTokenClientFollowsNoRedirect(t *testing.T) {
	var reached atomic.Int32
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached.Add(1)
		w.WriteHeader(http.StatusOK)
	}))
	defer target.Close()
	django := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL+UpstreamTokenPath, http.StatusTemporaryRedirect)
	}))
	defer django.Close()
	c, err := NewTokenClient(TokenClientOptions{BaseURL: django.URL, Secret: testDjangoSecret})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := c.Fetch(context.Background(), keyA1); !errors.Is(err, ErrTokenChannel) {
		t.Fatalf("a redirect: %v, want a channel failure", err)
	}
	if reached.Load() != 0 {
		t.Fatal("the redirect target was contacted")
	}
}

// fakeDjango serves mcp-upstream-token-v1 as M25x does: POST
// UpstreamTokenPath with the internal secret, a token or a typed refusal
// per org, user and server, or per org and server for the organization's
// shared request (no_credential with a connect hint for anyone it does not
// know), HTTP 401 for a wrong secret. An answer set with answerRaw is sent
// as it is, with its own status (Microsoft's hold is HTTP 409).
type fakeDjango struct {
	*httptest.Server
	secret string

	mu      sync.Mutex
	answers map[Key]tokenAnswer
	raw     map[Key]rawAnswer
	fetches map[Key]int
	headers []http.Header
	bodies  []map[string]any
	status  int // when set, every request is answered with this status
}

// rawAnswer is an answer body sent verbatim with its status.
type rawAnswer struct {
	status int
	body   string
}

func newFakeDjango(t *testing.T) *fakeDjango {
	t.Helper()
	d := &fakeDjango{secret: testDjangoSecret, answers: map[Key]tokenAnswer{}, raw: map[Key]rawAnswer{}, fetches: map[Key]int{}}
	d.Server = httptest.NewServer(http.HandlerFunc(d.serve))
	t.Cleanup(d.Close)
	return d
}

func (d *fakeDjango) serve(w http.ResponseWriter, r *http.Request) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.headers = append(d.headers, r.Header.Clone())
	if r.Method != http.MethodPost || r.URL.Path != UpstreamTokenPath {
		http.NotFound(w, r)
		return
	}
	if d.status != 0 {
		w.WriteHeader(d.status)
		return
	}
	if r.Header.Get(InternalSecretHeader) != d.secret {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	var body map[string]any
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		return
	}
	d.bodies = append(d.bodies, body)
	str := func(name string) string { s, _ := body[name].(string); return s }
	k := Key{OrgID: str("org_id"), UserID: str("user_id"), ServerID: str("server_id"), Shared: str("scope") == "shared"}
	d.fetches[k]++
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	if raw, ok := d.raw[k]; ok {
		w.WriteHeader(raw.status)
		_, _ = io.WriteString(w, raw.body)
		return
	}
	a, ok := d.answers[k]
	if !ok {
		a = tokenAnswer{Error: RefusalNoCredential, ConnectHint: testConnectHost + ConnectStartPath(k.ServerID)}
	}
	_ = json.NewEncoder(w).Encode(answerJSON(a))
}

// answerJSON is a's wire shape: Success or Refusal, nothing else.
func answerJSON(a tokenAnswer) map[string]any {
	if a.Error != "" {
		out := map[string]any{"error": a.Error}
		if a.ConnectHint != "" {
			out["connect_hint"] = a.ConnectHint
		}
		return out
	}
	return map[string]any{"access_token": a.AccessToken, "token_type": a.TokenType, "expires_at": a.ExpiresAt, "credential_version": a.CredentialVersion}
}

// give makes Django hand k the token value, expiring at exp.
func (d *fakeDjango) give(k Key, value string, exp time.Time, version int) {
	d.mu.Lock()
	defer d.mu.Unlock()
	delete(d.raw, k)
	d.answers[k] = tokenAnswer{AccessToken: value, TokenType: "Bearer", ExpiresAt: exp.UTC().Format(time.RFC3339), CredentialVersion: version}
}

// refuse makes Django refuse k with reason, naming hint as the start page.
func (d *fakeDjango) refuse(k Key, reason, hint string) {
	d.mu.Lock()
	defer d.mu.Unlock()
	delete(d.raw, k)
	d.answers[k] = tokenAnswer{Error: reason, ConnectHint: hint}
}

// answerRaw makes Django answer k with body as it is, under status.
func (d *fakeDjango) answerRaw(k Key, status int, body string) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.raw[k] = rawAnswer{status: status, body: body}
}

func (d *fakeDjango) count(k Key) int {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.fetches[k]
}

func (d *fakeDjango) failWith(status int) {
	d.mu.Lock()
	d.status = status
	d.mu.Unlock()
}

func (d *fakeDjango) sentHeaders() []http.Header {
	d.mu.Lock()
	defer d.mu.Unlock()
	return append([]http.Header(nil), d.headers...)
}

// tokenClientFor is a TokenClient on d.
func tokenClientFor(t *testing.T, d *fakeDjango) *TokenClient {
	t.Helper()
	c, err := NewTokenClient(TokenClientOptions{BaseURL: d.URL, Secret: testDjangoSecret, AdminTokens: []string{testAdminToken}})
	if err != nil {
		t.Fatal(err)
	}
	return c
}
