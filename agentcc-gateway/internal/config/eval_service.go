package config

import (
	"fmt"
	"os"

	"github.com/futureagi/agentcc-gateway/internal/evaltarget"
)

// Keep config.yaml startup validation consistent with the request-time
// constructor without importing guardrails (which depends on config).
func validateFutureAGIAuth(cfg map[string]interface{}) error {
	if cfg["provider"] != "futureagi" {
		return nil
	}
	mode, ok := cfg["auth"].(string)
	if _, present := cfg["auth"]; present && !ok {
		return fmt.Errorf("futureagi auth must be pair or gateway_service")
	}
	switch mode {
	case "", "pair":
		return nil
	case "gateway_service":
		base, ok := cfg["base_url"].(string)
		if _, present := cfg["base_url"]; present && !ok {
			return fmt.Errorf("gateway_service base_url must be a string matching FI_BASE_URL")
		}
		if _, err := evaltarget.ServiceBase(expandWholeEnvRef(base), os.Getenv("FI_BASE_URL")); err != nil {
			return err
		}
		if len(os.Getenv("AGENTCC_ADMIN_TOKEN")) < 32 {
			return fmt.Errorf("gateway_service requires AGENTCC_ADMIN_TOKEN of at least 32 characters")
		}
		return nil
	default:
		return fmt.Errorf("futureagi auth must be pair or gateway_service")
	}
}
