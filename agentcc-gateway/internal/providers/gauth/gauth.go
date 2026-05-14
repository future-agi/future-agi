package gauth

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
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"
)

const (
	ScopeCloudPlatform = "https://www.googleapis.com/auth/cloud-platform"
	defaultTokenURL    = "https://oauth2.googleapis.com/token"
)

type serviceAccountKey struct {
	ClientEmail string `json:"client_email"`
	PrivateKey  string `json:"private_key"`
	TokenURI    string `json:"token_uri"`
}

type TokenProvider struct {
	mu          sync.Mutex
	clientEmail string
	privateKey  *rsa.PrivateKey
	tokenURL    string
	scope       string
	accessToken string
	tokenExpiry time.Time
}

func NewTokenProvider(credentialsFile, scope string) (*TokenProvider, error) {
	if credentialsFile == "" {
		credentialsFile = os.Getenv("GOOGLE_APPLICATION_CREDENTIALS")
	}
	if credentialsFile == "" {
		return nil, fmt.Errorf("gauth: no credentials file provided and GOOGLE_APPLICATION_CREDENTIALS not set")
	}

	data, err := os.ReadFile(credentialsFile)
	if err != nil {
		return nil, fmt.Errorf("gauth: read credentials file: %w", err)
	}

	var sa serviceAccountKey
	if err := json.Unmarshal(data, &sa); err != nil {
		return nil, fmt.Errorf("gauth: parse credentials file: %w", err)
	}

	block, _ := pem.Decode([]byte(sa.PrivateKey))
	if block == nil {
		return nil, fmt.Errorf("gauth: failed to decode PEM private key")
	}
	key, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("gauth: parse private key: %w", err)
	}
	rsaKey, ok := key.(*rsa.PrivateKey)
	if !ok {
		return nil, fmt.Errorf("gauth: private key is not RSA")
	}

	tokenURL := defaultTokenURL
	if sa.TokenURI != "" {
		tokenURL = sa.TokenURI
	}

	return &TokenProvider{
		clientEmail: sa.ClientEmail,
		privateKey:  rsaKey,
		tokenURL:    tokenURL,
		scope:       scope,
	}, nil
}

func (t *TokenProvider) Token() string {
	t.mu.Lock()
	defer t.mu.Unlock()

	if t.accessToken != "" && time.Now().Before(t.tokenExpiry) {
		return t.accessToken
	}

	now := time.Now()
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"RS256","typ":"JWT"}`))
	claims := fmt.Sprintf(
		`{"iss":%q,"scope":%q,"aud":%q,"exp":%d,"iat":%d}`,
		t.clientEmail, t.scope, t.tokenURL,
		now.Add(time.Hour).Unix(), now.Unix(),
	)
	claimsEnc := base64.RawURLEncoding.EncodeToString([]byte(claims))
	sigInput := header + "." + claimsEnc

	hash := sha256.Sum256([]byte(sigInput))
	sig, err := rsa.SignPKCS1v15(rand.Reader, t.privateKey, crypto.SHA256, hash[:])
	if err != nil {
		slog.Warn("gauth: JWT sign error", "error", err)
		return ""
	}
	jwt := sigInput + "." + base64.RawURLEncoding.EncodeToString(sig)

	form := "grant_type=" + url.QueryEscape("urn:ietf:params:oauth:grant-type:jwt-bearer") + "&assertion=" + jwt
	resp, err := http.Post(t.tokenURL, "application/x-www-form-urlencoded", strings.NewReader(form)) //nolint:noctx
	if err != nil {
		slog.Warn("gauth: token exchange error", "error", err)
		return ""
	}
	defer resp.Body.Close()

	var tokenResp struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int    `json:"expires_in"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&tokenResp); err != nil {
		slog.Warn("gauth: token decode error", "error", err)
		return ""
	}

	t.accessToken = tokenResp.AccessToken
	t.tokenExpiry = now.Add(time.Duration(tokenResp.ExpiresIn-60) * time.Second)
	return t.accessToken
}
