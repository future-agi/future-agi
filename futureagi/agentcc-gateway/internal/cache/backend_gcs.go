package cache

import (
	"bytes"
	"compress/gzip"
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
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// GCSBackend stores cached responses in Google Cloud Storage.
type GCSBackend struct {
	client   *http.Client
	bucket   string
	prefix   string
	compress bool

	// Auth
	mu          sync.Mutex
	accessToken string
	tokenExpiry time.Time
	clientEmail string
	privateKey  *rsa.PrivateKey
	tokenURL    string
}

// gcsServiceAccount represents the JSON key file structure.
type gcsServiceAccount struct {
	ClientEmail string `json:"client_email"`
	PrivateKey  string `json:"private_key"`
	TokenURI    string `json:"token_uri"`
}

// NewGCSBackend creates a GCS-backed cache.
func NewGCSBackend(bucket, prefix, credentialsFile string, compress bool, timeout time.Duration) (*GCSBackend, error) {
	if timeout <= 0 {
		timeout = 5 * time.Second
	}
	if credentialsFile == "" {
		credentialsFile = os.Getenv("GOOGLE_APPLICATION_CREDENTIALS")
	}

	g := &GCSBackend{
		client:   &http.Client{Timeout: timeout},
		bucket:   bucket,
		prefix:   prefix,
		compress: compress,
		tokenURL: "https://oauth2.googleapis.com/token",
	}

	// Load service account key if provided.
	if credentialsFile != "" {
		data, err := os.ReadFile(credentialsFile)
		if err != nil {
			return nil, fmt.Errorf("gcs: read credentials: %w", err)
		}
		var sa gcsServiceAccount
		if err := json.Unmarshal(data, &sa); err != nil {
			return nil, fmt.Errorf("gcs: parse credentials: %w", err)
		}
		g.clientEmail = sa.ClientEmail
		if sa.TokenURI != "" {
			g.tokenURL = sa.TokenURI
		}

		block, _ := pem.Decode([]byte(sa.PrivateKey))
		if block == nil {
			return nil, fmt.Errorf("gcs: failed to decode PEM private key")
		}
		key, err := x509.ParsePKCS8PrivateKey(block.Bytes)
		if err != nil {
			return nil, fmt.Errorf("gcs: parse private key: %w", err)
		}
		rsaKey, ok := key.(*rsa.PrivateKey)
		if !ok {
			return nil, fmt.Errorf("gcs: private key is not RSA")
		}
		g.privateKey = rsaKey
	}

	return g, nil
}

func (g *GCSBackend) Get(key string) (*models.ChatCompletionResponse, bool) {
	objectName := g.prefix + key
	url := fmt.Sprintf("https://storage.googleapis.com/storage/v1/b/%s/o/%s?alt=media",
		g.bucket, url.PathEscape(objectName))

	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, false
	}
	g.authorize(req)

	resp, err := g.client.Do(req)
	if err != nil {
		slog.Warn("gcs cache get error", "key", key, "error", err)
		return nil, false
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, false
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, false
	}

	// For GCS, metadata is fetched via a separate call or embedded in the object.
	// We embed expiresAt as the first 8 bytes of the object.
	if len(body) < 8 {
		return nil, false
	}
	expiresAt := int64(body[0])<<56 | int64(body[1])<<48 | int64(body[2])<<40 | int64(body[3])<<32 |
		int64(body[4])<<24 | int64(body[5])<<16 | int64(body[6])<<8 | int64(body[7])
	if time.Now().Unix() > expiresAt {
		go g.Delete(key)
		return nil, false
	}
	payload := body[8:]

	if g.compress {
		gr, err := gzip.NewReader(bytes.NewReader(payload))
		if err != nil {
			return nil, false
		}
		payload, err = io.ReadAll(gr)
		gr.Close()
		if err != nil {
			return nil, false
		}
	}

	var result models.ChatCompletionResponse
	if err := json.Unmarshal(payload, &result); err != nil {
		return nil, false
	}
	return &result, true
}

func (g *GCSBackend) Set(key string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	if ttl <= 0 || resp == nil {
		return
	}

	data, err := json.Marshal(resp)
	if err != nil {
		return
	}

	var payload []byte
	if g.compress {
		var buf bytes.Buffer
		gw := gzip.NewWriter(&buf)
		gw.Write(data)
		gw.Close()
		payload = buf.Bytes()
	} else {
		payload = data
	}

	expiresAt := time.Now().Add(ttl).Unix()

	// Prepend 8-byte expiresAt header.
	header := make([]byte, 8)
	header[0] = byte(expiresAt >> 56)
	header[1] = byte(expiresAt >> 48)
	header[2] = byte(expiresAt >> 40)
	header[3] = byte(expiresAt >> 32)
	header[4] = byte(expiresAt >> 24)
	header[5] = byte(expiresAt >> 16)
	header[6] = byte(expiresAt >> 8)
	header[7] = byte(expiresAt)

	body := append(header, payload...)

	objectName := g.prefix + key
	uploadURL := fmt.Sprintf("https://storage.googleapis.com/upload/storage/v1/b/%s/o?uploadType=media&name=%s",
		g.bucket, url.PathEscape(objectName))

	req, err := http.NewRequest("POST", uploadURL, bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/octet-stream")
	req.Header.Set("Content-Length", strconv.Itoa(len(body)))
	g.authorize(req)

	r, err := g.client.Do(req)
	if err != nil {
		slog.Warn("gcs cache put error", "key", key, "error", err)
		return
	}
	r.Body.Close()
}

func (g *GCSBackend) Delete(key string) {
	objectName := g.prefix + key
	deleteURL := fmt.Sprintf("https://storage.googleapis.com/storage/v1/b/%s/o/%s",
		g.bucket, url.PathEscape(objectName))

	req, err := http.NewRequest("DELETE", deleteURL, nil)
	if err != nil {
		return
	}
	g.authorize(req)

	r, err := g.client.Do(req)
	if err != nil {
		return
	}
	r.Body.Close()
}

// authorize adds a Bearer token to the request, refreshing if needed.
func (g *GCSBackend) authorize(req *http.Request) {
	token := g.getToken()
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
}

func (g *GCSBackend) getToken() string {
	if g.privateKey == nil {
		return ""
	}

	g.mu.Lock()
	defer g.mu.Unlock()

	if g.accessToken != "" && time.Now().Before(g.tokenExpiry) {
		return g.accessToken
	}

	// Create JWT.
	now := time.Now()
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"RS256","typ":"JWT"}`))
	claims := fmt.Sprintf(`{"iss":"%s","scope":"https://www.googleapis.com/auth/devstorage.read_write","aud":"%s","exp":%d,"iat":%d}`,
		g.clientEmail, g.tokenURL, now.Add(time.Hour).Unix(), now.Unix())
	claimsEnc := base64.RawURLEncoding.EncodeToString([]byte(claims))
	sigInput := header + "." + claimsEnc

	hash := sha256.Sum256([]byte(sigInput))
	sig, err := rsa.SignPKCS1v15(rand.Reader, g.privateKey, crypto.SHA256, hash[:])
	if err != nil {
		slog.Warn("gcs jwt sign error", "error", err)
		return ""
	}
	jwt := sigInput + "." + base64.RawURLEncoding.EncodeToString(sig)

	// Exchange JWT for access token.
	form := "grant_type=" + url.QueryEscape("urn:ietf:params:oauth:grant-type:jwt-bearer") + "&assertion=" + jwt
	resp, err := http.Post(g.tokenURL, "application/x-www-form-urlencoded", strings.NewReader(form))
	if err != nil {
		slog.Warn("gcs token exchange error", "error", err)
		return ""
	}
	defer resp.Body.Close()

	var tokenResp struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int    `json:"expires_in"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&tokenResp); err != nil {
		return ""
	}

	g.accessToken = tokenResp.AccessToken
	g.tokenExpiry = now.Add(time.Duration(tokenResp.ExpiresIn-60) * time.Second) // refresh 60s early
	return g.accessToken
}
