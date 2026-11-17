package providers

import "fmt"

// ConfigurationError is safe to show to the org that owns this provider. It
// contains an ID and a static explanation, never credentials or upstream errors.
type ConfigurationError struct {
	ProviderID string
	Problem    string
}

func (e *ConfigurationError) Error() string {
	return fmt.Sprintf("provider %q configuration error: %s", e.ProviderID, e.Problem)
}
