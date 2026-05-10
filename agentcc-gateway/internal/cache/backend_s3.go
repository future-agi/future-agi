package cache

import (
	"bytes"
	"compress/gzip"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// S3Backend stores cached responses in Amazon S3.
type S3Backend struct {
	client     *http.Client
	bucket     string
	prefix     string
	region     string
	accessKey  string
	secretKey  string
	compress   bool
}

// NewS3Backend creates an S3-backed cache.
func NewS3Backend(bucket, prefix, region, accessKey, secretKey string, compress bool, timeout time.Duration) *S3Backend {
	if region == "" {
		region = "us-east-1"
	}
	if timeout <= 0 {
		timeout = 5 * time.Second
	}
	// Resolve credentials from env if not provided.
	if accessKey == "" {
		accessKey = os.Getenv("AWS_ACCESS_KEY_ID")
	}
	if secretKey == "" {
		secretKey = os.Getenv("AWS_SECRET_ACCESS_KEY")
	}
	return &S3Backend{
		client:    &http.Client{Timeout: timeout},
		bucket:    bucket,
		prefix:    prefix,
		region:    region,
		accessKey: accessKey,
		secretKey: secretKey,
		compress:  compress,
	}
}

func (s *S3Backend) Get(key string) (*models.ChatCompletionResponse, bool) {
	objectKey := s.prefix + key
	url := s.objectURL(objectKey)

	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, false
	}
	s.signRequest(req, "GET", objectKey, nil)

	resp, err := s.client.Do(req)
	if err != nil {
		slog.Warn("s3 cache get error", "key", key, "error", err)
		return nil, false
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, false
	}

	// Check TTL from metadata.
	if expiresStr := resp.Header.Get("X-Amz-Meta-Agentcc-Expires-At"); expiresStr != "" {
		expiresUnix, err := strconv.ParseInt(expiresStr, 10, 64)
		if err == nil && time.Now().Unix() > expiresUnix {
			// Expired — best-effort delete.
			go s.Delete(key)
			return nil, false
		}
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, false
	}

	// Decompress if gzip.
	if s.compress || resp.Header.Get("Content-Encoding") == "gzip" {
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

func (s *S3Backend) Set(key string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	if ttl <= 0 || resp == nil {
		return
	}

	data, err := json.Marshal(resp)
	if err != nil {
		slog.Warn("s3 cache marshal error", "key", key, "error", err)
		return
	}

	var body []byte
	contentEncoding := ""
	if s.compress {
		var buf bytes.Buffer
		gw := gzip.NewWriter(&buf)
		gw.Write(data)
		gw.Close()
		body = buf.Bytes()
		contentEncoding = "gzip"
	} else {
		body = data
	}

	objectKey := s.prefix + key
	url := s.objectURL(objectKey)
	expiresAt := time.Now().Add(ttl).Unix()

	req, err := http.NewRequest("PUT", url, bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Content-Length", strconv.Itoa(len(body)))
	req.Header.Set("X-Amz-Meta-Agentcc-Expires-At", strconv.FormatInt(expiresAt, 10))
	if contentEncoding != "" {
		req.Header.Set("Content-Encoding", contentEncoding)
	}
	s.signRequest(req, "PUT", objectKey, body)

	r, err := s.client.Do(req)
	if err != nil {
		slog.Warn("s3 cache put error", "key", key, "error", err)
		return
	}
	r.Body.Close()
}

func (s *S3Backend) Delete(key string) {
	objectKey := s.prefix + key
	url := s.objectURL(objectKey)

	req, err := http.NewRequest("DELETE", url, nil)
	if err != nil {
		return
	}
	s.signRequest(req, "DELETE", objectKey, nil)

	r, err := s.client.Do(req)
	if err != nil {
		return
	}
	r.Body.Close()
}

// objectURL returns the S3 virtual-hosted-style URL for an object.
func (s *S3Backend) objectURL(objectKey string) string {
	return fmt.Sprintf("https://%s.s3.%s.amazonaws.com/%s", s.bucket, s.region, objectKey)
}

// signRequest signs an HTTP request with AWS Signature Version 4.
func (s *S3Backend) signRequest(req *http.Request, method, objectKey string, payload []byte) {
	if s.accessKey == "" || s.secretKey == "" {
		return // No credentials — rely on IAM role or instance profile via proxy
	}

	now := time.Now().UTC()
	datestamp := now.Format("20060102")
	amzDate := now.Format("20060102T150405Z")

	req.Header.Set("X-Amz-Date", amzDate)
	req.Header.Set("Host", req.URL.Host)

	// Payload hash.
	var payloadHash string
	if payload != nil {
		h := sha256.Sum256(payload)
		payloadHash = hex.EncodeToString(h[:])
	} else {
		h := sha256.Sum256([]byte(""))
		payloadHash = hex.EncodeToString(h[:])
	}
	req.Header.Set("X-Amz-Content-Sha256", payloadHash)

	// Canonical request.
	signedHeaders := s.getSignedHeaders(req)
	canonicalHeaders := s.getCanonicalHeaders(req, signedHeaders)
	canonicalRequest := strings.Join([]string{
		method,
		"/" + objectKey,
		"", // query string
		canonicalHeaders,
		strings.Join(signedHeaders, ";"),
		payloadHash,
	}, "\n")

	// String to sign.
	scope := datestamp + "/" + s.region + "/s3/aws4_request"
	crHash := sha256.Sum256([]byte(canonicalRequest))
	stringToSign := "AWS4-HMAC-SHA256\n" + amzDate + "\n" + scope + "\n" + hex.EncodeToString(crHash[:])

	// Signing key.
	signingKey := s.deriveSigningKey(datestamp)

	// Signature.
	sig := hmacSHA256(signingKey, []byte(stringToSign))

	// Authorization header.
	authHeader := fmt.Sprintf("AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s",
		s.accessKey, scope, strings.Join(signedHeaders, ";"), hex.EncodeToString(sig))
	req.Header.Set("Authorization", authHeader)
}

func (s *S3Backend) deriveSigningKey(datestamp string) []byte {
	kDate := hmacSHA256([]byte("AWS4"+s.secretKey), []byte(datestamp))
	kRegion := hmacSHA256(kDate, []byte(s.region))
	kService := hmacSHA256(kRegion, []byte("s3"))
	return hmacSHA256(kService, []byte("aws4_request"))
}

func (s *S3Backend) getSignedHeaders(req *http.Request) []string {
	headers := []string{"host"}
	for k := range req.Header {
		lower := strings.ToLower(k)
		if lower == "x-amz-date" || lower == "x-amz-content-sha256" || strings.HasPrefix(lower, "x-amz-meta-") || lower == "content-type" || lower == "content-encoding" {
			headers = append(headers, lower)
		}
	}
	sort.Strings(headers)
	return headers
}

func (s *S3Backend) getCanonicalHeaders(req *http.Request, signedHeaders []string) string {
	var sb strings.Builder
	for _, h := range signedHeaders {
		if h == "host" {
			sb.WriteString("host:" + req.URL.Host + "\n")
		} else {
			canonicalKey := http.CanonicalHeaderKey(h)
			vals := req.Header[canonicalKey]
			if len(vals) == 0 {
				// Try exact match for lowercase headers.
				for k, v := range req.Header {
					if strings.ToLower(k) == h {
						vals = v
						break
					}
				}
			}
			sb.WriteString(h + ":" + strings.Join(vals, ",") + "\n")
		}
	}
	return sb.String()
}

func hmacSHA256(key, data []byte) []byte {
	h := hmac.New(sha256.New, key)
	h.Write(data)
	return h.Sum(nil)
}
