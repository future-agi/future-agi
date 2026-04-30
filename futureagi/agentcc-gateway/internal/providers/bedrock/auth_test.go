package bedrock

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// ===========================================================================
// extractRegion tests
// ===========================================================================

func TestExtractRegion_FromURL(t *testing.T) {
	tests := []struct {
		name    string
		baseURL string
		want    string
	}{
		{
			name:    "standard bedrock runtime URL",
			baseURL: "https://bedrock-runtime.us-east-1.amazonaws.com",
			want:    "us-east-1",
		},
		{
			name:    "eu-west-1 region",
			baseURL: "https://bedrock-runtime.eu-west-1.amazonaws.com",
			want:    "eu-west-1",
		},
		{
			name:    "ap-southeast-2 region",
			baseURL: "https://bedrock-runtime.ap-southeast-2.amazonaws.com",
			want:    "ap-southeast-2",
		},
		{
			name:    "us-west-2 without scheme",
			baseURL: "bedrock-runtime.us-west-2.amazonaws.com",
			want:    "us-west-2",
		},
		{
			name:    "URL with trailing path",
			baseURL: "https://bedrock-runtime.us-east-2.amazonaws.com/some/path",
			want:    "us-east-2",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := extractRegion(tt.baseURL, nil, "")
			if got != tt.want {
				t.Errorf("extractRegion(%q, nil) = %q, want %q", tt.baseURL, got, tt.want)
			}
		})
	}
}

func TestExtractRegion_FromHeaders(t *testing.T) {
	headers := map[string]string{
		"x-aws-region": "eu-central-1",
	}
	got := extractRegion("https://bedrock-runtime.us-east-1.amazonaws.com", headers, "")
	if got != "eu-central-1" {
		t.Errorf("extractRegion with x-aws-region header = %q, want %q", got, "eu-central-1")
	}
}

func TestExtractRegion_HeaderTakesPrecedenceOverURL(t *testing.T) {
	headers := map[string]string{
		"x-aws-region": "ap-northeast-1",
	}
	got := extractRegion("https://bedrock-runtime.us-west-2.amazonaws.com", headers, "")
	if got != "ap-northeast-1" {
		t.Errorf("expected header region to take precedence, got %q", got)
	}
}

func TestExtractRegion_DefaultFallback(t *testing.T) {
	tests := []struct {
		name    string
		baseURL string
		headers map[string]string
	}{
		{
			name:    "non-bedrock URL",
			baseURL: "https://example.com/api",
			headers: nil,
		},
		{
			name:    "empty URL",
			baseURL: "",
			headers: nil,
		},
		{
			name:    "empty headers no match",
			baseURL: "https://api.example.com",
			headers: map[string]string{"x-custom": "value"},
		},
		{
			name:    "partial match no runtime segment",
			baseURL: "https://bedrock.us-east-1.amazonaws.com",
			headers: nil,
		},
		{
			name:    "nil headers nil URL",
			baseURL: "",
			headers: nil,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := extractRegion(tt.baseURL, tt.headers, "")
			if got != "us-east-1" {
				t.Errorf("extractRegion(%q, %v) = %q, want default %q", tt.baseURL, tt.headers, got, "us-east-1")
			}
		})
	}
}

// ===========================================================================
// loadCredentials tests
// ===========================================================================

func TestLoadCredentials_Success(t *testing.T) {
	cfg := config.ProviderConfig{
		AWSAccessKeyID:     "AKIAIOSFODNN7EXAMPLE",
		AWSSecretAccessKey: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
		AWSSessionToken:    "FwoGZXIvYXdzEBYaDHqa0AP",
	}

	creds, err := loadCredentials(cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if creds.AccessKeyID != "AKIAIOSFODNN7EXAMPLE" {
		t.Errorf("AccessKeyID = %q, want %q", creds.AccessKeyID, "AKIAIOSFODNN7EXAMPLE")
	}
	if creds.SecretAccessKey != "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY" {
		t.Errorf("SecretAccessKey = %q, want %q", creds.SecretAccessKey, "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY")
	}
	if creds.SessionToken != "FwoGZXIvYXdzEBYaDHqa0AP" {
		t.Errorf("SessionToken = %q, want %q", creds.SessionToken, "FwoGZXIvYXdzEBYaDHqa0AP")
	}
}

func TestLoadCredentials_WithoutSessionToken(t *testing.T) {
	cfg := config.ProviderConfig{
		AWSAccessKeyID:     "AKIAIOSFODNN7EXAMPLE",
		AWSSecretAccessKey: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
	}

	creds, err := loadCredentials(cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if creds.SessionToken != "" {
		t.Errorf("SessionToken should be empty, got %q", creds.SessionToken)
	}
}

func TestLoadCredentials_MissingAccessKey(t *testing.T) {
	cfg := config.ProviderConfig{
		AWSSecretAccessKey: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
	}

	_, err := loadCredentials(cfg)
	if err == nil {
		t.Fatal("expected error when aws_access_key_id is empty")
	}
}

func TestLoadCredentials_MissingSecretKey(t *testing.T) {
	cfg := config.ProviderConfig{
		AWSAccessKeyID: "AKIAIOSFODNN7EXAMPLE",
	}

	_, err := loadCredentials(cfg)
	if err == nil {
		t.Fatal("expected error when aws_secret_access_key is empty")
	}
}

func TestLoadCredentials_BothMissing(t *testing.T) {
	cfg := config.ProviderConfig{}

	_, err := loadCredentials(cfg)
	if err == nil {
		t.Fatal("expected error when both credentials are missing")
	}
}

// ===========================================================================
// hashSHA256 tests
// ===========================================================================

func TestHashSHA256_Correctness(t *testing.T) {
	tests := []struct {
		name  string
		input []byte
		want  string
	}{
		{
			name:  "nil input (empty body hash)",
			input: nil,
			want:  "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
		},
		{
			name:  "empty byte slice",
			input: []byte{},
			want:  "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
		},
		{
			name:  "hello world",
			input: []byte("hello world"),
			want:  "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9",
		},
		{
			name:  "single byte",
			input: []byte("a"),
			want:  "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb",
		},
		{
			name:  "JSON body",
			input: []byte(`{"key":"value"}`),
			want: func() string {
				h := sha256.Sum256([]byte(`{"key":"value"}`))
				return hex.EncodeToString(h[:])
			}(),
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := hashSHA256(tt.input)
			if got != tt.want {
				t.Errorf("hashSHA256(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

func TestHashSHA256_ConsistentWithStdlib(t *testing.T) {
	data := []byte("The quick brown fox jumps over the lazy dog")
	h := sha256.Sum256(data)
	expected := hex.EncodeToString(h[:])
	got := hashSHA256(data)
	if got != expected {
		t.Errorf("hashSHA256 mismatch: got %q, want %q", got, expected)
	}
}

// ===========================================================================
// deriveSigningKey tests
// ===========================================================================

func TestDeriveSigningKey_Deterministic(t *testing.T) {
	key1 := deriveSigningKey("wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20230101", "us-east-1", "bedrock")
	key2 := deriveSigningKey("wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20230101", "us-east-1", "bedrock")

	if !bytes.Equal(key1, key2) {
		t.Error("deriveSigningKey should return deterministic output for the same inputs")
	}
}

func TestDeriveSigningKey_DifferentRegions(t *testing.T) {
	key1 := deriveSigningKey("secret", "20230101", "us-east-1", "bedrock")
	key2 := deriveSigningKey("secret", "20230101", "eu-west-1", "bedrock")

	if bytes.Equal(key1, key2) {
		t.Error("deriveSigningKey should produce different keys for different regions")
	}
}

func TestDeriveSigningKey_DifferentDates(t *testing.T) {
	key1 := deriveSigningKey("secret", "20230101", "us-east-1", "bedrock")
	key2 := deriveSigningKey("secret", "20230102", "us-east-1", "bedrock")

	if bytes.Equal(key1, key2) {
		t.Error("deriveSigningKey should produce different keys for different dates")
	}
}

func TestDeriveSigningKey_DifferentServices(t *testing.T) {
	key1 := deriveSigningKey("secret", "20230101", "us-east-1", "bedrock")
	key2 := deriveSigningKey("secret", "20230101", "us-east-1", "s3")

	if bytes.Equal(key1, key2) {
		t.Error("deriveSigningKey should produce different keys for different services")
	}
}

func TestDeriveSigningKey_Length(t *testing.T) {
	key := deriveSigningKey("secret", "20230101", "us-east-1", "bedrock")
	// HMAC-SHA256 always produces 32 bytes.
	if len(key) != 32 {
		t.Errorf("deriveSigningKey returned %d bytes, want 32", len(key))
	}
}

func TestDeriveSigningKey_MatchesManualComputation(t *testing.T) {
	secret := "testSecret"
	dateStamp := "20230615"
	region := "us-west-2"
	service := "bedrock"

	// Manually compute the expected key following the AWS SigV4 derivation.
	kDate := hmacSHA256Ref([]byte("AWS4"+secret), []byte(dateStamp))
	kRegion := hmacSHA256Ref(kDate, []byte(region))
	kService := hmacSHA256Ref(kRegion, []byte(service))
	kSigning := hmacSHA256Ref(kService, []byte("aws4_request"))

	got := deriveSigningKey(secret, dateStamp, region, service)
	if !bytes.Equal(got, kSigning) {
		t.Errorf("deriveSigningKey mismatch\n got: %x\nwant: %x", got, kSigning)
	}
}

// hmacSHA256Ref is a test helper implementation.
func hmacSHA256Ref(key, data []byte) []byte {
	h := hmac.New(sha256.New, key)
	h.Write(data)
	return h.Sum(nil)
}

// ===========================================================================
// signRequest tests
// ===========================================================================

func TestSignRequest_SetsRequiredHeaders(t *testing.T) {
	body := `{"messages":[]}`
	req, err := http.NewRequest("POST", "https://bedrock-runtime.us-east-1.amazonaws.com/model/anthropic.claude-3-sonnet-20240229-v1:0/invoke", strings.NewReader(body))
	if err != nil {
		t.Fatalf("creating request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")

	creds := &Credentials{
		AccessKeyID:     "AKIAIOSFODNN7EXAMPLE",
		SecretAccessKey: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
	}

	if err := signRequest(req, creds, "us-east-1", "bedrock"); err != nil {
		t.Fatalf("signRequest failed: %v", err)
	}

	// Verify Authorization header is set and uses AWS4-HMAC-SHA256.
	auth := req.Header.Get("Authorization")
	if auth == "" {
		t.Fatal("Authorization header not set")
	}
	if !strings.HasPrefix(auth, "AWS4-HMAC-SHA256 ") {
		t.Errorf("Authorization header does not start with AWS4-HMAC-SHA256: %q", auth)
	}

	// Verify x-amz-date is set and has the expected format (YYYYMMDDTHHMMSSZ).
	amzDate := req.Header.Get("x-amz-date")
	if amzDate == "" {
		t.Fatal("x-amz-date header not set")
	}
	if len(amzDate) != 16 || amzDate[8] != 'T' || amzDate[15] != 'Z' {
		t.Errorf("x-amz-date has unexpected format: %q", amzDate)
	}

	// Verify x-amz-content-sha256 is set and is a valid SHA256 hex digest.
	contentHash := req.Header.Get("x-amz-content-sha256")
	if contentHash == "" {
		t.Fatal("x-amz-content-sha256 header not set")
	}
	if len(contentHash) != 64 {
		t.Errorf("x-amz-content-sha256 length = %d, want 64 hex chars", len(contentHash))
	}
	// Verify the hash is correct for the body.
	expectedHash := hashSHA256([]byte(body))
	if contentHash != expectedHash {
		t.Errorf("x-amz-content-sha256 = %q, want %q", contentHash, expectedHash)
	}
}

func TestSignRequest_AuthorizationContainsCredentialAndSignedHeaders(t *testing.T) {
	req, err := http.NewRequest("POST", "https://bedrock-runtime.us-east-1.amazonaws.com/invoke", strings.NewReader("{}"))
	if err != nil {
		t.Fatalf("creating request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")

	creds := &Credentials{
		AccessKeyID:     "AKIAIOSFODNN7EXAMPLE",
		SecretAccessKey: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
	}

	if err := signRequest(req, creds, "us-east-1", "bedrock"); err != nil {
		t.Fatalf("signRequest failed: %v", err)
	}

	auth := req.Header.Get("Authorization")

	// Check that it contains the credential with the access key.
	if !strings.Contains(auth, "Credential=AKIAIOSFODNN7EXAMPLE/") {
		t.Errorf("Authorization missing Credential: %q", auth)
	}

	// Check that it contains SignedHeaders.
	if !strings.Contains(auth, "SignedHeaders=") {
		t.Errorf("Authorization missing SignedHeaders: %q", auth)
	}

	// Check that it contains Signature.
	if !strings.Contains(auth, "Signature=") {
		t.Errorf("Authorization missing Signature: %q", auth)
	}

	// Check that content-type and host are in signed headers.
	if !strings.Contains(auth, "content-type") {
		t.Errorf("Authorization signed headers missing content-type: %q", auth)
	}
	if !strings.Contains(auth, "host") {
		t.Errorf("Authorization signed headers missing host: %q", auth)
	}
}

func TestSignRequest_WithSessionToken(t *testing.T) {
	req, err := http.NewRequest("POST", "https://bedrock-runtime.us-east-1.amazonaws.com/invoke", strings.NewReader("{}"))
	if err != nil {
		t.Fatalf("creating request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")

	creds := &Credentials{
		AccessKeyID:     "AKIAIOSFODNN7EXAMPLE",
		SecretAccessKey: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
		SessionToken:    "FwoGZXIvYXdzEBYaDHqa0AP",
	}

	if err := signRequest(req, creds, "us-east-1", "bedrock"); err != nil {
		t.Fatalf("signRequest failed: %v", err)
	}

	// When a session token is present, x-amz-security-token should be set.
	secToken := req.Header.Get("x-amz-security-token")
	if secToken != "FwoGZXIvYXdzEBYaDHqa0AP" {
		t.Errorf("x-amz-security-token = %q, want %q", secToken, "FwoGZXIvYXdzEBYaDHqa0AP")
	}

	// x-amz-security-token should appear in the signed headers.
	auth := req.Header.Get("Authorization")
	if !strings.Contains(auth, "x-amz-security-token") {
		t.Errorf("Authorization signed headers missing x-amz-security-token: %q", auth)
	}
}

func TestSignRequest_WithoutSessionToken(t *testing.T) {
	req, err := http.NewRequest("POST", "https://bedrock-runtime.us-east-1.amazonaws.com/invoke", strings.NewReader("{}"))
	if err != nil {
		t.Fatalf("creating request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")

	creds := &Credentials{
		AccessKeyID:     "AKIAIOSFODNN7EXAMPLE",
		SecretAccessKey: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
	}

	if err := signRequest(req, creds, "us-east-1", "bedrock"); err != nil {
		t.Fatalf("signRequest failed: %v", err)
	}

	// Without session token, x-amz-security-token should NOT be set.
	if secToken := req.Header.Get("x-amz-security-token"); secToken != "" {
		t.Errorf("x-amz-security-token should be empty without session token, got %q", secToken)
	}

	auth := req.Header.Get("Authorization")
	if strings.Contains(auth, "x-amz-security-token") {
		t.Errorf("Authorization should not include x-amz-security-token when no session token: %q", auth)
	}
}

func TestSignRequest_NilBody(t *testing.T) {
	req, err := http.NewRequest("GET", "https://bedrock-runtime.us-east-1.amazonaws.com/models", nil)
	if err != nil {
		t.Fatalf("creating request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")

	creds := &Credentials{
		AccessKeyID:     "AKIAIOSFODNN7EXAMPLE",
		SecretAccessKey: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
	}

	if err := signRequest(req, creds, "us-east-1", "bedrock"); err != nil {
		t.Fatalf("signRequest failed: %v", err)
	}

	// x-amz-content-sha256 should be the hash of an empty body.
	contentHash := req.Header.Get("x-amz-content-sha256")
	emptyHash := hashSHA256(nil)
	if contentHash != emptyHash {
		t.Errorf("x-amz-content-sha256 for nil body = %q, want %q", contentHash, emptyHash)
	}

	// Authorization should still be set.
	if auth := req.Header.Get("Authorization"); auth == "" {
		t.Fatal("Authorization header not set for nil-body request")
	}
}

func TestSignRequest_BodyPreservedAfterSigning(t *testing.T) {
	bodyContent := `{"model":"claude-3","messages":[{"role":"user","content":"hello"}]}`
	req, err := http.NewRequest("POST", "https://bedrock-runtime.us-east-1.amazonaws.com/invoke", strings.NewReader(bodyContent))
	if err != nil {
		t.Fatalf("creating request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")

	creds := &Credentials{
		AccessKeyID:     "AKIAIOSFODNN7EXAMPLE",
		SecretAccessKey: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
	}

	if err := signRequest(req, creds, "us-east-1", "bedrock"); err != nil {
		t.Fatalf("signRequest failed: %v", err)
	}

	// After signing, the body must still be readable (signRequest reads it then replaces it).
	readBack, err := io.ReadAll(req.Body)
	if err != nil {
		t.Fatalf("reading body after sign: %v", err)
	}
	if string(readBack) != bodyContent {
		t.Errorf("body after signing = %q, want %q", string(readBack), bodyContent)
	}
}

func TestSignRequest_CredentialScopeContainsRegionAndService(t *testing.T) {
	req, err := http.NewRequest("POST", "https://bedrock-runtime.eu-west-1.amazonaws.com/invoke", strings.NewReader("{}"))
	if err != nil {
		t.Fatalf("creating request: %v", err)
	}
	req.Header.Set("Content-Type", "application/json")

	creds := &Credentials{
		AccessKeyID:     "AKIAIOSFODNN7EXAMPLE",
		SecretAccessKey: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
	}

	if err := signRequest(req, creds, "eu-west-1", "bedrock"); err != nil {
		t.Fatalf("signRequest failed: %v", err)
	}

	auth := req.Header.Get("Authorization")

	// Credential scope should contain the region and service.
	if !strings.Contains(auth, "eu-west-1/bedrock/aws4_request") {
		t.Errorf("Authorization credential scope missing region/service: %q", auth)
	}
}

func TestSignRequest_DifferentRegionsProduceDifferentSignatures(t *testing.T) {
	creds := &Credentials{
		AccessKeyID:     "AKIAIOSFODNN7EXAMPLE",
		SecretAccessKey: "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
	}

	req1, _ := http.NewRequest("POST", "https://bedrock-runtime.us-east-1.amazonaws.com/invoke", strings.NewReader("{}"))
	req1.Header.Set("Content-Type", "application/json")
	signRequest(req1, creds, "us-east-1", "bedrock")

	req2, _ := http.NewRequest("POST", "https://bedrock-runtime.eu-west-1.amazonaws.com/invoke", strings.NewReader("{}"))
	req2.Header.Set("Content-Type", "application/json")
	signRequest(req2, creds, "eu-west-1", "bedrock")

	auth1 := req1.Header.Get("Authorization")
	auth2 := req2.Header.Get("Authorization")

	if auth1 == auth2 {
		t.Error("different regions should produce different Authorization headers")
	}
}
