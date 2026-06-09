package bedrock

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Credentials holds AWS credentials.
type Credentials struct {
	AccessKeyID    string
	SecretAccessKey string
	SessionToken   string
}

// loadCredentials loads AWS credentials from provider config.
func loadCredentials(cfg config.ProviderConfig) (*Credentials, error) {
	accessKey := cfg.AWSAccessKeyID
	secretKey := cfg.AWSSecretAccessKey

	if accessKey == "" || secretKey == "" {
		return nil, fmt.Errorf("aws_access_key_id and aws_secret_access_key must be set in provider config")
	}

	return &Credentials{
		AccessKeyID:    accessKey,
		SecretAccessKey: secretKey,
		SessionToken:   cfg.AWSSessionToken,
	}, nil
}

// signRequest signs an HTTP request with AWS Signature Version 4.
func signRequest(req *http.Request, creds *Credentials, region, service string) error {
	now := time.Now().UTC()
	dateStamp := now.Format("20060102")
	amzDate := now.Format("20060102T150405Z")

	// Read and hash the body.
	var bodyHash string
	if req.Body != nil {
		bodyBytes, err := io.ReadAll(req.Body)
		if err != nil {
			return fmt.Errorf("reading request body: %w", err)
		}
		bodyHash = hashSHA256(bodyBytes)
		req.Body = io.NopCloser(strings.NewReader(string(bodyBytes)))
		req.ContentLength = int64(len(bodyBytes))
	} else {
		bodyHash = hashSHA256(nil)
	}

	// Set required headers.
	req.Header.Set("x-amz-date", amzDate)
	req.Header.Set("x-amz-content-sha256", bodyHash)
	if creds.SessionToken != "" {
		req.Header.Set("x-amz-security-token", creds.SessionToken)
	}

	// Build canonical request.
	// Use EscapedPath() to preserve %2F in path segments (e.g. ARN model IDs),
	// then re-encode per AWS SigV4 rules (which require %3A for colons).
	canonicalURI := awsURIEncodePath(req.URL.EscapedPath())
	canonicalQueryString := req.URL.Query().Encode()

	// Canonical headers (sorted, lowercase).
	signedHeaderKeys := []string{"content-type", "host", "x-amz-content-sha256", "x-amz-date"}
	if creds.SessionToken != "" {
		signedHeaderKeys = append(signedHeaderKeys, "x-amz-security-token")
	}
	sort.Strings(signedHeaderKeys)

	var canonicalHeaders strings.Builder
	for _, key := range signedHeaderKeys {
		var val string
		if key == "host" {
			val = req.Host
			if val == "" {
				val = req.URL.Host
			}
		} else {
			val = req.Header.Get(key)
		}
		canonicalHeaders.WriteString(key)
		canonicalHeaders.WriteString(":")
		canonicalHeaders.WriteString(strings.TrimSpace(val))
		canonicalHeaders.WriteString("\n")
	}

	signedHeaders := strings.Join(signedHeaderKeys, ";")

	canonicalRequest := strings.Join([]string{
		req.Method,
		canonicalURI,
		canonicalQueryString,
		canonicalHeaders.String(),
		signedHeaders,
		bodyHash,
	}, "\n")

	// Create string to sign.
	credentialScope := fmt.Sprintf("%s/%s/%s/aws4_request", dateStamp, region, service)
	stringToSign := strings.Join([]string{
		"AWS4-HMAC-SHA256",
		amzDate,
		credentialScope,
		hashSHA256([]byte(canonicalRequest)),
	}, "\n")

	// Derive signing key.
	signingKey := deriveSigningKey(creds.SecretAccessKey, dateStamp, region, service)

	// Calculate signature.
	signature := hex.EncodeToString(hmacSHA256(signingKey, []byte(stringToSign)))

	// Set Authorization header.
	authHeader := fmt.Sprintf(
		"AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s",
		creds.AccessKeyID, credentialScope, signedHeaders, signature,
	)
	req.Header.Set("Authorization", authHeader)

	return nil
}

func deriveSigningKey(secret, dateStamp, region, service string) []byte {
	kDate := hmacSHA256([]byte("AWS4"+secret), []byte(dateStamp))
	kRegion := hmacSHA256(kDate, []byte(region))
	kService := hmacSHA256(kRegion, []byte(service))
	kSigning := hmacSHA256(kService, []byte("aws4_request"))
	return kSigning
}

func hmacSHA256(key, data []byte) []byte {
	h := hmac.New(sha256.New, key)
	h.Write(data)
	return h.Sum(nil)
}

func hashSHA256(data []byte) string {
	h := sha256.New()
	if data != nil {
		h.Write(data)
	}
	return hex.EncodeToString(h.Sum(nil))
}

// awsURIEncodePath percent-encodes each path segment per AWS SigV4 rules.
// The input should be the raw/escaped path (from URL.EscapedPath()) so that
// percent-encoded characters like %2F are treated as literal bytes and get
// double-encoded (e.g. %2F → %252F) as AWS SigV4 requires for non-S3 services.
// Go's url.PathEscape doesn't encode colons (valid per RFC 3986), but AWS requires %3A.
func awsURIEncodePath(path string) string {
	if path == "" {
		return "/"
	}
	segments := strings.Split(path, "/")
	for i, seg := range segments {
		segments[i] = strings.ReplaceAll(url.PathEscape(seg), ":", "%3A")
	}
	return strings.Join(segments, "/")
}

// extractRegion extracts the AWS region from config, headers, or URL.
func extractRegion(baseURL string, headers map[string]string, configRegion string) string {
	// Check explicit config region first.
	if configRegion != "" {
		return configRegion
	}

	// Check headers.
	if region, ok := headers["x-aws-region"]; ok {
		return region
	}

	// Try to extract from URL: bedrock-runtime.us-east-1.amazonaws.com
	parts := strings.Split(baseURL, ".")
	for i, part := range parts {
		if part == "bedrock-runtime" && i+1 < len(parts) {
			return parts[i+1]
		}
	}

	// Also try after :// removal.
	clean := baseURL
	if idx := strings.Index(clean, "://"); idx >= 0 {
		clean = clean[idx+3:]
	}
	parts = strings.Split(clean, ".")
	for i, part := range parts {
		if part == "bedrock-runtime" && i+1 < len(parts) {
			return parts[i+1]
		}
	}

	// Default.
	return "us-east-1"
}
