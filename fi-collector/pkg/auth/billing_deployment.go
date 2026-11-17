package auth

import (
	"strconv"
	"strings"
)

// billingEnforcementConfigured follows the backend's EE/cloud configuration
// signals (tfc.ee_loader._is_oss_mode). Cloud intent stays fail-closed even
// while its secret/license is being repaired; the collector need not receive
// the cloud secret. Usage emission is independent and can never disable caps.
func billingEnforcementConfigured(getenv func(string) string) bool {
	if getenv("EE_LICENSE_KEY") != "" {
		return true
	}
	switch strings.ToUpper(strings.TrimSpace(getenv("CLOUD_DEPLOYMENT"))) {
	case "US", "EU", "DEV":
		return true
	}
	// Helm derives this non-secret signal from the backend's configuration,
	// avoiding a second copy of the license in the collector pod. It can enable
	// enforcement, but cannot override the canonical signals above to disable it.
	configured := strings.TrimSpace(getenv("FI_BILLING_ENFORCEMENT_ENABLED"))
	if configured == "" {
		return false
	}
	enabled, err := strconv.ParseBool(configured)
	// A misspelled explicit setting is never an implicit billing exemption.
	return err != nil || enabled
}
