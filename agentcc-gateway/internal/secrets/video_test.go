package secrets

import (
	"github.com/futureagi/agentcc-gateway/internal/config"
	"testing"
)

func TestResolveVideoCredentialReference(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Video.Providers["byteplus"] = config.VideoProviderConfig{Enabled: true, CredentialRef: "provider:source"}
	cfg.Providers["source"] = config.ProviderConfig{APIKey: "test-credential"}
	if e := ResolveVideoSecrets(cfg); e != nil {
		t.Fatal(e)
	}
	if cfg.Video.Providers["byteplus"].APIKey != "test-credential" {
		t.Fatal("credential reference not resolved")
	}
}
func TestResolveVideoSecretFailureIsSanitized(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Video.CorrelationSecret = "vault://private-secret-sentinel"
	if e := ResolveVideoSecrets(cfg); e == nil || e.Error() != "video secret backend unavailable" {
		t.Fatal(e)
	}
}
