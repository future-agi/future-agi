package secrets

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

// AzureConfig configures the Azure Key Vault backend.
type AzureConfig struct {
	VaultURL     string `yaml:"vault_url" json:"vault_url"` // default vault URL
	TenantID     string `yaml:"tenant_id" json:"-"`
	ClientID     string `yaml:"client_id" json:"-"`
	ClientSecret string `yaml:"client_secret" json:"-"`
}

// AzureBackend resolves secrets from Azure Key Vault.
type AzureBackend struct {
	defaultVaultURL string
	token           string
	client          *http.Client
}

// NewAzureBackend creates an Azure Key Vault backend.
func NewAzureBackend(cfg AzureConfig) (*AzureBackend, error) {
	tenantID := cfg.TenantID
	if tenantID == "" {
		tenantID = os.Getenv("AZURE_TENANT_ID")
	}
	clientID := cfg.ClientID
	if clientID == "" {
		clientID = os.Getenv("AZURE_CLIENT_ID")
	}
	clientSecret := cfg.ClientSecret
	if clientSecret == "" {
		clientSecret = os.Getenv("AZURE_CLIENT_SECRET")
	}

	if tenantID == "" || clientID == "" || clientSecret == "" {
		return nil, fmt.Errorf("azure: tenant_id, client_id, and client_secret required")
	}

	token, err := azureGetToken(tenantID, clientID, clientSecret)
	if err != nil {
		return nil, fmt.Errorf("azure: get token: %w", err)
	}

	return &AzureBackend{
		defaultVaultURL: cfg.VaultURL,
		token:           token,
		client:          &http.Client{Timeout: 10 * time.Second},
	}, nil
}

// Resolve fetches a secret from Azure Key Vault.
// URI format: azure-kv://vault-name/secrets/secret-name
//
//	or: azure-kv://secret-name (uses default vault URL)
func (a *AzureBackend) Resolve(uri string) (string, error) {
	_, path, _, err := ParseURI(uri)
	if err != nil {
		return "", err
	}

	var vaultURL, secretName string

	// Parse path: either "vault-name/secrets/secret-name" or just "secret-name".
	if strings.Contains(path, "/secrets/") {
		parts := strings.SplitN(path, "/secrets/", 2)
		vaultName := parts[0]
		secretName = parts[1]
		vaultURL = fmt.Sprintf("https://%s.vault.azure.net", vaultName)
	} else {
		if a.defaultVaultURL == "" {
			return "", fmt.Errorf("azure: vault_url required for short-form secret name %q", path)
		}
		vaultURL = strings.TrimSuffix(a.defaultVaultURL, "/")
		secretName = path
	}

	apiURL := fmt.Sprintf("%s/secrets/%s?api-version=7.4", vaultURL, url.PathEscape(secretName))

	req, err := http.NewRequest("GET", apiURL, nil)
	if err != nil {
		return "", fmt.Errorf("azure: create request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+a.token)

	resp, err := a.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("azure: request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
		return "", fmt.Errorf("azure: HTTP %d: %s", resp.StatusCode, string(body))
	}

	var result struct {
		Value string `json:"value"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("azure: decode response: %w", err)
	}

	return result.Value, nil
}

// azureGetToken obtains an access token using client credentials flow.
func azureGetToken(tenantID, clientID, clientSecret string) (string, error) {
	tokenURL := fmt.Sprintf("https://login.microsoftonline.com/%s/oauth2/v2.0/token", tenantID)

	data := url.Values{
		"grant_type":    {"client_credentials"},
		"client_id":     {clientID},
		"client_secret": {clientSecret},
		"scope":         {"https://vault.azure.net/.default"},
	}

	resp, err := http.PostForm(tokenURL, data)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
		return "", fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(body))
	}

	var tokenResp struct {
		AccessToken string `json:"access_token"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&tokenResp); err != nil {
		return "", err
	}

	if tokenResp.AccessToken == "" {
		return "", fmt.Errorf("empty access_token in response")
	}

	return tokenResp.AccessToken, nil
}
