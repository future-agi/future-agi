package secrets

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"strings"
	"time"
)

// GCPConfig configures the GCP Secret Manager backend.
type GCPConfig struct {
	Project string `yaml:"project" json:"project"`
}

// GCPBackend resolves secrets from GCP Secret Manager.
type GCPBackend struct {
	project string
	token   string
	client  *http.Client
}

// NewGCPBackend creates a GCP Secret Manager backend.
func NewGCPBackend(cfg GCPConfig) (*GCPBackend, error) {
	project := cfg.Project
	if project == "" {
		project = os.Getenv("GCLOUD_PROJECT")
	}
	if project == "" {
		project = os.Getenv("GCP_PROJECT")
	}

	token, err := gcpAccessToken()
	if err != nil {
		return nil, fmt.Errorf("gcp: failed to get access token: %w", err)
	}

	return &GCPBackend{
		project: project,
		token:   token,
		client:  &http.Client{Timeout: 10 * time.Second},
	}, nil
}

// Resolve fetches a secret from GCP Secret Manager.
// URI formats:
//   - gcp-sm://projects/P/secrets/S/versions/V (full resource name)
//   - gcp-sm://secret-name (uses default project and "latest" version)
func (g *GCPBackend) Resolve(uri string) (string, error) {
	_, path, _, err := ParseURI(uri)
	if err != nil {
		return "", err
	}

	resourceName := path
	if !strings.HasPrefix(path, "projects/") {
		if g.project == "" {
			return "", fmt.Errorf("gcp: project required for short-form secret name %q", path)
		}
		resourceName = fmt.Sprintf("projects/%s/secrets/%s/versions/latest", g.project, path)
	}

	apiURL := fmt.Sprintf("https://secretmanager.googleapis.com/v1/%s:access", url.PathEscape(resourceName))
	apiURL = strings.ReplaceAll(apiURL, "%2F", "/")

	req, err := http.NewRequest("GET", apiURL, nil)
	if err != nil {
		return "", fmt.Errorf("gcp: create request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+g.token)

	resp, err := g.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("gcp: request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
		return "", fmt.Errorf("gcp: HTTP %d: %s", resp.StatusCode, string(body))
	}

	var result struct {
		Payload struct {
			Data string `json:"data"` // base64-encoded
		} `json:"payload"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("gcp: decode response: %w", err)
	}

	decoded, err := base64.StdEncoding.DecodeString(result.Payload.Data)
	if err != nil {
		return "", fmt.Errorf("gcp: decode payload: %w", err)
	}

	return string(decoded), nil
}

// gcpAccessToken gets an access token from metadata server or gcloud CLI.
func gcpAccessToken() (string, error) {
	// Try metadata server first (works in GCP environments).
	client := &http.Client{Timeout: 2 * time.Second}
	req, _ := http.NewRequest("GET",
		"http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
		nil,
	)
	req.Header.Set("Metadata-Flavor", "Google")

	resp, err := client.Do(req)
	if err == nil {
		defer resp.Body.Close()
		if resp.StatusCode == http.StatusOK {
			var tok struct {
				AccessToken string `json:"access_token"`
			}
			if err := json.NewDecoder(resp.Body).Decode(&tok); err == nil && tok.AccessToken != "" {
				return tok.AccessToken, nil
			}
		}
	}

	// Fall back to gcloud CLI.
	out, err := exec.Command("gcloud", "auth", "print-access-token").Output()
	if err != nil {
		return "", fmt.Errorf("gcloud CLI not available and metadata server unreachable")
	}

	token := strings.TrimSpace(string(out))
	if token == "" {
		return "", fmt.Errorf("empty token from gcloud CLI")
	}
	return token, nil
}
