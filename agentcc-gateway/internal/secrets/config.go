package secrets

import (
	"fmt"
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// ResolveProviderSecrets walks provider API keys and resolves any secret URIs.
// Returns an error if a secret URI cannot be resolved.
func ResolveProviderSecrets(cfg *config.Config) error {
	// Check if any provider uses secret URIs.
	hasSecrets := false
	for _, p := range cfg.Providers {
		if IsSecretURI(p.APIKey) {
			hasSecrets = true
			break
		}
	}
	if !hasSecrets {
		return nil
	}

	// Build backends on-demand based on which schemes are needed.
	backends := make(map[string]Backend)

	for name, p := range cfg.Providers {
		if !IsSecretURI(p.APIKey) {
			continue
		}

		scheme, _, _, err := ParseURI(p.APIKey)
		if err != nil {
			return fmt.Errorf("provider %q api_key: %w", name, err)
		}

		// Lazily initialize backend.
		if _, ok := backends[scheme]; !ok {
			backend, err := initBackend(scheme, cfg.Secrets)
			if err != nil {
				return fmt.Errorf("provider %q: init %s backend: %w", name, scheme, err)
			}
			backends[scheme] = backend
		}
	}

	resolver := NewResolver(backends)

	for name, p := range cfg.Providers {
		if !IsSecretURI(p.APIKey) {
			continue
		}

		val, err := resolver.Resolve(p.APIKey)
		if err != nil {
			return fmt.Errorf("provider %q api_key: %w", name, err)
		}

		p.APIKey = val
		cfg.Providers[name] = p
		slog.Info("resolved secret for provider", "provider", name)
	}

	return nil
}

func initBackend(scheme string, cfg config.SecretsConfig) (Backend, error) {
	switch scheme {
	case "vault":
		return NewVaultBackend(VaultConfig{
			Address:       cfg.Vault.Address,
			Token:         cfg.Vault.Token,
			AppRoleID:     cfg.Vault.AppRoleID,
			AppRoleSecret: cfg.Vault.AppRoleSecret,
		})
	case "aws-sm":
		return NewAWSBackend(AWSConfig{
			Region:          cfg.AWS.Region,
			AccessKeyID:     cfg.AWS.AccessKeyID,
			SecretAccessKey: cfg.AWS.SecretAccessKey,
		})
	case "gcp-sm":
		return NewGCPBackend(GCPConfig{
			Project: cfg.GCP.Project,
		})
	case "azure-kv":
		return NewAzureBackend(AzureConfig{
			VaultURL:     cfg.Azure.VaultURL,
			TenantID:     cfg.Azure.TenantID,
			ClientID:     cfg.Azure.ClientID,
			ClientSecret: cfg.Azure.ClientSecret,
		})
	default:
		return nil, fmt.Errorf("unsupported secret backend: %s", scheme)
	}
}
