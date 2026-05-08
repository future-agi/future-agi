package gauth_test

import (
	"net/http"
	"os"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/providers/gauth"
)

func credentialsFile(t *testing.T) string {
	t.Helper()
	f := os.Getenv("GOOGLE_APPLICATION_CREDENTIALS")
	if f == "" {
		t.Skip("GOOGLE_APPLICATION_CREDENTIALS not set")
	}
	if _, err := os.Stat(f); err != nil {
		t.Skipf("credentials file not found: %s", f)
	}
	return f
}

func TestTokenProvider_LoadAndToken(t *testing.T) {
	f := credentialsFile(t)

	tp, err := gauth.NewTokenProvider(f, gauth.ScopeCloudPlatform)
	if err != nil {
		t.Fatalf("NewTokenProvider: %v", err)
	}

	tok := tp.Token()
	if tok == "" {
		t.Fatal("Token() returned empty string")
	}
	if !strings.HasPrefix(tok, "ya29.") {
		t.Logf("Token prefix: %s... (expected ya29.* for Google OAuth2)", tok[:min(10, len(tok))])
	}
	t.Logf("Token obtained (len=%d)", len(tok))
}

func TestTokenProvider_Cached(t *testing.T) {
	f := credentialsFile(t)

	tp, err := gauth.NewTokenProvider(f, gauth.ScopeCloudPlatform)
	if err != nil {
		t.Fatalf("NewTokenProvider: %v", err)
	}

	tok1 := tp.Token()
	tok2 := tp.Token()
	if tok1 != tok2 {
		t.Error("consecutive Token() calls should return cached token")
	}
}

func TestTokenProvider_VertexAICall(t *testing.T) {
	f := credentialsFile(t)
	project := os.Getenv("GOOGLE_CLOUD_PROJECT")
	location := os.Getenv("GOOGLE_CLOUD_LOCATION")
	if project == "" || location == "" {
		t.Skip("GOOGLE_CLOUD_PROJECT or GOOGLE_CLOUD_LOCATION not set")
	}

	tp, err := gauth.NewTokenProvider(f, gauth.ScopeCloudPlatform)
	if err != nil {
		t.Fatalf("NewTokenProvider: %v", err)
	}

	tok := tp.Token()
	if tok == "" {
		t.Fatal("no token")
	}

	model := "gemini-2.5-flash"
	url := "https://" + location + "-aiplatform.googleapis.com/v1/projects/" + project +
		"/locations/" + location + "/publishers/google/models/" + model + ":generateContent"

	body := `{"contents":[{"role":"user","parts":[{"text":"Reply with exactly: pong"}]}]}`
	req, _ := http.NewRequest("POST", url, strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("HTTP request failed: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("Vertex AI returned HTTP %d (expected 200)", resp.StatusCode)
	}
	t.Logf("Vertex AI call OK: HTTP %d", resp.StatusCode)
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
