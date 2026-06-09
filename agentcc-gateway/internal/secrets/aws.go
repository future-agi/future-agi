package secrets

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

// AWSConfig configures the AWS Secrets Manager backend.
type AWSConfig struct {
	Region          string `yaml:"region" json:"region"`
	AccessKeyID     string `yaml:"access_key_id" json:"-"`
	SecretAccessKey string `yaml:"secret_access_key" json:"-"`
}

// AWSBackend resolves secrets from AWS Secrets Manager.
type AWSBackend struct {
	region          string
	accessKeyID     string
	secretAccessKey string
	client          *http.Client
}

// NewAWSBackend creates an AWS Secrets Manager backend.
func NewAWSBackend(cfg AWSConfig) (*AWSBackend, error) {
	region := cfg.Region
	if region == "" {
		region = os.Getenv("AWS_REGION")
	}
	if region == "" {
		region = os.Getenv("AWS_DEFAULT_REGION")
	}
	if region == "" {
		region = "us-east-1"
	}

	accessKey := cfg.AccessKeyID
	if accessKey == "" {
		accessKey = os.Getenv("AWS_ACCESS_KEY_ID")
	}
	secretKey := cfg.SecretAccessKey
	if secretKey == "" {
		secretKey = os.Getenv("AWS_SECRET_ACCESS_KEY")
	}

	if accessKey == "" || secretKey == "" {
		return nil, fmt.Errorf("aws: credentials required (config or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY)")
	}

	return &AWSBackend{
		region:          region,
		accessKeyID:     accessKey,
		secretAccessKey: secretKey,
		client:          &http.Client{Timeout: 10 * time.Second},
	}, nil
}

// Resolve fetches a secret from AWS Secrets Manager.
// URI format: aws-sm://secret-name[#json-field]
func (a *AWSBackend) Resolve(uri string) (string, error) {
	_, path, field, err := ParseURI(uri)
	if err != nil {
		return "", err
	}

	endpoint := fmt.Sprintf("https://secretsmanager.%s.amazonaws.com", a.region)
	payload := fmt.Sprintf(`{"SecretId":"%s"}`, path)

	now := time.Now().UTC()
	req, err := http.NewRequest("POST", endpoint, strings.NewReader(payload))
	if err != nil {
		return "", fmt.Errorf("aws: create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/x-amz-json-1.1")
	req.Header.Set("X-Amz-Target", "secretsmanager.GetSecretValue")
	req.Header.Set("X-Amz-Date", now.Format("20060102T150405Z"))
	req.Header.Set("Host", req.URL.Host)

	a.signRequest(req, []byte(payload), now)

	resp, err := a.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("aws: request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
		return "", fmt.Errorf("aws: HTTP %d: %s", resp.StatusCode, string(body))
	}

	var result struct {
		SecretString string `json:"SecretString"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("aws: decode response: %w", err)
	}

	if field == "" {
		return result.SecretString, nil
	}

	// Try to extract a JSON field.
	var data map[string]interface{}
	if err := json.Unmarshal([]byte(result.SecretString), &data); err != nil {
		return "", fmt.Errorf("aws: secret is not JSON, cannot extract field %q", field)
	}

	val, ok := data[field]
	if !ok {
		return "", fmt.Errorf("aws: field %q not found in secret", field)
	}

	return fmt.Sprintf("%v", val), nil
}

// signRequest adds AWS SigV4 Authorization header.
func (a *AWSBackend) signRequest(req *http.Request, payload []byte, now time.Time) {
	dateStamp := now.Format("20060102")
	amzDate := now.Format("20060102T150405Z")
	service := "secretsmanager"
	credentialScope := fmt.Sprintf("%s/%s/%s/aws4_request", dateStamp, a.region, service)

	// Canonical request.
	payloadHash := sha256Hex(payload)
	canonicalHeaders := fmt.Sprintf("content-type:%s\nhost:%s\nx-amz-date:%s\nx-amz-target:%s\n",
		req.Header.Get("Content-Type"),
		req.URL.Host,
		amzDate,
		req.Header.Get("X-Amz-Target"),
	)
	signedHeaders := "content-type;host;x-amz-date;x-amz-target"
	canonicalRequest := fmt.Sprintf("POST\n/\n\n%s\n%s\n%s",
		canonicalHeaders, signedHeaders, payloadHash)

	// String to sign.
	stringToSign := fmt.Sprintf("AWS4-HMAC-SHA256\n%s\n%s\n%s",
		amzDate, credentialScope, sha256Hex([]byte(canonicalRequest)))

	// Signing key.
	signingKey := getSignatureKey(a.secretAccessKey, dateStamp, a.region, service)
	signature := hex.EncodeToString(hmacSHA256(signingKey, []byte(stringToSign)))

	req.Header.Set("Authorization", fmt.Sprintf(
		"AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s",
		a.accessKeyID, credentialScope, signedHeaders, signature,
	))
}

func sha256Hex(data []byte) string {
	h := sha256.Sum256(data)
	return hex.EncodeToString(h[:])
}

func hmacSHA256(key, data []byte) []byte {
	h := hmac.New(sha256.New, key)
	h.Write(data)
	return h.Sum(nil)
}

func getSignatureKey(secret, dateStamp, region, service string) []byte {
	kDate := hmacSHA256([]byte("AWS4"+secret), []byte(dateStamp))
	kRegion := hmacSHA256(kDate, []byte(region))
	kService := hmacSHA256(kRegion, []byte(service))
	kSigning := hmacSHA256(kService, []byte("aws4_request"))
	return kSigning
}
