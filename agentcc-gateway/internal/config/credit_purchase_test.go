package config

import "testing"

func TestDXR3CreditGuidanceDefaultsToHostedPurchase(t *testing.T) {
	if DefaultConfig().Managed.GrantOnlyCredits {
		t.Fatal("omitting the hosted setting must not claim that credit purchase is unavailable")
	}
	for _, tc := range []struct {
		body      string
		grantOnly bool
	}{
		{"managed:\n  require_credits: true\n", false},
		{"managed:\n  grant_only_credits: true\n", true},
		{"managed:\n  grant_only_credits: false\n", false},
	} {
		cfg, err := loadYAML(t, tc.body)
		if err != nil {
			t.Fatal(err)
		}
		if cfg.Managed.GrantOnlyCredits != tc.grantOnly {
			t.Fatalf("config %q grant_only_credits=%v, want %v", tc.body, cfg.Managed.GrantOnlyCredits, tc.grantOnly)
		}
	}
}
