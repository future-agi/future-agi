package tenant

import (
	"encoding/hex"
	"math"
	"strings"
	"testing"
	"time"
)

func fingerprintTestConfig() *GuardrailConfig {
	return &GuardrailConfig{
		PipelineMode: "parallel",
		Checks: map[string]*GuardrailCheck{
			"org-check": {Enabled: true, Action: "block"},
		},
		KeyPolicies: []*KeyGuardrailPolicy{{
			ID: "p1", Name: "policy", Mode: "monitor", Priority: 1,
			KeyIDs: []string{"key_a", "key_b"},
			Checks: map[string]*GuardrailCheck{
				"secret-detection": {
					Enabled: true, Action: "block", ConfidenceThreshold: 0.5,
					Config: map[string]interface{}{"token": "secret-configuration-token"},
				},
			},
		}},
	}
}

func TestKeyPolicyFingerprintExecutionSettings(t *testing.T) {
	for _, tt := range []struct {
		name   string
		mutate func(*GuardrailConfig)
		same   bool
	}{
		{name: "unchanged", mutate: func(*GuardrailConfig) {}, same: true},
		{name: "fail open", mutate: func(c *GuardrailConfig) { c.FailOpen = true }},
		{name: "timeout", mutate: func(c *GuardrailConfig) { c.TimeoutMs = 100 }},
		{name: "explicit default timeout", mutate: func(c *GuardrailConfig) { c.TimeoutMs = 30000 }, same: true},
		{name: "negative timeout uses default", mutate: func(c *GuardrailConfig) { c.TimeoutMs = -1 }, same: true},
		{name: "sequential pipeline", mutate: func(c *GuardrailConfig) { c.PipelineMode = "sequential" }},
		{name: "normalized sequential pipeline", mutate: func(c *GuardrailConfig) { c.PipelineMode = " SEQUENTIAL " }},
		{name: "default parallel pipeline", mutate: func(c *GuardrailConfig) { c.PipelineMode = "" }, same: true},
		{name: "unknown parallel pipeline", mutate: func(c *GuardrailConfig) { c.PipelineMode = "other" }, same: true},
		{name: "normalized parallel pipeline", mutate: func(c *GuardrailConfig) { c.PipelineMode = " PARALLEL " }, same: true},
		{name: "policy mode", mutate: func(c *GuardrailConfig) { c.KeyPolicies[0].Mode = "enforce" }},
		{name: "policy mode case", mutate: func(c *GuardrailConfig) { c.KeyPolicies[0].Mode = "MONITOR" }, same: true},
		{name: "policy mode whitespace enforces", mutate: func(c *GuardrailConfig) { c.KeyPolicies[0].Mode = " monitor " }},
		{name: "policy ID", mutate: func(c *GuardrailConfig) { c.KeyPolicies[0].ID = "p2" }},
		{name: "policy name", mutate: func(c *GuardrailConfig) { c.KeyPolicies[0].Name = "renamed" }},
		{name: "policy priority", mutate: func(c *GuardrailConfig) { c.KeyPolicies[0].Priority++ }},
		{name: "check enabled", mutate: func(c *GuardrailConfig) { c.KeyPolicies[0].Checks["secret-detection"].Enabled = false }},
		{name: "check action", mutate: func(c *GuardrailConfig) { c.KeyPolicies[0].Checks["secret-detection"].Action = "log" }},
		{name: "check threshold", mutate: func(c *GuardrailConfig) { c.KeyPolicies[0].Checks["secret-detection"].ConfidenceThreshold = 0.8 }},
		{name: "check configuration", mutate: func(c *GuardrailConfig) {
			c.KeyPolicies[0].Checks["secret-detection"].Config["token"] = "rotated-secret"
		}},
		{name: "org check enabled", mutate: func(c *GuardrailConfig) { c.Checks["org-check"].Enabled = false }},
		{name: "org check action", mutate: func(c *GuardrailConfig) { c.Checks["org-check"].Action = "log" }},
		{name: "org check threshold", mutate: func(c *GuardrailConfig) { c.Checks["org-check"].ConfidenceThreshold = 0.8 }},
		{name: "org check configuration", mutate: func(c *GuardrailConfig) { c.Checks["org-check"].Config = map[string]interface{}{"token": "org-secret"} }},
		{name: "org check added", mutate: func(c *GuardrailConfig) { c.Checks["new-check"] = &GuardrailCheck{Enabled: true} }},
		{name: "org checks removed", mutate: func(c *GuardrailConfig) { c.Checks = nil }},
		{name: "unselected policy ignored", mutate: func(c *GuardrailConfig) {
			c.KeyPolicies = append(c.KeyPolicies, &KeyGuardrailPolicy{ID: "unselected", KeyIDs: []string{"other-key"}})
		}, same: true},
		{name: "other key assignment ignored", mutate: func(c *GuardrailConfig) { c.KeyPolicies[0].KeyIDs = []string{"key_a"} }, same: true},
	} {
		t.Run(tt.name, func(t *testing.T) {
			cfg := fingerprintTestConfig()
			before := cfg.KeyPolicyFingerprint("key_a")
			tt.mutate(cfg)
			after := cfg.KeyPolicyFingerprint("key_a")
			if same := before == after; same != tt.same {
				t.Fatalf("fingerprints equal = %v; want %v (before %q, after %q)", same, tt.same, before, after)
			}
		})
	}
}

func TestKeyPolicyCacheFingerprintEffectiveSettings(t *testing.T) {
	for _, tt := range []struct {
		name          string
		beforeTimeout int
		afterTimeout  int
		beforeDefault time.Duration
		afterDefault  time.Duration
		same          bool
	}{
		{name: "unchanged default", beforeDefault: time.Second, afterDefault: time.Second, same: true},
		{name: "engine default changes", beforeDefault: time.Second, afterDefault: 2 * time.Second},
		{name: "negative configured timeout uses engine default", beforeTimeout: -1, beforeDefault: time.Second, afterDefault: time.Second, same: true},
		{name: "explicit timeout equals default", afterTimeout: 1000, beforeDefault: time.Second, afterDefault: 2 * time.Second, same: true},
		{name: "configured timeout changes", beforeTimeout: 1000, afterTimeout: 2000, beforeDefault: time.Second, afterDefault: time.Second},
		{name: "default overridden", beforeTimeout: 1000, afterTimeout: 1000, beforeDefault: time.Second, afterDefault: 2 * time.Second, same: true},
		{name: "sub millisecond default changes", beforeDefault: time.Microsecond, afterDefault: 2 * time.Microsecond},
	} {
		t.Run(tt.name, func(t *testing.T) {
			cfg := fingerprintTestConfig()
			cfg.TimeoutMs = tt.beforeTimeout
			before, beforeCacheable := cfg.KeyPolicyCacheFingerprint("key_a", tt.beforeDefault)
			cfg.TimeoutMs = tt.afterTimeout
			after, afterCacheable := cfg.KeyPolicyCacheFingerprint("key_a", tt.afterDefault)
			if !beforeCacheable || !afterCacheable || before == "" || after == "" {
				t.Fatal("encodable selected policies must have cacheable nonempty fingerprints")
			}
			if same := before == after; same != tt.same {
				t.Fatalf("fingerprints equal = %v; want %v", same, tt.same)
			}
		})
	}

	for _, mode := range []string{"", "ENFORCE", "unknown", " monitor ", " monitor", "monitor "} {
		t.Run("enforce mode "+mode, func(t *testing.T) {
			cfg := fingerprintTestConfig()
			cfg.KeyPolicies[0].Mode = "enforce"
			before, _ := cfg.KeyPolicyCacheFingerprint("key_a", time.Second)
			cfg.KeyPolicies[0].Mode = mode
			after, cacheable := cfg.KeyPolicyCacheFingerprint("key_a", time.Second)
			if !cacheable || before != after {
				t.Fatal("equivalent enforce modes must have the same cacheable fingerprint")
			}
		})
	}

	cfg := fingerprintTestConfig()
	cfg.PipelineMode = "sequential"
	before, _ := cfg.KeyPolicyCacheFingerprint("key_a", time.Second)
	cfg.PipelineMode = " SEQUENTIAL "
	after, cacheable := cfg.KeyPolicyCacheFingerprint("key_a", time.Second)
	if !cacheable || before != after {
		t.Fatal("equivalent sequential modes must have the same cacheable fingerprint")
	}
}

func TestKeyPolicyCacheFingerprintUnscoped(t *testing.T) {
	for _, tt := range []struct {
		name  string
		cfg   *GuardrailConfig
		keyID string
	}{
		{name: "nil config", keyID: "key_a"},
		{name: "empty config", cfg: &GuardrailConfig{}, keyID: "key_a"},
		{name: "no key", cfg: fingerprintTestConfig()},
		{name: "unselected key", cfg: fingerprintTestConfig(), keyID: "other-key"},
		{name: "only nil policies", cfg: &GuardrailConfig{KeyPolicies: []*KeyGuardrailPolicy{nil}}, keyID: "key_a"},
		{name: "unencodable org without selected policies", cfg: &GuardrailConfig{
			Checks: map[string]*GuardrailCheck{"bad": {Config: map[string]interface{}{"bad": make(chan int)}}},
		}, keyID: "key_a"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			if got, cacheable := tt.cfg.KeyPolicyCacheFingerprint(tt.keyID, time.Second); got != "" || !cacheable {
				t.Fatalf("unscoped fingerprint = %q, cacheable = %v; want empty, true", got, cacheable)
			}
		})
	}
}

func TestKeyPolicyCacheFingerprintUnencodable(t *testing.T) {
	cycle := map[string]interface{}{}
	cycle["cycle"] = cycle
	for _, tt := range []struct {
		name  string
		value interface{}
	}{
		{name: "function", value: func() {}},
		{name: "channel", value: make(chan int)},
		{name: "NaN", value: math.NaN()},
		{name: "infinity", value: math.Inf(1)},
		{name: "cycle", value: cycle},
	} {
		for _, orgCheck := range []bool{false, true} {
			name := "policy/" + tt.name
			if orgCheck {
				name = "org/" + tt.name
			}
			t.Run(name, func(t *testing.T) {
				cfg := fingerprintTestConfig()
				check := cfg.KeyPolicies[0].Checks["secret-detection"]
				if orgCheck {
					check = cfg.Checks["org-check"]
				}
				check.Config = map[string]interface{}{"secret-configuration-token": tt.value}
				got, cacheable := cfg.KeyPolicyCacheFingerprint("key_a", time.Second)
				if got == "" || cacheable {
					t.Fatalf("unencodable fingerprint = %q, cacheable = %v; want nonempty, false", got, cacheable)
				}
				if strings.Contains(got, "secret-configuration-token") {
					t.Fatal("unencodable sentinel exposes configuration")
				}
				if cfg.KeyPolicyFingerprint("key_a") == "" {
					t.Fatal("legacy wrapper must not confuse unencodable policies with unscoped keys")
				}
			})
		}
	}
}

func TestKeyPolicyCacheFingerprintOpaqueAndDeterministic(t *testing.T) {
	cfg := fingerprintTestConfig()
	cfg.Checks["org-check"].Config = map[string]interface{}{"authorization": "org-credential-secret"}
	cfg.KeyPolicies = append(cfg.KeyPolicies, nil, &KeyGuardrailPolicy{
		ID: "p2", Name: "second-policy", KeyIDs: []string{"key_a", "key_b"},
	})
	before, cacheable := cfg.KeyPolicyCacheFingerprint("key_a", 30*time.Second)
	decoded, err := hex.DecodeString(before)
	if !cacheable || err != nil || len(decoded) != 32 {
		t.Fatalf("fingerprint = %q, cacheable = %v; want SHA256 hex digest", before, cacheable)
	}
	for _, secret := range []string{"key_a", "secret-configuration-token", "org-credential-secret", "second-policy"} {
		if strings.Contains(before, secret) {
			t.Fatalf("fingerprint exposes %q", secret)
		}
	}
	if cfg.KeyPolicyFingerprint("key_a") != before {
		t.Fatal("legacy wrapper must use a 30 second default")
	}
	otherKey, _ := cfg.KeyPolicyCacheFingerprint("key_b", 30*time.Second)
	if before == otherKey {
		t.Fatal("identical policies on distinct keys must not share fingerprints")
	}
	cfg.KeyPolicies[0], cfg.KeyPolicies[2] = cfg.KeyPolicies[2], cfg.KeyPolicies[0]
	after, cacheable := cfg.KeyPolicyCacheFingerprint("key_a", 30*time.Second)
	if !cacheable || before != after {
		t.Fatal("policy order must not affect fingerprint")
	}
	cfg.KeyPolicies[2].Checks["secret-detection"].Config = map[string]interface{}{"z": "last", "a": "first"}
	before, _ = cfg.KeyPolicyCacheFingerprint("key_a", 30*time.Second)
	cfg.KeyPolicies[2].Checks["secret-detection"].Config = map[string]interface{}{"a": "first", "z": "last"}
	after, cacheable = cfg.KeyPolicyCacheFingerprint("key_a", 30*time.Second)
	if !cacheable || before != after {
		t.Fatal("configuration map insertion order must not affect fingerprint")
	}
}

func TestKeyPolicyFingerprint(t *testing.T) {
	pol := &KeyGuardrailPolicy{ID: "p1", Mode: "monitor", KeyIDs: []string{"key_a", "key_c"},
		Checks: map[string]*GuardrailCheck{"secret-detection": {Enabled: true, Action: "block"}}}
	other := &KeyGuardrailPolicy{ID: "p2", Mode: "enforce", KeyIDs: []string{"key_c"}}
	cfg := &GuardrailConfig{KeyPolicies: []*KeyGuardrailPolicy{nil, pol, other}}

	if got := cfg.KeyPolicyFingerprint("key_b"); got != "" {
		t.Fatalf("key without policies: fingerprint %q; want empty", got)
	}
	if got := cfg.KeyPolicyFingerprint(""); got != "" {
		t.Fatalf("no key: fingerprint %q; want empty", got)
	}
	if got := (*GuardrailConfig)(nil).KeyPolicyFingerprint("key_a"); got != "" {
		t.Fatalf("nil config: fingerprint %q; want empty", got)
	}
	a := cfg.KeyPolicyFingerprint("key_a")
	c := cfg.KeyPolicyFingerprint("key_c")
	if a == "" || a == c {
		t.Fatalf("fingerprints a=%q c=%q; want non-empty and different (c has a second policy)", a, c)
	}
	if again := cfg.KeyPolicyFingerprint("key_a"); again != a {
		t.Fatalf("fingerprint not stable: %q then %q", a, again)
	}
	pol.Mode = "enforce"
	enforced := cfg.KeyPolicyFingerprint("key_a")
	if enforced == a {
		t.Fatal("Monitor to Enforce kept the fingerprint")
	}
	pol.Checks["secret-detection"].Action = "log"
	if cfg.KeyPolicyFingerprint("key_a") == enforced {
		t.Fatal("changing a check's action kept the fingerprint")
	}
	// A rename can change the order policies run in.
	before := cfg.KeyPolicyFingerprint("key_a")
	pol.Name = "renamed"
	if cfg.KeyPolicyFingerprint("key_a") == before {
		t.Fatal("renaming the policy kept the fingerprint")
	}
	// Two keys never share one, even under the same policy: a check may read
	// the request's key.
	pol.KeyIDs = append(pol.KeyIDs, "key_d")
	if cfg.KeyPolicyFingerprint("key_d") == cfg.KeyPolicyFingerprint("key_a") {
		t.Fatal("two keys under the same policy share a fingerprint")
	}
}
