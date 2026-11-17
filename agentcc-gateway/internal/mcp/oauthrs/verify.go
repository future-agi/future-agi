package oauthrs

import (
	"context"
	"crypto/ecdsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// Access tokens (mcp-access-token-claims-v1, access): ES256 JWTs the
// authorization server issues for one virtual server.
const (
	// TypeAccess is an access token's typ claim.
	TypeAccess = "futureagi-mcp-access"
	// MaxAccessLifetime caps exp - iat: revocation acts at refresh, so a
	// token outlives a revoked consent by at most this (decision 37).
	MaxAccessLifetime = 900 * time.Second
	// clockSkew is how far ahead of this gateway's clock iat and nbf may be.
	clockSkew = 30 * time.Second
	// maxTokenBytes bounds the work spent on one token.
	maxTokenBytes = 8 << 10
)

// errInvalidToken is every refusal. Its wrapped reason names the check that
// failed and never repeats the token or a claim value.
var errInvalidToken = errors.New("the bearer token is not valid for this resource")

func refuse(reason string) error { return fmt.Errorf("%w: %s", errInvalidToken, reason) }

// Verifier is the MCP server's mcp.BearerVerifier on a gateway that is an
// OAuth resource server. It accepts an access token only when the
// authorization server signed it with a key it pushed, for exactly the
// resource the request addresses, and only while it is fresh; the caller's
// org, user and client come from the token's signed claims only.
type Verifier struct {
	res   *Resource
	keys  *KeySet
	clock func() time.Time
}

// NewVerifier returns the verifier for res with the keys in keys. clock
// defaults to time.Now.
func NewVerifier(res *Resource, keys *KeySet, clock func() time.Time) *Verifier {
	if clock == nil {
		clock = time.Now
	}
	return &Verifier{res: res, keys: keys, clock: clock}
}

// Resource is the resource the verifier accepts tokens for.
func (v *Verifier) Resource() *Resource { return v.res }

// PushKeys applies a key push (PUT /-/mcp/oauth/keys).
func (v *Verifier) PushKeys(ctx context.Context, body []byte) (PushResult, error) {
	return v.keys.Push(ctx, body)
}

// looksLikeJWT is the default verifier's test (mcp.rejectJWTs): anything
// else is left to key authentication, exactly as before.
func looksLikeJWT(token string) bool {
	return strings.Count(token, ".") == 2 && strings.HasPrefix(token, "eyJ")
}

type tokenHeader struct {
	Alg  string          `json:"alg"`
	Kid  string          `json:"kid"`
	Crit json.RawMessage `json:"crit"`
}

type accessClaims struct {
	Typ      string          `json:"typ"`
	Iss      string          `json:"iss"`
	Aud      json.RawMessage `json:"aud"`
	Sub      string          `json:"sub"`
	OrgID    string          `json:"org_id"`
	ClientID string          `json:"client_id"`
	Scope    string          `json:"scope"`
	Iat      *int64          `json:"iat"`
	Nbf      *int64          `json:"nbf"`
	Exp      *int64          `json:"exp"`
	Jti      string          `json:"jti"`
}

// Verify checks token for the request path audiencePath (mcp.verifyBearer
// passes the path the router served, which is canonical). A token that is
// not JWT-shaped is not handled (nil, nil) and is tried as a virtual key; a
// JWT-shaped one is either accepted or refused, never tried as a key.
func (v *Verifier) Verify(ctx context.Context, token, audiencePath string) (*mcp.Principal, error) {
	if !looksLikeJWT(token) {
		return nil, nil
	}
	if len(token) > maxTokenBytes {
		return nil, refuse("too long")
	}
	parts := strings.Split(token, ".")
	var h tokenHeader
	if err := decodeSegment(parts[0], &h); err != nil {
		return nil, refuse("header")
	}
	switch {
	case h.Alg != "ES256":
		return nil, refuse("alg")
	case h.Kid == "":
		return nil, refuse("kid")
	case h.Crit != nil:
		return nil, refuse("crit")
	}
	key, ok := v.keys.Lookup(ctx, h.Kid)
	if !ok {
		return nil, refuse("unknown key")
	}
	if !verifyES256(key, parts[0]+"."+parts[1], parts[2]) {
		return nil, refuse("signature")
	}
	var c accessClaims
	if err := decodeSegment(parts[1], &c); err != nil {
		return nil, refuse("claims")
	}
	if err := v.checkClaims(&c, audiencePath); err != nil {
		return nil, err
	}
	return &mcp.Principal{
		Kind:     mcp.PrincipalOAuth,
		UserID:   c.Sub,
		OrgID:    c.OrgID,
		ClientID: c.ClientID,
		Scopes:   strings.Fields(c.Scope),
		TokenID:  c.Jti,
	}, nil
}

// checkClaims checks a signed token's claims: type, issuer, audience,
// validity window and lifetime, and the identity it must name.
func (v *Verifier) checkClaims(c *accessClaims, audiencePath string) error {
	if c.Typ != TypeAccess {
		return refuse("typ")
	}
	if c.Iss != v.res.Issuer() {
		return refuse("iss")
	}
	want, ok := v.res.ResourceForPath(audiencePath)
	var aud string
	if !ok || json.Unmarshal(c.Aud, &aud) != nil || aud != want {
		return refuse("aud")
	}
	if c.Iat == nil || c.Nbf == nil || c.Exp == nil {
		return refuse("iat, nbf and exp are required")
	}
	now := v.clock()
	iat, nbf, exp := time.Unix(*c.Iat, 0), time.Unix(*c.Nbf, 0), time.Unix(*c.Exp, 0)
	switch {
	case !now.Before(exp):
		return refuse("expired")
	case nbf.After(now.Add(clockSkew)), iat.After(now.Add(clockSkew)):
		return refuse("not yet valid")
	case !exp.After(iat), exp.Sub(iat) > MaxAccessLifetime:
		return refuse("lifetime")
	}
	if c.Sub == "" || c.OrgID == "" || c.ClientID == "" || c.Jti == "" || len(strings.Fields(c.Scope)) == 0 {
		return refuse("sub, org_id, client_id, scope and jti are required")
	}
	return nil
}

// decodeSegment decodes one base64url JWT segment as JSON into out.
func decodeSegment(seg string, out any) error {
	raw, err := base64.RawURLEncoding.DecodeString(seg)
	if err != nil {
		return err
	}
	return json.Unmarshal(raw, out)
}

// verifyES256 checks an ES256 signature: r and s as two 32-byte big-endian
// values (RFC 7518 section 3.4), over the SHA-256 of the signing input.
func verifyES256(key *ecdsa.PublicKey, signingInput, sigSeg string) bool {
	sig, err := base64.RawURLEncoding.DecodeString(sigSeg)
	if err != nil || len(sig) != 64 {
		return false
	}
	digest := sha256.Sum256([]byte(signingInput))
	r := new(big.Int).SetBytes(sig[:32])
	s := new(big.Int).SetBytes(sig[32:])
	return ecdsa.Verify(key, digest[:], r, s)
}
