package mcp

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"
)

// State purposes. Each seals with its own derived key, so a token sealed for
// one purpose never opens as another.
const (
	StatePurposeSession      = "session"
	StatePurposeCursor       = "cursor"
	StatePurposeRequestState = "request_state"
	StatePurposeTokenCache   = "token_cache"
	StatePurposeApproval     = "approval"
	StatePurposeConnect      = "connect"
)

var statePurposes = []string{
	StatePurposeSession, StatePurposeCursor, StatePurposeRequestState,
	StatePurposeTokenCache, StatePurposeApproval, StatePurposeConnect,
}

// minStateKeyBytes matches config.MinMCPStateKeyBytes.
const minStateKeyBytes = 32

var (
	// ErrStateKeyMissing: MCP state cannot be sealed without a key.
	ErrStateKeyMissing = errors.New("mcp: no state key configured (AGENTCC_MCP_STATE_KEYS or mcp.state_keys)")
	// ErrStateInvalid: the token is malformed, altered, sealed under a key
	// that is not accepted, or sealed for another purpose. One error for all,
	// so a caller learns nothing about why.
	ErrStateInvalid = errors.New("mcp: state token invalid")
	// ErrStateExpired: the token is authentic but past its expiry.
	ErrStateExpired = errors.New("mcp: state token expired")
)

// Token layout, base64url without padding:
//
//	version (1 byte, 1) | nonce (12 bytes) | AES-256-GCM(expiry || payload)
//
// expiry is unix milliseconds, 8 bytes big-endian. The AEAD key for a purpose
// is HKDF-SHA256(state key, salt = stateHKDFSalt, info = stateHKDFInfo +
// purpose), and the additional data is the version byte and the purpose.
const (
	stateTokenVersion = 1
	stateHKDFSalt     = "agentcc-mcp-state"
	stateHKDFInfo     = "agentcc-mcp-state/v1/"
	stateExpiryBytes  = 8
)

// StateCodec seals and opens the MCP state handed to clients: session ids,
// list cursors, request state, cached upstream tokens, approval ids and
// connect bindings. Tokens are authenticated and encrypted;
// they and their payloads must never be logged. It is safe for concurrent
// use.
type StateCodec struct {
	// keys[i][purpose] is the AEAD for state key i, current key first.
	keys []map[string]cipher.AEAD
	now  func() time.Time
}

// devStateKey is the process's ephemeral key for mcp.dev_insecure_state,
// shared by every codec built without a key so that their tokens interoperate
// within the process.
var devStateKey = sync.OnceValues(func() (string, error) {
	key := make([]byte, minStateKeyBytes)
	if _, err := rand.Read(key); err != nil {
		return "", fmt.Errorf("mcp: generating an ephemeral state key: %w", err)
	}
	slog.Warn("mcp state is sealed with an ephemeral key (mcp.dev_insecure_state): sessions and cursors do not survive a restart or reach another replica; never use this in production")
	return string(key), nil
})

// NewStateCodec builds a codec from the state keys, current first; the others
// still open tokens sealed before a rotation. Each key is at least 32 bytes of
// input key material. Codecs built from the same keys open each other's
// tokens. With no key it refuses (ErrStateKeyMissing) unless devInsecure is
// set, which seals with a random key that lives only as long as the process:
// tests only.
func NewStateCodec(keys []string, devInsecure bool) (*StateCodec, error) {
	if len(keys) == 0 {
		if !devInsecure {
			return nil, ErrStateKeyMissing
		}
		key, err := devStateKey()
		if err != nil {
			return nil, err
		}
		keys = []string{key}
	}
	c := &StateCodec{now: time.Now}
	for i, k := range keys {
		if len(k) < minStateKeyBytes {
			return nil, fmt.Errorf("mcp: state key %d is %d bytes; each must be at least %d", i, len(k), minStateKeyBytes)
		}
		aeads := make(map[string]cipher.AEAD, len(statePurposes))
		for _, purpose := range statePurposes {
			key, err := hkdf.Key(sha256.New, []byte(k), []byte(stateHKDFSalt), stateHKDFInfo+purpose, 32)
			if err != nil {
				return nil, fmt.Errorf("mcp: deriving the %s state key: %w", purpose, err)
			}
			block, err := aes.NewCipher(key)
			if err != nil {
				return nil, err
			}
			aead, err := cipher.NewGCM(block)
			if err != nil {
				return nil, err
			}
			aeads[purpose] = aead
		}
		c.keys = append(c.keys, aeads)
	}
	return c, nil
}

func stateAAD(purpose string) []byte {
	return append([]byte{stateTokenVersion}, purpose...)
}

// Seal encrypts payload for purpose under the current key, with an expiry
// ttl from now inside the sealed bytes.
func (c *StateCodec) Seal(purpose string, payload []byte, ttl time.Duration) (string, error) {
	if c == nil || len(c.keys) == 0 {
		return "", ErrStateKeyMissing
	}
	aead, ok := c.keys[0][purpose]
	if !ok {
		return "", fmt.Errorf("mcp: unknown state purpose %q", purpose)
	}
	if ttl <= 0 {
		return "", fmt.Errorf("mcp: state ttl must be positive, got %s", ttl)
	}
	plain := make([]byte, stateExpiryBytes, stateExpiryBytes+len(payload))
	binary.BigEndian.PutUint64(plain, uint64(c.now().Add(ttl).UnixMilli()))
	plain = append(plain, payload...)

	out := make([]byte, 1+aead.NonceSize(), 1+aead.NonceSize()+len(plain)+aead.Overhead())
	out[0] = stateTokenVersion
	if _, err := rand.Read(out[1:]); err != nil {
		return "", fmt.Errorf("mcp: state nonce: %w", err)
	}
	out = aead.Seal(out, out[1:], plain, stateAAD(purpose))
	return base64.RawURLEncoding.EncodeToString(out), nil
}

// Open authenticates and decrypts a token sealed for purpose by any accepted
// key, and returns its payload: ErrStateInvalid if it does not open,
// ErrStateExpired if it opens but has expired.
func (c *StateCodec) Open(purpose, token string) ([]byte, error) {
	if c == nil || len(c.keys) == 0 {
		return nil, ErrStateKeyMissing
	}
	if _, ok := c.keys[0][purpose]; !ok {
		return nil, fmt.Errorf("mcp: unknown state purpose %q", purpose)
	}
	raw, err := base64.RawURLEncoding.DecodeString(token)
	if err != nil {
		return nil, ErrStateInvalid
	}
	nonceSize := c.keys[0][purpose].NonceSize()
	if len(raw) < 1+nonceSize || raw[0] != stateTokenVersion {
		return nil, ErrStateInvalid
	}
	nonce, sealed := raw[1:1+nonceSize], raw[1+nonceSize:]
	for _, aeads := range c.keys {
		plain, err := aeads[purpose].Open(nil, nonce, sealed, stateAAD(purpose))
		if err != nil {
			continue
		}
		if len(plain) < stateExpiryBytes {
			return nil, ErrStateInvalid
		}
		expiry := int64(binary.BigEndian.Uint64(plain[:stateExpiryBytes]))
		if c.now().UnixMilli() >= expiry {
			return nil, ErrStateExpired
		}
		return plain[stateExpiryBytes:], nil
	}
	return nil, ErrStateInvalid
}
