package vendors_test

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hkdf"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
)

type tokenRow struct {
	value, refusal        string
	expired, channelError bool
}
type controlPlane struct {
	mu       sync.Mutex
	url      string
	rows     map[credentials.Key]tokenRow
	requests []credentials.Key
	cleanup  map[credentials.Key]string
}

func newControlPlane(t *testing.T) *controlPlane {
	t.Helper()
	c := &controlPlane{rows: map[credentials.Key]tokenRow{}, cleanup: map[credentials.Key]string{}}
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != credentials.UpstreamTokenPath || r.Method != "POST" || r.Header.Get(credentials.InternalSecretHeader) != channelSecret || r.Header.Get("Authorization") != "" {
			http.Error(w, "synthetic channel contract", 400)
			return
		}
		var req struct {
			Org    string `json:"org_id"`
			User   string `json:"user_id"`
			Server string `json:"server_id"`
			Scope  string `json:"scope"`
		}
		if json.NewDecoder(r.Body).Decode(&req) != nil {
			http.Error(w, "bad request", 400)
			return
		}
		k := credentials.Key{OrgID: req.Org, UserID: req.User, ServerID: req.Server, Shared: req.Scope == "shared"}
		c.mu.Lock()
		c.requests = append(c.requests, k)
		row, ok := c.rows[k]
		c.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		if row.channelError {
			http.Error(w, "synthetic channel outage", 503)
			return
		}
		if !ok {
			row.refusal = credentials.RefusalNoCredential
		}
		if row.refusal != "" {
			json.NewEncoder(w).Encode(map[string]any{"error": row.refusal, "connect_hint": c.url + credentials.ConnectStartPath(req.Server)})
			return
		}
		exp := time.Now().Add(time.Hour)
		if row.expired {
			exp = time.Now().Add(-time.Minute)
		}
		json.NewEncoder(w).Encode(map[string]any{"access_token": row.value, "token_type": "Bearer", "expires_at": exp.UTC().Format(time.RFC3339), "credential_version": 1})
	}))
	c.url = s.URL
	t.Cleanup(s.Close)
	return c
}
func key(org, user string) credentials.Key {
	return credentials.Key{OrgID: org, UserID: user, ServerID: "service"}
}
func (c *controlPlane) set(k credentials.Key, row tokenRow) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.rows[k] = row
}
func (c *controlPlane) fetched() []credentials.Key {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]credentials.Key(nil), c.requests...)
}
func (c *controlPlane) revoke(k credentials.Key) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.rows[k] = tokenRow{refusal: credentials.RefusalRevoked}
	c.cleanup[k] = "failed_upstream_cleanup"
}
func (f *fixture) give(t *testing.T, org, user string) string {
	t.Helper()
	var reply struct {
		Access string `json:"access_token"`
	}
	raw, _ := json.Marshal(map[string]any{"sub": org + "/" + user, "ttl_s": 3600})
	resp, err := http.Post(f.upstream+"/__control/oauth/token", "application/json", bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatal("synthetic OAuth mint failed")
	}
	if err := json.NewDecoder(resp.Body).Decode(&reply); err != nil || reply.Access == "" {
		t.Fatal("synthetic OAuth token missing")
	}
	f.cp.set(key(org, user), tokenRow{value: reply.Access})
	return reply.Access
}

// oauthGrant exercises the existing mock's standard authorization-code/PKCE
// contract with either a CIMD-shaped URL client id or a DCR-issued-shaped id.
// The mock does not implement real client registration/discovery; those remain
// control-plane integration obligations rather than fabricated capabilities.
func (f *fixture) oauthGrant(t *testing.T, clientID string) string {
	t.Helper()
	verifier := strings.Repeat("w", 50)
	sum := sha256.Sum256([]byte(verifier))
	q := url.Values{"response_type": {"code"}, "client_id": {clientID}, "redirect_uri": {"http://127.0.0.1:9/callback"},
		"resource": {f.upstream + "/mcp"}, "scope": {"mcp"}, "state": {"w4b-state"}, "code_challenge_method": {"S256"}, "code_challenge": {base64.RawURLEncoding.EncodeToString(sum[:])}}
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	resp, err := client.Get(f.upstream + "/authorize?" + q.Encode())
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode == http.StatusOK {
		q.Set("decision", "approve")
		resp, err = client.PostForm(f.upstream+"/authorize", q)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
	}
	if resp.StatusCode != http.StatusFound {
		t.Fatal("mock consent did not redirect")
	}
	u, err := url.Parse(resp.Header.Get("Location"))
	if err != nil {
		t.Fatal(err)
	}
	equal(t, u.Query().Get("state"), "w4b-state")
	equal(t, u.Query().Get("iss"), f.upstream)
	code := u.Query().Get("code")
	if code == "" {
		t.Fatal("mock consent returned no authorization code")
	}
	resp, err = client.PostForm(f.upstream+"/token", url.Values{"grant_type": {"authorization_code"}, "code": {code}, "client_id": {clientID}, "redirect_uri": q["redirect_uri"], "resource": q["resource"], "code_verifier": {verifier}})
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var tok struct {
		Access  string `json:"access_token"`
		Refresh string `json:"refresh_token"`
		Type    string `json:"token_type"`
	}
	if err = json.NewDecoder(resp.Body).Decode(&tok); err != nil || resp.StatusCode != 200 || tok.Access == "" || tok.Refresh == "" || !strings.EqualFold(tok.Type, "Bearer") {
		t.Fatal("standard MCP OAuth grant failed")
	}
	f.cp.set(key("org-a", "u1"), tokenRow{value: tok.Access})
	return tok.Access
}

type logBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *logBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}
func (b *logBuffer) text() string { b.mu.Lock(); defer b.mu.Unlock(); return b.buf.String() }
func captureLogs(t *testing.T) *logBuffer {
	t.Helper()
	b := &logBuffer{}
	previous := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(b, nil)))
	t.Cleanup(func() { slog.SetDefault(previous) })
	return b
}

func bearerHash(value string) string {
	sum := sha256.Sum256([]byte("Bearer " + value))
	return hex.EncodeToString(sum[:])
}
func (f *fixture) fingerprints() []string {
	var out []string
	for _, r := range f.mock.State().ReceivedLast(0, "tools/call") {
		out = append(out, r.Auth.SHA256)
	}
	return out
}

// Decode ONLY a synthetic binding with the documented W4 envelope, as Django
// does. This inspects its real sealed expiry without a ten-minute sleep or
// changing the unexported production clock.
func bindingEnvelope(t *testing.T, sealed string) (cipher.AEAD, []byte, []byte) {
	t.Helper()
	raw, err := base64.RawURLEncoding.DecodeString(sealed)
	if err != nil {
		t.Fatal(err)
	}
	k, err := hkdf.Key(sha256.New, []byte(channelSecret), []byte("agentcc-mcp-state"), "agentcc-mcp-state/v1/connect", 32)
	if err != nil {
		t.Fatal(err)
	}
	block, err := aes.NewCipher(k)
	if err != nil {
		t.Fatal(err)
	}
	a, err := cipher.NewGCM(block)
	if err != nil {
		t.Fatal(err)
	}
	plain, err := a.Open(nil, raw[1:13], raw[13:], append([]byte{1}, []byte("connect")...))
	if err != nil {
		t.Fatal(err)
	}
	return a, raw, plain
}
func assertBindingTTL(t *testing.T, sealed string, before time.Time) {
	t.Helper()
	a, raw, plain := bindingEnvelope(t, sealed)
	exp := time.UnixMilli(int64(binary.BigEndian.Uint64(plain[:8])))
	if exp.Before(before.Add(10*time.Minute-time.Second)) || exp.After(time.Now().Add(10*time.Minute+time.Second)) {
		t.Fatal("connect binding is not sealed for ten minutes")
	}
	// Make a separate authentic, already-expired test envelope. Fresh nonce
	// avoids nonce reuse; the existing codec must reject it as expired.
	expired := append([]byte(nil), plain...)
	binary.BigEndian.PutUint64(expired[:8], uint64(time.Now().Add(-time.Second).UnixMilli()))
	nonce := make([]byte, a.NonceSize())
	copy(nonce, raw[1:13])
	nonce[0] ^= 0x80
	old := append([]byte{1}, nonce...)
	old = append(old, a.Seal(nil, nonce, expired, append([]byte{1}, []byte("connect")...))...)
	codec, err := credentials.ConnectCodec(channelSecret)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = credentials.OpenConnectBinding(codec, base64.RawURLEncoding.EncodeToString(old)); err == nil {
		t.Fatal("expired binding opened")
	}
}
