package config

import (
	"strings"
	"testing"
)

func Test733GatewayServiceConfigStartup(t *testing.T) {
	t.Setenv("FI_BASE_URL", "http://LOCALHOST:80/")
	t.Setenv("AGENTCC_ADMIN_TOKEN", "test_only_admin_secret_at_least_32_characters")
	cfg := map[string]interface{}{"provider": "futureagi", "auth": "gateway_service", "eval_id": "15", "base_url": "http://localhost"}
	if err := withRule("service", cfg).Validate(); err != nil {
		t.Fatalf("service auth should not require a pair: %v", err)
	}
	cfg["base_url"] = "http://127.0.0.1:8001"
	if err := withRule("service", cfg).Validate(); err == nil || !strings.Contains(err.Error(), "FI_BASE_URL") {
		t.Fatalf("mismatch must stop startup with a clear message: %v", err)
	}
	delete(cfg, "base_url")
	t.Setenv("AGENTCC_ADMIN_TOKEN", "")
	if err := withRule("service", cfg).Validate(); err == nil || !strings.Contains(err.Error(), "AGENTCC_ADMIN_TOKEN") {
		t.Fatalf("service secret absent must stop startup: %v", err)
	}
}
