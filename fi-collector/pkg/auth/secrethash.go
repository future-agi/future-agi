package auth

import (
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"
)

// API secrets at rest (READINESS L03). Django stores each Future AGI API secret
// as "hmac-sha256$<pepper id>$<hex HMAC-SHA256(pepper, secret)>" and keeps no
// plaintext (futureagi/accounts/api_secrets.py). The collector verifies the same
// string; TestSecretHashGoldenVector pins the format to Django's.

const (
	secretHashScheme = "hmac-sha256"
	minPepperLength  = 32
	// publishedPepper is the compose default (Django's _PUBLISHED_API_SECRET_PEPPER;
	// futureagi/tfc/tests/test_settings_env.py pins the two equal).
	publishedPepper = "local-dev-only-api-secret-pepper-replace-me"
	// RevocationPrefix marks an fi:auth:revoke payload that names a key by
	// sha256(api_key) instead of the legacy sha256(api_key:secret) cache key.
	RevocationPrefix = "apikey:"
)

var (
	pepperIDLabel    = []byte("futureagi/api-secret-pepper-id/v1\x00")
	localPepperLabel = []byte("futureagi/api-secret-pepper/local-v1")
	absentDigest     = []byte(strings.Repeat("0", 64))

	ErrPepperMissing = errors.New("FI_API_SECRET_PEPPER is required: set it from the secret store, as for the API (local and test installs may derive it from SECRET_KEY instead)")
	// ErrPepperPublished names the problem only, never the configured value.
	ErrPepperPublished = errors.New("FI_API_SECRET_PEPPER is the published default in a non-local environment: set it from the secret store (32+ random characters), as for the API")
)

type pepper struct {
	id  string
	key []byte
}

// SecretPeppers holds the current pepper and every retired one a stored hash
// may still name.
type SecretPeppers struct {
	current pepper
	known   map[string]pepper
}

// PepperID is the first 8 hex characters of a one-way fingerprint of the pepper.
func PepperID(key []byte) string {
	sum := sha256.Sum256(append(append([]byte{}, pepperIDLabel...), key...))
	return hex.EncodeToString(sum[:])[:8]
}

// LocalPepperFromSecretKey mirrors Django: local and test installs without a
// configured pepper derive it from SECRET_KEY.
func LocalPepperFromSecretKey(secretKey string) []byte {
	mac := hmac.New(sha256.New, []byte(secretKey))
	mac.Write(localPepperLabel)
	return []byte(hex.EncodeToString(mac.Sum(nil)))
}

// ResolveSecretPeppers applies Django's rule: the configured pepper, refused
// when it is the published default and ENV_TYPE is not local or test (unset
// means local); else, on a local or test install, one derived from SECRET_KEY;
// else ErrPepperMissing.
func ResolveSecretPeppers(configured string, retired []string, envType, secretKey string) (*SecretPeppers, error) {
	configured = strings.TrimSpace(configured)
	env := strings.ToLower(strings.TrimSpace(envType))
	local := env == "" || env == "local" || env == "test"
	var currentKey []byte
	switch {
	case configured != "":
		if len(configured) < minPepperLength {
			return nil, fmt.Errorf("FI_API_SECRET_PEPPER must be at least %d characters", minPepperLength)
		}
		if configured == publishedPepper && !local {
			return nil, ErrPepperPublished
		}
		currentKey = []byte(configured)
	case local && secretKey != "":
		currentKey = LocalPepperFromSecretKey(secretKey)
	default:
		return nil, ErrPepperMissing
	}
	p := &SecretPeppers{current: pepper{id: PepperID(currentKey), key: currentKey}}
	p.known = map[string]pepper{p.current.id: p.current}
	for _, raw := range retired {
		if raw = strings.TrimSpace(raw); raw != "" {
			old := pepper{id: PepperID([]byte(raw)), key: []byte(raw)}
			if _, ok := p.known[old.id]; !ok {
				p.known[old.id] = old
			}
		}
	}
	return p, nil
}

// CurrentID is the id new hashes carry.
func (p *SecretPeppers) CurrentID() string { return p.current.id }

func (p *SecretPeppers) digest(k pepper, secret string) []byte {
	mac := hmac.New(sha256.New, k.key)
	mac.Write([]byte(secret))
	return []byte(hex.EncodeToString(mac.Sum(nil)))
}

// HashSecret returns the stored form of secret under the current pepper.
func (p *SecretPeppers) HashSecret(secret string) string {
	return secretHashScheme + "$" + p.current.id + "$" + string(p.digest(p.current, secret))
}

// VerifySecret checks a presented secret against one stored row. storedHash and
// legacyPlaintext are both empty for "no such key". Every path computes one HMAC
// and one constant-time compare, as Django's verify_secret does.
func (p *SecretPeppers) VerifySecret(storedHash, legacyPlaintext, presented string) bool {
	if storedHash != "" {
		parts := strings.Split(storedHash, "$")
		if len(parts) == 3 && parts[0] == secretHashScheme && len(parts[2]) == 64 {
			k, known := p.known[parts[1]]
			if !known {
				k = p.current
			}
			ok := subtle.ConstantTimeCompare(p.digest(k, presented), []byte(parts[2])) == 1
			return ok && known
		}
		subtle.ConstantTimeCompare(p.digest(p.current, presented), absentDigest)
		return false
	}
	if legacyPlaintext != "" {
		p.digest(p.current, presented)
		return subtle.ConstantTimeCompare([]byte(legacyPlaintext), []byte(presented)) == 1
	}
	subtle.ConstantTimeCompare(p.digest(p.current, presented), absentDigest)
	return false
}

// APIKeyDigest is the revocation identity of a key: sha256(api_key) in hex.
func APIKeyDigest(apiKey string) string {
	sum := sha256.Sum256([]byte(apiKey))
	return hex.EncodeToString(sum[:])
}
