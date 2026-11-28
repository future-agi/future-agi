package gauth_test

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/providers/gauth"
)

func TestTokenProviderJSON(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	data, err := json.Marshal(map[string]string{
		"client_email": "test@example.iam.gserviceaccount.com",
		"private_key":  string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: encoded})),
		"token_uri":    "https://oauth2.googleapis.com/token",
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := gauth.NewTokenProviderJSON(data, gauth.ScopeCloudPlatform); err != nil {
		t.Fatalf("valid inline service account rejected: %v", err)
	}
	if _, err := gauth.NewTokenProviderJSON([]byte(`{"private_key":"bad"}`), gauth.ScopeCloudPlatform); err == nil {
		t.Fatal("incomplete service account accepted")
	}
}

const testClientEmail = "test@example.iam.gserviceaccount.com"

// newServiceAccountFile writes a service-account file whose RSA key is generated for this test
// only, and returns its path and public key. No real credential is involved.
func newServiceAccountFile(t *testing.T) (string, *rsa.PublicKey) {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	data, err := json.Marshal(map[string]string{
		"client_email": testClientEmail,
		"private_key":  string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: encoded})),
		"token_uri":    "https://oauth2.googleapis.com/token",
	})
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "service-account.json")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return path, &key.PublicKey
}

// fakeTokenEndpoint stands in for Google's token endpoint. It checks the signed assertion the
// provider sends and answers with a numbered token.
type fakeTokenEndpoint struct {
	*httptest.Server
	hits atomic.Int64
}

func newFakeTokenEndpoint(t *testing.T, pub *rsa.PublicKey) *fakeTokenEndpoint {
	t.Helper()
	f := &fakeTokenEndpoint{}
	f.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := checkAssertion(r, pub); err != nil {
			t.Errorf("token request: %v", err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		n := f.hits.Add(1)
		fmt.Fprintf(w, `{"access_token":"ya29.fake-%d","expires_in":3600}`, n)
	}))
	t.Cleanup(f.Close)
	return f
}

func checkAssertion(r *http.Request, pub *rsa.PublicKey) error {
	if r.Method != http.MethodPost || r.URL.Path != "/token" {
		return fmt.Errorf("unexpected %s %s", r.Method, r.URL.Path)
	}
	if err := r.ParseForm(); err != nil {
		return err
	}
	if got := r.PostForm.Get("grant_type"); got != "urn:ietf:params:oauth:grant-type:jwt-bearer" {
		return fmt.Errorf("grant_type %q", got)
	}
	parts := strings.Split(r.PostForm.Get("assertion"), ".")
	if len(parts) != 3 {
		return fmt.Errorf("assertion has %d parts", len(parts))
	}
	sig, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		return err
	}
	hash := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	if err := rsa.VerifyPKCS1v15(pub, crypto.SHA256, hash[:], sig); err != nil {
		return fmt.Errorf("assertion signature: %w", err)
	}
	raw, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return err
	}
	var claims struct {
		Iss   string `json:"iss"`
		Scope string `json:"scope"`
		Aud   string `json:"aud"`
		Exp   int64  `json:"exp"`
		Iat   int64  `json:"iat"`
	}
	if err := json.Unmarshal(raw, &claims); err != nil {
		return err
	}
	if claims.Iss != testClientEmail || claims.Scope != gauth.ScopeCloudPlatform ||
		claims.Aud != "https://oauth2.googleapis.com/token" || claims.Exp <= claims.Iat {
		return fmt.Errorf("unexpected claims %+v", claims)
	}
	return nil
}

// toServer sends the provider's requests for Google's token host to a local server instead.
type toServer struct{ target *url.URL }

func (rt toServer) RoundTrip(r *http.Request) (*http.Response, error) {
	if r.URL.Host != "oauth2.googleapis.com" {
		return nil, fmt.Errorf("unexpected token host %q", r.URL.Host)
	}
	out := r.Clone(r.Context())
	out.URL.Scheme, out.URL.Host, out.Host = rt.target.Scheme, rt.target.Host, rt.target.Host
	return http.DefaultTransport.RoundTrip(out)
}

func newLocalProvider(t *testing.T) (*gauth.TokenProvider, *fakeTokenEndpoint) {
	t.Helper()
	path, pub := newServiceAccountFile(t)
	endpoint := newFakeTokenEndpoint(t, pub)
	target, err := url.Parse(endpoint.URL)
	if err != nil {
		t.Fatal(err)
	}
	gauth.UseTokenTransport(t, toServer{target: target})
	tp, err := gauth.NewTokenProvider(path, gauth.ScopeCloudPlatform)
	if err != nil {
		t.Fatalf("NewTokenProvider: %v", err)
	}
	return tp, endpoint
}

func TestTokenProvider_LoadAndToken(t *testing.T) {
	tp, endpoint := newLocalProvider(t)

	if tok := tp.Token(); tok != "ya29.fake-1" {
		t.Fatalf("Token() = %q, want the token the endpoint issued", tok)
	}
	if got := endpoint.hits.Load(); got != 1 {
		t.Fatalf("token endpoint called %d times, want 1", got)
	}
}

func TestTokenProvider_Cached(t *testing.T) {
	tp, endpoint := newLocalProvider(t)

	tok1 := tp.Token()
	tok2 := tp.Token()
	if tok1 == "" || tok1 != tok2 {
		t.Fatalf("consecutive Token() calls returned %q and %q, want the same cached token", tok1, tok2)
	}
	if got := endpoint.hits.Load(); got != 1 {
		t.Fatalf("token endpoint called %d times, want 1 (second call should be cached)", got)
	}
}

func TestTokenProvider_VertexAICall(t *testing.T) {
	tp, _ := newLocalProvider(t)
	tok := tp.Token()
	if tok == "" {
		t.Fatal("no token")
	}

	const project, location, model = "test-project", "us-central1", "gemini-2.5-flash"
	wantPath := "/v1/projects/" + project + "/locations/" + location +
		"/publishers/google/models/" + model + ":generateContent"
	vertex := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		switch {
		case r.Method != http.MethodPost || r.URL.Path != wantPath:
			t.Errorf("unexpected %s %s", r.Method, r.URL.Path)
		case r.Header.Get("Authorization") != "Bearer "+tok:
			t.Errorf("Authorization header does not carry the provider's token")
		case r.Header.Get("Content-Type") != "application/json":
			t.Errorf("Content-Type %q", r.Header.Get("Content-Type"))
		case !strings.Contains(string(body), `"contents"`):
			t.Errorf("request body has no contents: %s", body)
		default:
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"candidates":[{"content":{"parts":[{"text":"pong"}]}}]}`)
			return
		}
		w.WriteHeader(http.StatusBadRequest)
	}))
	defer vertex.Close()

	body := `{"contents":[{"role":"user","parts":[{"text":"Reply with exactly: pong"}]}]}`
	req, err := http.NewRequest(http.MethodPost, vertex.URL+wantPath, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("Content-Type", "application/json")

	resp, err := vertex.Client().Do(req)
	if err != nil {
		t.Fatalf("HTTP request failed: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("Vertex AI endpoint returned HTTP %d (expected 200)", resp.StatusCode)
	}
}
