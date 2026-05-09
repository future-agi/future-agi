package secrets

import (
	"fmt"
	"strings"
)

// Backend resolves a secret URI into its plaintext value.
type Backend interface {
	Resolve(uri string) (string, error)
}

// Resolver dispatches secret URIs to the appropriate backend.
type Resolver struct {
	backends map[string]Backend
}

// NewResolver creates a Resolver with the given backends.
// Backends are keyed by URI scheme (e.g. "vault", "aws-sm").
func NewResolver(backends map[string]Backend) *Resolver {
	return &Resolver{backends: backends}
}

// IsSecretURI returns true if the value looks like a secret URI.
func IsSecretURI(value string) bool {
	scheme, _ := parseScheme(value)
	return scheme != ""
}

// Resolve fetches the secret value for a URI.
func (r *Resolver) Resolve(uri string) (string, error) {
	scheme, _ := parseScheme(uri)
	if scheme == "" {
		return "", fmt.Errorf("not a secret URI: %s", uri)
	}

	backend, ok := r.backends[scheme]
	if !ok {
		return "", fmt.Errorf("unsupported secret backend: %s", scheme)
	}

	return backend.Resolve(uri)
}

// ParseURI splits a secret URI into scheme, path, and optional fragment.
// e.g. "vault://secret/data/agentcc/openai#api_key" → ("vault", "secret/data/agentcc/openai", "api_key")
func ParseURI(uri string) (scheme, path, fragment string, err error) {
	scheme, rest := parseScheme(uri)
	if scheme == "" {
		return "", "", "", fmt.Errorf("invalid secret URI: %s", uri)
	}

	// Split on # for optional field selector.
	if idx := strings.Index(rest, "#"); idx >= 0 {
		path = rest[:idx]
		fragment = rest[idx+1:]
	} else {
		path = rest
	}

	if path == "" {
		return "", "", "", fmt.Errorf("empty path in secret URI: %s", uri)
	}

	return scheme, path, fragment, nil
}

// parseScheme extracts the scheme and remaining path from a URI.
// Returns ("", "") if the value is not a recognized secret URI.
func parseScheme(value string) (scheme, rest string) {
	for _, s := range []string{"vault", "aws-sm", "gcp-sm", "azure-kv"} {
		prefix := s + "://"
		if strings.HasPrefix(value, prefix) {
			return s, value[len(prefix):]
		}
	}
	return "", ""
}
