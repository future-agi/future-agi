package auth

import (
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"regexp"
	"strconv"
	"strings"
	"time"

	"golang.org/x/crypto/hkdf"
	"io"
)

// Principal is assigned by a verifier, not by an API key's database type.
type Principal string

const (
	PrincipalPersonal Principal = "personal"
	PrincipalSandbox  Principal = "sandbox"
	PrincipalPlatform Principal = "platform"
)

// GrantClaims is the collector subset of the fe1 19-field wire contract.
// Mode, issuer, method, route, body and grant are constants for these grants.
type GrantClaims struct {
	ID        string `json:"jti"`
	Kind      string `json:"kind"`
	Kid       string `json:"kid"`
	Aud       string `json:"aud"`
	Org       string `json:"org"`
	Workspace string `json:"workspace"`
	User      string `json:"user"`
	Via       string `json:"via"`
	Sub       string `json:"sub"`
	Scope     string `json:"scope"`
	Iat       int64  `json:"iat"`
	Exp       int64  `json:"exp"`
}

var (
	grantPublicID = regexp.MustCompile(`^fit_[0-9a-f]{32}$`)
	envelopeKid   = regexp.MustCompile(`^k[0-9]{1,3}$`)
	envelopeUUID  = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	envelopeTime  = regexp.MustCompile(`^(0|[1-9][0-9]{0,10})$`)
	envelopeB64   = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)
)

func decodeEnvelopeSegment(s string) ([]byte, error) {
	if !envelopeB64.MatchString(s) {
		return nil, ErrUnauthenticated
	}
	raw, err := base64.RawURLEncoding.Strict().DecodeString(s)
	if err != nil || base64.RawURLEncoding.EncodeToString(raw) != s {
		return nil, ErrUnauthenticated
	}
	return raw, nil
}

// VerifyCollectorGrant accepts only the two collector audiences. A valid MAC
// for a request envelope or another service's grant confers no collector access.
func VerifyCollectorGrant(apiKey, token string, peppers *SecretPeppers, kids []string, now time.Time) (*GrantClaims, error) {
	if peppers == nil {
		return nil, ErrPepperMissing
	}
	if len(token) > 2048 || !grantPublicID.MatchString(apiKey) {
		return nil, ErrUnauthenticated
	}
	parts := strings.Split(token, ".")
	if len(parts) != 3 || parts[0] != "fe1" {
		return nil, ErrUnauthenticated
	}
	canonical, err := decodeEnvelopeSegment(parts[1])
	if err != nil {
		return nil, err
	}
	mac, err := decodeEnvelopeSegment(parts[2])
	if err != nil || len(mac) != sha256.Size {
		return nil, ErrUnauthenticated
	}
	f := strings.Split(string(canonical), "\n")
	if len(f) != 19 || f[0] != "fe1" || f[1] != "grant" || f[3] != "fa" || f[5] != "-" || f[6] != "-" || f[17] != "-" || f[18] != "-" {
		return nil, ErrUnauthenticated
	}
	if !envelopeKid.MatchString(f[2]) || !envelopeTime.MatchString(f[7]) || !envelopeTime.MatchString(f[8]) || apiKey != "fit_"+f[9] || !envelopeUUID.MatchString(f[11]) {
		return nil, ErrUnauthenticated
	}
	known := false
	for _, kid := range kids {
		known = known || kid == f[2]
	}
	if !known {
		return nil, ErrUnauthenticated
	}
	for _, value := range []string{f[12], f[13]} {
		if value != "-" && !envelopeUUID.MatchString(value) {
			return nil, ErrUnauthenticated
		}
	}
	iat, _ := strconv.ParseInt(f[7], 10, 64)
	exp, _ := strconv.ParseInt(f[8], 10, 64)
	if exp <= iat || now.Unix() < iat-5 || now.Unix() >= exp {
		return nil, ErrUnauthenticated
	}
	c := &GrantClaims{ID: f[9], Kind: f[10], Kid: f[2], Aud: f[4], Org: f[11], Workspace: f[12], User: f[13], Via: f[14], Sub: f[15], Scope: f[16], Iat: iat, Exp: exp}
	if !c.validKind() {
		return nil, ErrUnauthenticated
	}
	valid := 0
	// Try every configured pepper, including the rotation overlap. The kid is
	// only a configured label; it is never derived from any pepper's digest.
	for _, p := range peppers.known {
		key := make([]byte, 32)
		if _, err := io.ReadFull(hkdf.New(sha256.New, p.key, []byte("fe1"), []byte("fa>"+c.Aud+"|"+c.Kid)), key); err != nil {
			return nil, err
		}
		h := hmac.New(sha256.New, key)
		_, _ = h.Write(canonical)
		valid |= subtle.ConstantTimeCompare(h.Sum(nil), mac)
	}
	if valid != 1 {
		return nil, ErrUnauthenticated
	}
	return c, nil
}

func (c *GrantClaims) validKind() bool {
	if c.Scope != "traces" {
		return false
	}
	switch c.Kind {
	case "sandbox":
		s := strings.Split(c.Sub, ":")
		return c.Aud == "collector-ingest" && c.Via == "person" && envelopeUUID.MatchString(c.User) && (c.Workspace == "-" || envelopeUUID.MatchString(c.Workspace)) && len(s) == 3 && s[0] == "attempt" && envelopeUUID.MatchString(s[1]) && envelopeUUID.MatchString(s[2]) && c.Exp-c.Iat <= 86400
	case "platform-emit":
		return c.Aud == "collector-emit" && c.Via == "svc" && c.User == "-" && (c.Sub == "emit:provider" || c.Sub == "emit:voice" || c.Sub == "emit:sim") && c.Exp-c.Iat <= 60
	default:
		return false
	}
}
