package tenant

import "testing"

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
