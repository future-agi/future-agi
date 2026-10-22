package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/routing/reqopts"
)

// keyedUpstreamUser is reqopts.UpstreamUser's keyed form, computed here so
// the test does not read the value it checks from the code under test.
func keyedUpstreamUser(key, orgID string) string {
	m := hmac.New(sha256.New, []byte(key))
	m.Write([]byte(orgID))
	return "fa_org_" + hex.EncodeToString(m.Sum(nil))[:24]
}

// TestRoutingOptionsWiringConfigWins: the routing_options step hands
// managed.upstream_user_hash_key to reqopts, and that key wins over a
// different AGENTCC_MANAGED_USER_HASH_KEY in the environment.
func TestRoutingOptionsWiringConfigWins(t *testing.T) {
	t.Setenv("AGENTCC_MANAGED_USER_HASH_KEY", "env-key")
	t.Cleanup(func() { reqopts.ConfigureUpstreamUser("") })

	cfg := &config.Config{}
	cfg.Managed.UpstreamUserHashKey = "config-key"
	if err := initRoutingOptions(&wiringDeps{cfg: cfg}); err != nil {
		t.Fatalf("initRoutingOptions: %v", err)
	}
	got := reqopts.UpstreamUser("org-hash")
	if want := keyedUpstreamUser("config-key", "org-hash"); got != want {
		t.Fatalf("UpstreamUser = %q, want the config key's %q (env key's would be %q)",
			got, want, keyedUpstreamUser("env-key", "org-hash"))
	}
}
