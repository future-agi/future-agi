package cache

import (
	"bytes"
	"compress/gzip"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// AzBlobBackend stores cached responses in Azure Blob Storage.
type AzBlobBackend struct {
	client      *http.Client
	accountName string
	accountKey  []byte // decoded base64 key
	container   string
	prefix      string
	compress    bool
}

// NewAzBlobBackend creates an Azure Blob-backed cache.
// connectionString format: "DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=..."
func NewAzBlobBackend(connectionString, container, prefix string, compress bool, timeout time.Duration) (*AzBlobBackend, error) {
	if connectionString == "" {
		connectionString = os.Getenv("AZURE_STORAGE_CONNECTION_STRING")
	}
	if timeout <= 0 {
		timeout = 5 * time.Second
	}

	accountName, accountKey, err := parseAzureConnectionString(connectionString)
	if err != nil {
		return nil, fmt.Errorf("azure blob: %w", err)
	}

	keyBytes, err := base64.StdEncoding.DecodeString(accountKey)
	if err != nil {
		return nil, fmt.Errorf("azure blob: invalid account key: %w", err)
	}

	return &AzBlobBackend{
		client:      &http.Client{Timeout: timeout},
		accountName: accountName,
		accountKey:  keyBytes,
		container:   container,
		prefix:      prefix,
		compress:    compress,
	}, nil
}

func (a *AzBlobBackend) Get(key string) (*models.ChatCompletionResponse, bool) {
	blobName := a.prefix + key
	url := a.blobURL(blobName)

	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, false
	}
	a.signRequest(req)

	resp, err := a.client.Do(req)
	if err != nil {
		slog.Warn("azure blob cache get error", "key", key, "error", err)
		return nil, false
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, false
	}

	// Check TTL from metadata.
	if expiresStr := resp.Header.Get("X-Ms-Meta-Agentccexpiresat"); expiresStr != "" {
		expiresUnix, err := strconv.ParseInt(expiresStr, 10, 64)
		if err == nil && time.Now().Unix() > expiresUnix {
			go a.Delete(key)
			return nil, false
		}
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, false
	}

	if a.compress {
		gr, err := gzip.NewReader(bytes.NewReader(body))
		if err != nil {
			return nil, false
		}
		body, err = io.ReadAll(gr)
		gr.Close()
		if err != nil {
			return nil, false
		}
	}

	var result models.ChatCompletionResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, false
	}
	return &result, true
}

func (a *AzBlobBackend) Set(key string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	if ttl <= 0 || resp == nil {
		return
	}

	data, err := json.Marshal(resp)
	if err != nil {
		return
	}

	var body []byte
	if a.compress {
		var buf bytes.Buffer
		gw := gzip.NewWriter(&buf)
		gw.Write(data)
		gw.Close()
		body = buf.Bytes()
	} else {
		body = data
	}

	blobName := a.prefix + key
	url := a.blobURL(blobName)
	expiresAt := time.Now().Add(ttl).Unix()

	req, err := http.NewRequest("PUT", url, bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/octet-stream")
	req.Header.Set("Content-Length", strconv.Itoa(len(body)))
	req.Header.Set("X-Ms-Blob-Type", "BlockBlob")
	req.Header.Set("X-Ms-Meta-Agentccexpiresat", strconv.FormatInt(expiresAt, 10))
	a.signRequest(req)

	r, err := a.client.Do(req)
	if err != nil {
		slog.Warn("azure blob cache put error", "key", key, "error", err)
		return
	}
	r.Body.Close()
}

func (a *AzBlobBackend) Delete(key string) {
	blobName := a.prefix + key
	url := a.blobURL(blobName)

	req, err := http.NewRequest("DELETE", url, nil)
	if err != nil {
		return
	}
	a.signRequest(req)

	r, err := a.client.Do(req)
	if err != nil {
		return
	}
	r.Body.Close()
}

func (a *AzBlobBackend) blobURL(blobName string) string {
	return fmt.Sprintf("https://%s.blob.core.windows.net/%s/%s", a.accountName, a.container, blobName)
}

// signRequest adds Azure SharedKey authorization header.
func (a *AzBlobBackend) signRequest(req *http.Request) {
	now := time.Now().UTC().Format(http.TimeFormat)
	req.Header.Set("X-Ms-Date", now)
	req.Header.Set("X-Ms-Version", "2021-12-02")

	// Build string to sign for SharedKey.
	contentLength := req.Header.Get("Content-Length")
	if contentLength == "" || contentLength == "0" {
		contentLength = ""
	}

	stringToSign := strings.Join([]string{
		req.Method,
		req.Header.Get("Content-Encoding"),
		req.Header.Get("Content-Language"),
		contentLength,
		req.Header.Get("Content-MD5"),
		req.Header.Get("Content-Type"),
		"", // Date (empty, using x-ms-date)
		req.Header.Get("If-Modified-Since"),
		req.Header.Get("If-Match"),
		req.Header.Get("If-None-Match"),
		req.Header.Get("If-Unmodified-Since"),
		req.Header.Get("Range"),
		a.canonicalizedHeaders(req),
		a.canonicalizedResource(req),
	}, "\n")

	mac := hmac.New(sha256.New, a.accountKey)
	mac.Write([]byte(stringToSign))
	sig := base64.StdEncoding.EncodeToString(mac.Sum(nil))

	req.Header.Set("Authorization", "SharedKey "+a.accountName+":"+sig)
}

func (a *AzBlobBackend) canonicalizedHeaders(req *http.Request) string {
	var headers []string
	for k := range req.Header {
		lower := strings.ToLower(k)
		if strings.HasPrefix(lower, "x-ms-") {
			headers = append(headers, lower+":"+strings.TrimSpace(req.Header.Get(k)))
		}
	}
	if len(headers) == 0 {
		return ""
	}
	// Sort and join.
	sortStrings(headers)
	return strings.Join(headers, "\n")
}

func (a *AzBlobBackend) canonicalizedResource(req *http.Request) string {
	return "/" + a.accountName + req.URL.Path
}

func sortStrings(s []string) {
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j] < s[j-1]; j-- {
			s[j], s[j-1] = s[j-1], s[j]
		}
	}
}

// parseAzureConnectionString extracts AccountName and AccountKey.
func parseAzureConnectionString(cs string) (accountName, accountKey string, err error) {
	parts := strings.Split(cs, ";")
	for _, part := range parts {
		kv := strings.SplitN(part, "=", 2)
		if len(kv) != 2 {
			continue
		}
		switch strings.TrimSpace(kv[0]) {
		case "AccountName":
			accountName = strings.TrimSpace(kv[1])
		case "AccountKey":
			accountKey = strings.TrimSpace(kv[1])
		}
	}
	if accountName == "" || accountKey == "" {
		return "", "", fmt.Errorf("connection string must contain AccountName and AccountKey")
	}
	return accountName, accountKey, nil
}
