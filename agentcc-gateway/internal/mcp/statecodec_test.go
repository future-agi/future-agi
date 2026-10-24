package mcp

import (
	"bytes"
	"encoding/base64"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

const (
	stateTestKeyA = "mcp-test-state-key-A-0123456789ab"
	stateTestKeyB = "mcp-test-state-key-B-0123456789ab"
	// testStateKeyGuess is a fixed key string the ephemeral dev key must never equal.
	testStateKeyGuess = "agentcc-dev-insecure-state-key-0000"
)

func newTestStateCodec(t *testing.T, keys ...string) *StateCodec {
	t.Helper()
	c, err := NewStateCodec(keys, false)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

// stateFlipByte returns token with one byte of its decoded form changed.
func stateFlipByte(t *testing.T, token string, i int) string {
	t.Helper()
	raw, err := base64.RawURLEncoding.DecodeString(token)
	if err != nil {
		t.Fatal(err)
	}
	if i < 0 {
		i += len(raw)
	}
	raw[i] ^= 0x01
	return base64.RawURLEncoding.EncodeToString(raw)
}

// TestStateCodecRoundTripTamperExpiryRotation: a sealed payload opens for its
// own purpose only; any changed byte, a truncation or garbage is refused with
// the one invalid error; an expired token is refused as expired; and after a
// rotation the previous key still opens old tokens while new ones seal under
// the current key, which the old key alone cannot open.
func TestStateCodecRoundTripTamperExpiryRotation(t *testing.T) {
	c := newTestStateCodec(t, stateTestKeyA)
	payload := []byte(`{"org":"org-1","principal":"key:abc","vs":"vs_01"}`)

	for _, purpose := range statePurposes {
		token, err := c.Seal(purpose, payload, time.Minute)
		if err != nil {
			t.Fatalf("%s: Seal: %v", purpose, err)
		}
		if strings.ContainsAny(token, "+/=") {
			t.Errorf("%s: token %q is not unpadded base64url", purpose, token)
		}
		if strings.Contains(token, "org-1") || bytes.Contains([]byte(token), []byte(base64.RawURLEncoding.EncodeToString(payload))) {
			t.Errorf("%s: the payload is readable in the token", purpose)
		}
		got, err := c.Open(purpose, token)
		if err != nil || !bytes.Equal(got, payload) {
			t.Fatalf("%s: Open = %q, %v", purpose, got, err)
		}
		for _, other := range statePurposes {
			if other == purpose {
				continue
			}
			if _, err := c.Open(other, token); !errors.Is(err, ErrStateInvalid) {
				t.Errorf("a %s token opened as %s: %v", purpose, other, err)
			}
		}
	}

	// Each purpose has its own derived key, not only its own additional data:
	// the same nonce, plaintext and additional data seal differently.
	nonce := make([]byte, 12)
	seen := map[string]string{}
	for _, purpose := range statePurposes {
		ct := string(c.keys[0][purpose].Seal(nil, nonce, payload, []byte("aad")))
		if other, dup := seen[ct]; dup {
			t.Errorf("purposes %s and %s share a key", other, purpose)
		}
		seen[ct] = purpose
	}

	// Two seals of one payload differ (random nonce), and an empty payload
	// round-trips.
	t1, _ := c.Seal(StatePurposeCursor, payload, time.Minute)
	t2, _ := c.Seal(StatePurposeCursor, payload, time.Minute)
	if t1 == t2 {
		t.Error("two seals of the same payload are identical")
	}
	if empty, err := c.Seal(StatePurposeCursor, nil, time.Minute); err != nil {
		t.Fatal(err)
	} else if got, err := c.Open(StatePurposeCursor, empty); err != nil || len(got) != 0 {
		t.Errorf("empty payload: Open = %q, %v", got, err)
	}

	// Tampering: every byte position, plus truncation, extension and junk.
	token, _ := c.Seal(StatePurposeSession, payload, time.Minute)
	raw, _ := base64.RawURLEncoding.DecodeString(token)
	for i := range raw {
		if _, err := c.Open(StatePurposeSession, stateFlipByte(t, token, i)); !errors.Is(err, ErrStateInvalid) {
			t.Fatalf("byte %d changed: Open = %v, want ErrStateInvalid", i, err)
		}
	}
	for name, bad := range map[string]string{
		"truncated":  token[:len(token)-4],
		"nonce only": base64.RawURLEncoding.EncodeToString(raw[:13]),
		"extended":   token + "AA",
		"padded":     token + "=",
		"not base64": "!!not-a-token!!",
		"empty":      "",
		"version 2":  base64.RawURLEncoding.EncodeToString(append([]byte{2}, raw[1:]...)),
	} {
		if _, err := c.Open(StatePurposeSession, bad); !errors.Is(err, ErrStateInvalid) {
			t.Errorf("%s: Open = %v, want ErrStateInvalid", name, err)
		}
	}

	// Another key's token does not open.
	if _, err := newTestStateCodec(t, stateTestKeyB).Open(StatePurposeSession, token); !errors.Is(err, ErrStateInvalid) {
		t.Errorf("a token opened under another key: %v", err)
	}

	// Expiry is inside the sealed bytes and checked on open.
	now := time.Date(2026, 10, 16, 12, 0, 0, 0, time.UTC)
	c.now = func() time.Time { return now }
	short, err := c.Seal(StatePurposeApproval, payload, 10*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	now = now.Add(9 * time.Second)
	if _, err := c.Open(StatePurposeApproval, short); err != nil {
		t.Errorf("before expiry: %v", err)
	}
	now = now.Add(time.Second)
	if _, err := c.Open(StatePurposeApproval, short); !errors.Is(err, ErrStateExpired) {
		t.Errorf("at expiry: Open = %v, want ErrStateExpired", err)
	}
	for _, ttl := range []time.Duration{0, -time.Second} {
		if _, err := c.Seal(StatePurposeApproval, payload, ttl); err == nil {
			t.Errorf("Seal accepted ttl %s", ttl)
		}
	}

	// Rotation: B is current, A is still accepted.
	old := newTestStateCodec(t, stateTestKeyA)
	oldToken, _ := old.Seal(StatePurposeSession, payload, time.Minute)
	rotated := newTestStateCodec(t, stateTestKeyB, stateTestKeyA)
	if got, err := rotated.Open(StatePurposeSession, oldToken); err != nil || !bytes.Equal(got, payload) {
		t.Errorf("the previous key does not open its token: %q, %v", got, err)
	}
	newToken, _ := rotated.Seal(StatePurposeSession, payload, time.Minute)
	if _, err := old.Open(StatePurposeSession, newToken); !errors.Is(err, ErrStateInvalid) {
		t.Errorf("a token sealed after rotation opened under the previous key alone: %v", err)
	}
	if _, err := newTestStateCodec(t, stateTestKeyB).Open(StatePurposeSession, newToken); err != nil {
		t.Errorf("a token sealed after rotation does not open under the current key: %v", err)
	}
	// Once A is retired its tokens stop opening.
	if _, err := newTestStateCodec(t, stateTestKeyB).Open(StatePurposeSession, oldToken); !errors.Is(err, ErrStateInvalid) {
		t.Errorf("a retired key's token still opens: %v", err)
	}

	// Unknown purposes are refused both ways.
	if _, err := c.Seal("sessions", payload, time.Minute); err == nil {
		t.Error("Seal accepted an unknown purpose")
	}
	if _, err := c.Open("sessions", token); err == nil || errors.Is(err, ErrStateExpired) {
		t.Errorf("Open with an unknown purpose = %v", err)
	}
}

// TestStateCodecRefusesMissingKey: without a state key the codec refuses to
// build and a gateway with MCP enabled refuses to load its config; a key
// shorter than 32 bytes is refused; dev_insecure_state, for tests, seals with
// an ephemeral key. No error prints a key.
func TestStateCodecRefusesMissingKey(t *testing.T) {
	for _, keys := range [][]string{nil, {}} {
		if c, err := NewStateCodec(keys, false); !errors.Is(err, ErrStateKeyMissing) || c != nil {
			t.Errorf("NewStateCodec(%v) = %v, %v; want ErrStateKeyMissing", keys, c, err)
		}
	}
	short := "only-31-bytes-of-key-material!!"
	if _, err := NewStateCodec([]string{stateTestKeyA, short}, false); err == nil || strings.Contains(err.Error(), short) {
		t.Errorf("a short previous key: %v", err)
	}

	dev, err := NewStateCodec(nil, true)
	if err != nil {
		t.Fatal(err)
	}
	token, err := dev.Seal(StatePurposeSession, []byte("x"), time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	if got, err := dev.Open(StatePurposeSession, token); err != nil || string(got) != "x" {
		t.Errorf("dev codec round trip: %q, %v", got, err)
	}
	// Codecs built without a key share the process's ephemeral key, so one
	// consumer opens another's tokens; the key is not a fixed, guessable one.
	other, _ := NewStateCodec(nil, true)
	if got, err := other.Open(StatePurposeSession, token); err != nil || string(got) != "x" {
		t.Errorf("a second dev codec cannot open the first one's token: %q, %v", got, err)
	}
	for _, guess := range []string{strings.Repeat("\x00", 32), strings.Repeat("0", 32), testStateKeyGuess} {
		if _, err := newTestStateCodec(t, guess).Open(StatePurposeSession, token); !errors.Is(err, ErrStateInvalid) {
			t.Errorf("the dev key is the guessable %q: %v", guess, err)
		}
	}

	// The gateway refuses to start with MCP enabled and no key.
	t.Setenv(config.EnvMCPStateKeys, "")
	if err := os.Unsetenv(config.EnvMCPStateKeys); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "config.yaml")
	write := func(body string) {
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write("mcp:\n  enabled: true\n")
	if _, err := config.Load(path); err == nil || !strings.Contains(err.Error(), config.EnvMCPStateKeys) {
		t.Fatalf("config.Load with mcp.enabled and no state key = %v, want a refusal", err)
	}
	write("mcp:\n  enabled: true\n  dev_insecure_state: true\n")
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("dev_insecure_state did not allow a keyless start: %v", err)
	}
	if _, err := NewStateCodec(cfg.MCP.StateKeys, cfg.MCP.DevInsecureState); err != nil {
		t.Errorf("codec from the dev config: %v", err)
	}
	t.Setenv(config.EnvMCPStateKeys, stateTestKeyA+","+stateTestKeyB)
	write("mcp:\n  enabled: true\n")
	cfg, err = config.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := NewStateCodec(cfg.MCP.StateKeys, cfg.MCP.DevInsecureState); err != nil {
		t.Errorf("codec from the env keys: %v", err)
	}
}

// TestStateCodecZeroValueFailsClosed: a codec not built by NewStateCodec
// refuses to seal or open instead of panicking or sealing with no key.
func TestStateCodecZeroValueFailsClosed(t *testing.T) {
	for _, c := range []*StateCodec{nil, {}} {
		if _, err := c.Seal(StatePurposeSession, []byte("x"), time.Minute); !errors.Is(err, ErrStateKeyMissing) {
			t.Errorf("Seal on %#v = %v", c, err)
		}
		if _, err := c.Open(StatePurposeSession, "AQ"); !errors.Is(err, ErrStateKeyMissing) {
			t.Errorf("Open on %#v = %v", c, err)
		}
	}
}
