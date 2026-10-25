package config

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

// TestV2KeyDefaults pins the default of every managed, catalog, generations,
// idempotency, runtime-token and privacy key.
func TestV2KeyDefaults(t *testing.T) {
	c := DefaultConfig()
	m := c.Managed
	checks := map[string][2]any{
		"managed.require_redis":                 {m.RequireRedis, true},
		"managed.hold_ttl":                      {m.HoldTTL, 15 * time.Minute},
		"managed.reserve_default_output_tokens": {m.ReserveDefaultOutputTokens, 4096},
		"managed.unpriced_reserve_usd":          {m.UnpricedReserveUSD, 0.10},
		"managed.max_overdraft_usd":             {m.MaxOverdraftUSD, 0.0},
		"managed.spend_guard":                   {m.SpendGuard, SpendGuardConfig{}},
		"managed.upstream_key_mode":             {m.UpstreamKeyMode, "shared"},
		"managed.upstream_user_hash_key":        {m.UpstreamUserHashKey, ""},
		"managed.account_missing_retry_after":   {m.AccountMissingRetryAfter, 30 * time.Second},
		"managed.processing_fee_bps":            {m.ProcessingFeeBps, 320},
		"catalog.snapshot_dir":                  {c.Catalog.SnapshotDir, ""},
		"catalog.history_size":                  {c.Catalog.HistorySize, 50},
		"catalog.keep_snapshots":                {c.Catalog.KeepSnapshots, 10},
		"catalog.blocked_models":                {c.Catalog.BlockedModels, []string{}},
		"catalog.endpoints_refresh":             {c.Catalog.EndpointsRefresh, 6 * time.Hour},
		"catalog.endpoints_rps":                 {c.Catalog.EndpointsRPS, 5.0},
		// Unset: the engine's own rule applies.
		"catalog.thought_signature_models": {c.Catalog.ThoughtSignatureModels, []string(nil)},
		"generations.ttl":                  {c.Generations.TTL, 72 * time.Hour},
		"idempotency.ttl":                  {c.Idempotency.TTL, 24 * time.Hour},
		"idempotency.max_body_bytes":       {c.Idempotency.MaxBodyBytes, 1048576},
		"otel.propagate_for_agents":        {c.OTel.PropagateForAgents, true},
		"capabilities.qualification_file":  {c.Capabilities.QualificationFile, ""},
		"runtime_tokens.enabled":           {c.RuntimeTokens.Enabled, false},
		"runtime_tokens.audience":          {c.RuntimeTokens.Audience, "futureagi-agentcc-gateway"},
		"runtime_tokens.max_ttl":           {c.RuntimeTokens.MaxTTL, 15 * time.Minute},
		"runtime_tokens.clock_skew":        {c.RuntimeTokens.ClockSkew, 60 * time.Second},
		"privacy.default_content_mode":     {c.Privacy.DefaultContentMode, "full"},
	}
	for key, gw := range checks {
		if !reflect.DeepEqual(gw[0], gw[1]) {
			t.Errorf("%s default = %#v, want %#v", key, gw[0], gw[1])
		}
	}
	if err := c.Validate(); err != nil {
		t.Errorf("the default config does not validate: %v", err)
	}
}

func TestEffectiveUpstreamFeeBps(t *testing.T) {
	fee := 120
	zero := 0
	for _, tc := range []struct {
		name string
		p    ProviderConfig
		want int
	}{
		{"openrouter source unset", ProviderConfig{CatalogSource: "openrouter"}, 550},
		{"direct source unset", ProviderConfig{CatalogSource: "price_card"}, 0},
		{"no source unset", ProviderConfig{}, 0},
		{"set", ProviderConfig{CatalogSource: "openrouter", UpstreamFeeBps: &fee}, 120},
		{"set to zero", ProviderConfig{CatalogSource: "openrouter", UpstreamFeeBps: &zero}, 0},
	} {
		if got := tc.p.EffectiveUpstreamFeeBps(); got != tc.want {
			t.Errorf("%s: EffectiveUpstreamFeeBps = %d, want %d", tc.name, got, tc.want)
		}
	}
}

func TestV2KeysLoadFromYAMLAndEnv(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")
	yaml := `
providers:
  openrouter:
    base_url: http://upstream.invalid
    api_format: openai
    managed: true
    catalog_source: openrouter
    upstream_slug: openrouter
    resale_terms_ref: ""
managed:
  upstream_key_mode: per_org
  spend_guard:
    max_usd_per_minute: 0.5
    auto_pause: true
catalog:
  blocked_models: ["x/y"]
runtime_tokens:
  enabled: true
  issuer: https://runtime.example
  public_keys:
    - kid: k1
      public_key: "-----BEGIN PUBLIC KEY-----"
privacy:
  default_content_mode: metadata
otel:
  enabled: false
`
	if err := os.WriteFile(path, []byte(yaml), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AGENTCC_MANAGED_USER_HASH_KEY", "hash-key-from-env")
	c, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if c.Managed.UpstreamKeyMode != "per_org" || c.Managed.SpendGuard.MaxUSDPerMinute != 0.5 || !c.Managed.SpendGuard.AutoPause {
		t.Errorf("managed = %+v", c.Managed)
	}
	if c.Managed.HoldTTL != 15*time.Minute || !c.Managed.RequireRedis {
		t.Errorf("a partial managed section lost its other defaults: %+v", c.Managed)
	}
	if c.Managed.UpstreamUserHashKey != "hash-key-from-env" {
		t.Errorf("upstream_user_hash_key = %q, want the env value", c.Managed.UpstreamUserHashKey)
	}
	if p := c.Providers["openrouter"]; p.CatalogSource != "openrouter" || p.EffectiveUpstreamFeeBps() != 550 {
		t.Errorf("provider = %+v", p)
	}
	if !reflect.DeepEqual(c.Catalog.BlockedModels, []string{"x/y"}) || c.Catalog.HistorySize != 50 {
		t.Errorf("catalog = %+v", c.Catalog)
	}
	if !c.RuntimeTokens.Enabled || c.RuntimeTokens.Audience != "futureagi-agentcc-gateway" || c.RuntimeTokens.MaxTTL != 15*time.Minute {
		t.Errorf("runtime_tokens = %+v", c.RuntimeTokens)
	}
	if c.Privacy.DefaultContentMode != "metadata" || !c.OTel.PropagateForAgents {
		t.Errorf("privacy = %+v, otel.propagate_for_agents = %v", c.Privacy, c.OTel.PropagateForAgents)
	}
}

func TestV2KeysValidated(t *testing.T) {
	neg := -1
	for name, mutate := range map[string]func(*Config){
		"upstream key mode": func(c *Config) { c.Managed.UpstreamKeyMode = "per_team" },
		"content mode":      func(c *Config) { c.Privacy.DefaultContentMode = "bodies" },
		"negative hold ttl": func(c *Config) { c.Managed.HoldTTL = -time.Second },
		"negative fee": func(c *Config) {
			c.Providers["p"] = ProviderConfig{BaseURL: "http://x", APIFormat: "openai", UpstreamFeeBps: &neg}
		},
		"catalog source": func(c *Config) {
			c.Providers["p"] = ProviderConfig{BaseURL: "http://x", APIFormat: "openai", CatalogSource: "scraped"}
		},
		"runtime ttl":     func(c *Config) { c.RuntimeTokens.MaxTTL = 16 * time.Minute },
		"runtime no keys": func(c *Config) { c.RuntimeTokens.Enabled, c.RuntimeTokens.Issuer = true, "https://i" },
		"runtime no issuer": func(c *Config) {
			c.RuntimeTokens.Enabled, c.RuntimeTokens.PublicKeys = true, []RuntimeTokenKey{{"k", "pem"}}
		},
		"runtime key no kid": func(c *Config) {
			c.RuntimeTokens.Enabled, c.RuntimeTokens.Issuer, c.RuntimeTokens.PublicKeys = true, "https://i", []RuntimeTokenKey{{"", "pem"}}
		},
		"negative idem bytes": func(c *Config) { c.Idempotency.MaxBodyBytes = -1 },
	} {
		c := DefaultConfig()
		mutate(c)
		if err := c.Validate(); err == nil {
			t.Errorf("%s: Validate accepted it", name)
		}
	}
	// A Config built without DefaultConfig still validates its empty enums.
	c := DefaultConfig()
	c.Managed.UpstreamKeyMode, c.Privacy.DefaultContentMode, c.RuntimeTokens.MaxTTL = "", "", 0
	if err := c.Validate(); err != nil && !strings.Contains(err.Error(), "runtime_tokens") {
		t.Errorf("empty v2 enums rejected: %v", err)
	} else if err != nil {
		t.Errorf("empty runtime max_ttl rejected while runtime tokens are off: %v", err)
	}
}
