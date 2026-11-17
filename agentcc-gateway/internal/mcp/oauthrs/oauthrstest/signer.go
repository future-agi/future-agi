// Package oauthrstest mints test tokens for the MCP resource server: an
// ES256 signer standing in for the authorization server, its public key as a
// JWK, and mcp-oauth-keys-push-v1 bodies. Every key is generated per test
// run; none is a real key.
package oauthrstest

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"testing"
	"time"
)

// Signer signs tokens with one fresh P-256 key under kid.
type Signer struct {
	Kid string
	key *ecdsa.PrivateKey
}

// NewSigner returns a signer with a freshly generated key.
func NewSigner(t testing.TB, kid string) *Signer {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatalf("generating a test key: %v", err)
	}
	return &Signer{Kid: kid, key: key}
}

// JWK is the signer's public key as a push carries it.
func (s *Signer) JWK() map[string]any {
	raw, err := s.key.PublicKey.Bytes()
	if err != nil {
		panic(err)
	}
	return map[string]any{
		"kty": "EC", "crv": "P-256", "kid": s.Kid, "use": "sig", "alg": "ES256",
		"x": b64(raw[1:33]), "y": b64(raw[33:65]),
	}
}

// Sign returns an ES256 JWT with header {alg: ES256, kid} and claims.
func (s *Signer) Sign(t testing.TB, claims map[string]any) string {
	t.Helper()
	return s.SignWithHeader(t, map[string]any{"alg": "ES256", "kid": s.Kid, "typ": "JWT"}, claims)
}

// SignWithHeader returns a JWT with the given header and claims, signed with
// ES256 whatever the header says.
func (s *Signer) SignWithHeader(t testing.TB, header, claims map[string]any) string {
	t.Helper()
	input := segment(t, header) + "." + segment(t, claims)
	digest := sha256.Sum256([]byte(input))
	r, sv, err := ecdsa.Sign(rand.Reader, s.key, digest[:])
	if err != nil {
		t.Fatalf("signing a test token: %v", err)
	}
	sig := make([]byte, 64)
	r.FillBytes(sig[:32])
	sv.FillBytes(sig[32:])
	return input + "." + b64(sig)
}

// AccessClaims are valid access-token claims for audience from issuer, issued
// now and living lifetime.
func AccessClaims(issuer, audience string, now time.Time, lifetime time.Duration) map[string]any {
	return map[string]any{
		"typ":       "futureagi-mcp-access",
		"iss":       issuer,
		"aud":       audience,
		"sub":       "user-oauth-test",
		"org_id":    "org-oauth-test",
		"client_id": "client-oauth-test",
		"scope":     "mcp:tools mcp:resources mcp:prompts",
		"iat":       now.Unix(),
		"nbf":       now.Unix(),
		"exp":       now.Add(lifetime).Unix(),
		"jti":       "jti-oauth-test",
	}
}

// PushBody is a mcp-oauth-keys-push-v1 body for issuer at version with the
// signers' public keys.
func PushBody(t testing.TB, issuer string, version int64, signers ...*Signer) []byte {
	t.Helper()
	keys := make([]map[string]any, 0, len(signers))
	for _, s := range signers {
		keys = append(keys, s.JWK())
	}
	body, err := json.Marshal(map[string]any{"issuer": issuer, "version": version, "keys": keys})
	if err != nil {
		t.Fatalf("encoding a test push: %v", err)
	}
	return body
}

func segment(t testing.TB, v any) string {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("encoding a test token segment: %v", err)
	}
	return b64(raw)
}

func b64(b []byte) string { return base64.RawURLEncoding.EncodeToString(b) }
