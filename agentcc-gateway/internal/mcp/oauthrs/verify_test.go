package oauthrs

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/oauthrs/oauthrstest"
)

// verifierFixture is a resource server holding one pushed key, with a fixed
// clock.
type verifierFixture struct {
	v      *Verifier
	signer *Signer
	now    time.Time
}

// Signer is the test signer type, named here for brevity.
type Signer = oauthrstest.Signer

func newVerifierFixture(t *testing.T) *verifierFixture {
	t.Helper()
	res := newTestResource(t)
	now := time.Unix(1_800_000_000, 0)
	clock := func() time.Time { return now }
	keys := NewKeySet(testIssuer, nil, clock)
	signer := oauthrstest.NewSigner(t, "as-test")
	if _, err := keys.Push(context.Background(), oauthrstest.PushBody(t, testIssuer, 1, signer)); err != nil {
		t.Fatal(err)
	}
	return &verifierFixture{v: NewVerifier(res, keys, clock), signer: signer, now: now}
}

func (f *verifierFixture) claims(aud string) map[string]any {
	return oauthrstest.AccessClaims(testIssuer, aud, f.now, 10*time.Minute)
}

func (f *verifierFixture) verify(t *testing.T, token, path string) (*mcp.Principal, error) {
	t.Helper()
	return f.v.Verify(context.Background(), token, path)
}

func requireRefused(t *testing.T, p *mcp.Principal, err error, what string) {
	t.Helper()
	if err == nil || p != nil {
		t.Fatalf("%s: accepted (principal %+v, error %v), want refused", what, p, err)
	}
}

// A token opens exactly the resource it was issued for: the virtual server
// whose address is its audience, never another one, and never the endpoint.
func TestAudienceMustMatchPath(t *testing.T) {
	f := newVerifierFixture(t)
	forA := f.signer.Sign(t, f.claims("https://gw.example.test/mcp/v/vs_A"))
	if p, err := f.verify(t, forA, "/mcp/v/vs_A"); err != nil || p == nil {
		t.Fatalf("a token for vs_A on vs_A: %+v, %v, want accepted", p, err)
	}
	for _, path := range []string{"/mcp/v/vs_B", "/mcp", "/mcp/v/vs_A/x", "/other"} {
		p, err := f.verify(t, forA, path)
		requireRefused(t, p, err, "a token for vs_A on "+path)
	}
	forEndpoint := f.signer.Sign(t, f.claims("https://gw.example.test/mcp"))
	if p, err := f.verify(t, forEndpoint, "/mcp"); err != nil || p == nil {
		t.Fatalf("a token for the endpoint on the endpoint: %+v, %v, want accepted", p, err)
	}
	p, err := f.verify(t, forEndpoint, "/mcp/v/vs_A")
	requireRefused(t, p, err, "a token for the endpoint on vs_A")

	for name, aud := range map[string]any{
		"another host":   "https://evil.example.test/mcp/v/vs_A",
		"plain http":     "http://gw.example.test/mcp/v/vs_A",
		"trailing slash": "https://gw.example.test/mcp/v/vs_A/",
		"array":          []string{"https://gw.example.test/mcp/v/vs_A"},
		"missing":        nil,
	} {
		c := f.claims("")
		if aud == nil {
			delete(c, "aud")
		} else {
			c["aud"] = aud
		}
		p, err := f.verify(t, f.signer.Sign(t, c), "/mcp/v/vs_A")
		requireRefused(t, p, err, "aud "+name)
	}
}

// Only the trusted authorization server's access tokens are accepted: the
// issuer and type must match, the signature must verify with a pushed key,
// and the header must name ES256 and that key.
func TestOnlyTheAuthorizationServersTokensAreAccepted(t *testing.T) {
	f := newVerifierFixture(t)
	aud := "https://gw.example.test/mcp/v/vs_A"
	base := f.claims(aud)

	with := func(k string, v any) map[string]any {
		c := map[string]any{}
		for key, val := range base {
			c[key] = val
		}
		if v == nil {
			delete(c, k)
		} else {
			c[k] = v
		}
		return c
	}
	other := oauthrstest.NewSigner(t, "as-test") // same kid, key never pushed
	unknown := oauthrstest.NewSigner(t, "as-unknown")
	valid := f.signer.Sign(t, base)
	parts := strings.Split(valid, ".")
	tampered := parts[0] + "." + base64.RawURLEncoding.EncodeToString([]byte(`{"typ":"futureagi-mcp-access","iss":"`+testIssuer+`","aud":"`+aud+`","sub":"someone-else","org_id":"org-other","client_id":"c","scope":"mcp:tools","iat":1800000000,"nbf":1800000000,"exp":1800000600,"jti":"j"}`)) + "." + parts[2]

	tokens := map[string]string{
		"other issuer":         f.signer.Sign(t, with("iss", "https://other.example.test/as")),
		"callback type":        f.signer.Sign(t, with("typ", "futureagi-mcp-callback")),
		"no type":              f.signer.Sign(t, with("typ", nil)),
		"key never pushed":     other.Sign(t, base),
		"unknown kid":          unknown.Sign(t, base),
		"tampered claims":      tampered,
		"no signature":         parts[0] + "." + parts[1] + ".",
		"alg none":             f.signer.SignWithHeader(t, map[string]any{"alg": "none", "kid": "as-test"}, base),
		"alg HS256":            f.signer.SignWithHeader(t, map[string]any{"alg": "HS256", "kid": "as-test"}, base),
		"no kid":               f.signer.SignWithHeader(t, map[string]any{"alg": "ES256"}, base),
		"critical extension":   f.signer.SignWithHeader(t, map[string]any{"alg": "ES256", "kid": "as-test", "crit": []string{"x"}}, base),
		"no sub":               f.signer.Sign(t, with("sub", nil)),
		"no org":               f.signer.Sign(t, with("org_id", nil)),
		"no client":            f.signer.Sign(t, with("client_id", nil)),
		"no scope":             f.signer.Sign(t, with("scope", "")),
		"no jti":               f.signer.Sign(t, with("jti", nil)),
		"oversized":            "eyJ" + strings.Repeat("A", maxTokenBytes) + ".e30.e30",
		"not base64 header":    "eyJ!!!.e30.e30",
		"string numeric claim": f.signer.Sign(t, with("exp", "1800000600")),
	}
	for name, token := range tokens {
		p, err := f.verify(t, token, "/mcp/v/vs_A")
		requireRefused(t, p, err, name)
	}
	if p, err := f.verify(t, valid, "/mcp/v/vs_A"); err != nil || p == nil {
		t.Fatalf("the valid token: %+v, %v", p, err)
	}
}

// Tokens are short-lived: an expired or not-yet-valid token is refused, and
// so is one whose lifetime exceeds MaxAccessLifetime, so a revoked consent
// loses access within 15 minutes.
func TestAccessTokenLifetimeCapped(t *testing.T) {
	f := newVerifierFixture(t)
	aud := "https://gw.example.test/mcp/v/vs_A"
	sign := func(iat, nbf, exp time.Time) string {
		c := f.claims(aud)
		c["iat"], c["nbf"], c["exp"] = iat.Unix(), nbf.Unix(), exp.Unix()
		return f.signer.Sign(t, c)
	}
	now := f.now
	if p, err := f.verify(t, sign(now, now, now.Add(MaxAccessLifetime)), "/mcp/v/vs_A"); err != nil || p == nil {
		t.Fatalf("a token living exactly the cap: %+v, %v, want accepted", p, err)
	}
	for name, token := range map[string]string{
		"over the cap":        sign(now, now, now.Add(MaxAccessLifetime+time.Second)),
		"expired":             sign(now.Add(-10*time.Minute), now.Add(-10*time.Minute), now),
		"not yet valid":       sign(now, now.Add(time.Minute), now.Add(5*time.Minute)),
		"issued in future":    sign(now.Add(time.Minute), now, now.Add(5*time.Minute)),
		"exp before iat":      sign(now.Add(20*time.Second), now, now.Add(10*time.Second)),
		"backdated long life": sign(now.Add(-14*time.Minute), now.Add(-14*time.Minute), now.Add(2*time.Minute)),
	} {
		p, err := f.verify(t, token, "/mcp/v/vs_A")
		requireRefused(t, p, err, name)
	}
	// A few seconds of clock skew between the issuer and the gateway is fine.
	if p, err := f.verify(t, sign(now.Add(10*time.Second), now.Add(10*time.Second), now.Add(5*time.Minute)), "/mcp/v/vs_A"); err != nil || p == nil {
		t.Fatalf("a token issued 10 s ahead of this clock: %+v, %v, want accepted", p, err)
	}
}

// The caller's identity comes from the token's signed claims only: org,
// user, client, scopes and token id, as an OAuth principal.
func TestOrgFromClaimsOnly(t *testing.T) {
	f := newVerifierFixture(t)
	c := f.claims("https://gw.example.test/mcp/v/vs_A")
	c["org_id"], c["sub"], c["client_id"], c["scope"], c["jti"] = "org-claimed", "user-claimed", "client-claimed", "mcp:tools mcp:prompts", "jti-claimed"
	p, err := f.verify(t, f.signer.Sign(t, c), "/mcp/v/vs_A")
	if err != nil || p == nil {
		t.Fatalf("Verify: %+v, %v", p, err)
	}
	want := mcp.Principal{Kind: mcp.PrincipalOAuth, OrgID: "org-claimed", UserID: "user-claimed", ClientID: "client-claimed", Scopes: []string{"mcp:tools", "mcp:prompts"}, TokenID: "jti-claimed"}
	if p.Kind != want.Kind || p.OrgID != want.OrgID || p.UserID != want.UserID || p.ClientID != want.ClientID ||
		p.TokenID != want.TokenID || strings.Join(p.Scopes, " ") != strings.Join(want.Scopes, " ") || p.KeyID != "" {
		t.Fatalf("principal %+v, want %+v", *p, want)
	}
}

// A credential that is not JWT-shaped is left to key authentication exactly
// as before: the verifier does not handle it.
func TestVirtualKeysAreLeftToKeyAuthentication(t *testing.T) {
	f := newVerifierFixture(t)
	for _, token := range []string{"sk-agentcc-test-key", "plain", "a.b.c", ""} {
		p, err := f.verify(t, token, "/mcp")
		if p != nil || err != nil {
			t.Fatalf("Verify(%q) = %+v, %v, want not handled", token, p, err)
		}
	}
}

// No refusal and no log line carries the token or any part of it.
func TestTokenNeverLogged(t *testing.T) {
	var logs bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logs, &slog.HandlerOptions{Level: slog.LevelDebug})))
	t.Cleanup(func() { slog.SetDefault(prev) })

	res := newTestResource(t)
	store := &memStore{fail: true} // every reread fails and is logged
	keys := NewKeySet(testIssuer, store, nil)
	v := NewVerifier(res, keys, nil)
	signer := oauthrstest.NewSigner(t, "as-test")
	token := signer.Sign(t, oauthrstest.AccessClaims(testIssuer, "https://gw.example.test/mcp/v/vs_B", time.Now(), time.Minute))
	_, err := v.Verify(context.Background(), token, "/mcp/v/vs_A")
	if err == nil {
		t.Fatal("a token for a key that was never pushed was accepted")
	}
	parts := strings.Split(token, ".")
	for _, part := range parts {
		if strings.Contains(err.Error(), part) || strings.Contains(logs.String(), part) {
			t.Fatalf("a token segment appears in the refusal %q or the logs %q", err, logs.String())
		}
	}
	if !errors.Is(err, errInvalidToken) {
		t.Fatalf("refusal %v does not wrap the invalid-token error", err)
	}
	var claims map[string]any
	raw, _ := base64.RawURLEncoding.DecodeString(parts[1])
	_ = json.Unmarshal(raw, &claims)
	if strings.Contains(logs.String(), claims["sub"].(string)) {
		t.Fatal("a claim value appears in the logs")
	}
}
