package main

import (
	"path/filepath"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// G18's hosted preparation is not activation of the integrated one-key path.
// Load its actual shipped configuration under the hosted policy with synthetic
// operator inputs. Loading validates only: no providers or clients are started.
func TestShippedHostedIntegrationActivationOff(t *testing.T) {
	for _, name := range []string{"AGENTCC_ADMIN_TOKEN", "AGENTCC_INTERNAL_API_KEY", "AGENTCC_WEBHOOK_SECRET", "AGENTCC_REDIS_PASSWORD"} {
		t.Setenv(name, processSecret(t))
	}
	t.Setenv("AGENTCC_DEPLOYMENT_PROFILE", "hosted")
	t.Setenv("AGENTCC_HOSTED_ONEKEY_ACTIVATION", "")
	t.Setenv("AGENTCC_CONTROL_PLANE_URL", "http://backend:8000")
	t.Setenv("AGENTCC_REDIS_ADDRESS", "redis:6379")
	t.Setenv("AGENTCC_TRUSTED_PROXY_CIDR", "192.0.2.10/32")
	cfg, err := config.Load(filepath.Join(onekeyRoot, "agentcc-gateway", "config.hosted.example.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	if !cfg.IsHosted() {
		t.Fatal("shipped hosted configuration did not select the operator's hosted policy")
	}
	if len(cfg.Providers) != 0 {
		t.Fatal("hosted preparation must not configure any funded provider")
	}
	if len(cfg.Routing.ModelFallbacks) != 0 || cfg.Routing.Failover.Enabled || cfg.Routing.Retry.Enabled {
		t.Fatal("hosted preparation activates a fallback or retry profile")
	}
	t.Setenv("AGENTCC_HOSTED_ONEKEY_ACTIVATION", "true")
	_, err = config.Load(filepath.Join(onekeyRoot, "agentcc-gateway", "config.hosted.example.yaml"))
	if err == nil || !strings.Contains(err.Error(), "enforcing velocity cap") {
		t.Fatalf("activation alone must refuse the shipped config until ops explicitly sets velocity caps: %v", err)
	}
}
