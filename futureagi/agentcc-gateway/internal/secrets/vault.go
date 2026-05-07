package secrets

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// VaultConfig configures the HashiCorp Vault backend.
type VaultConfig struct {
	Address       string `yaml:"address" json:"address"`
	Token         string `yaml:"token" json:"-"`
	AppRoleID     string `yaml:"app_role_id" json:"-"`
	AppRoleSecret string `yaml:"app_role_secret" json:"-"`
}

// VaultBackend resolves secrets from HashiCorp Vault KV v2.
type VaultBackend struct {
	address string
	token   string
	client  *http.Client
}

// NewVaultBackend creates a Vault backend.
// If AppRole credentials are provided, it logs in to obtain a token.
func NewVaultBackend(cfg VaultConfig) (*VaultBackend, error) {
	if cfg.Address == "" {
		return nil, fmt.Errorf("vault: address is required")
	}

	token := cfg.Token
	if token == "" && cfg.AppRoleID != "" {
		var err error
		token, err = vaultAppRoleLogin(cfg.Address, cfg.AppRoleID, cfg.AppRoleSecret)
		if err != nil {
			return nil, fmt.Errorf("vault: approle login: %w", err)
		}
	}

	if token == "" {
		return nil, fmt.Errorf("vault: token or app_role credentials required")
	}

	return &VaultBackend{
		address: cfg.Address,
		token:   token,
		client:  &http.Client{Timeout: 10 * time.Second},
	}, nil
}

// Resolve fetches a secret from Vault.
// URI format: vault://path/to/secret#field
func (v *VaultBackend) Resolve(uri string) (string, error) {
	_, path, field, err := ParseURI(uri)
	if err != nil {
		return "", err
	}

	url := fmt.Sprintf("%s/v1/%s", v.address, path)
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return "", fmt.Errorf("vault: create request: %w", err)
	}
	req.Header.Set("X-Vault-Token", v.token)

	resp, err := v.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("vault: request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
		return "", fmt.Errorf("vault: HTTP %d: %s", resp.StatusCode, string(body))
	}

	var vaultResp struct {
		Data struct {
			Data map[string]interface{} `json:"data"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&vaultResp); err != nil {
		return "", fmt.Errorf("vault: decode response: %w", err)
	}

	data := vaultResp.Data.Data
	if data == nil {
		return "", fmt.Errorf("vault: no data at path %s", path)
	}

	// If no field selector, return the first (or only) value.
	if field == "" {
		if len(data) == 1 {
			for _, v := range data {
				return fmt.Sprintf("%v", v), nil
			}
		}
		return "", fmt.Errorf("vault: multiple fields at path %s, specify #field", path)
	}

	val, ok := data[field]
	if !ok {
		return "", fmt.Errorf("vault: field %q not found at path %s", field, path)
	}

	return fmt.Sprintf("%v", val), nil
}

func vaultAppRoleLogin(address, roleID, secretID string) (string, error) {
	url := fmt.Sprintf("%s/v1/auth/approle/login", address)
	body := fmt.Sprintf(`{"role_id":"%s","secret_id":"%s"}`, roleID, secretID)

	resp, err := http.Post(url, "application/json", strings.NewReader(body))
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
		return "", fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(b))
	}

	var loginResp struct {
		Auth struct {
			ClientToken string `json:"client_token"`
		} `json:"auth"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&loginResp); err != nil {
		return "", err
	}

	if loginResp.Auth.ClientToken == "" {
		return "", fmt.Errorf("empty client_token in response")
	}

	return loginResp.Auth.ClientToken, nil
}
