package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

var hostedCapFields = []string{"max_usd_per_minute", "max_usd_per_hour", "new_org_daily_cap_usd"}

func loadHostedCaps(t *testing.T, guard string) error {
	t.Helper()
	text := hostedFixture(t) + "managed: {spend_guard: {" + guard + "}}\n"
	t.Setenv(hostedActivationEnv, "true")
	return loadHostedFixture(t, text)
}

func TestHostedCapInvalidValues(t *testing.T) {
	for _, field := range hostedCapFields {
		for _, value := range []struct{ name, yaml, class string }{
			{"nan", ".nan", "NaN"}, {"inf", ".inf", "infinite"},
			{"tiny", "1e-7", "sub-micro"}, {"tiny4", "4e-7", "sub-micro"},
			{"negative", "-1", "negative"}, {"negative_inf", "-.inf", "infinite"},
		} {
			for _, otherEnforcing := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/%s/other_%t", field, value.name, otherEnforcing), func(t *testing.T) {
					values := map[string]string{"max_usd_per_minute": "0", "max_usd_per_hour": "0", "new_org_daily_cap_usd": "0"}
					if otherEnforcing {
						values["max_usd_per_minute"] = "0.01"
						values["max_usd_per_hour"] = "0.01"
					}
					values[field] = value.yaml
					guard := fmt.Sprintf("max_usd_per_minute: %s, max_usd_per_hour: %s, new_org_daily_cap_usd: %s", values[hostedCapFields[0]], values[hostedCapFields[1]], values[hostedCapFields[2]])
					err := loadHostedCaps(t, guard)
					if err == nil || !strings.Contains(err.Error(), "managed.spend_guard."+field) || !strings.Contains(err.Error(), value.class) {
						t.Fatalf("want field %s and class %s refusal, got %v", field, value.class, err)
					}
				})
			}
		}
	}
}

func TestHostedCapVelocityRequired(t *testing.T) {
	for _, tc := range []struct{ name, guard, want string }{
		{"new_org_only", "max_usd_per_minute: 0, max_usd_per_hour: 0, new_org_daily_cap_usd: 25", "enforcing velocity cap"},
		{"both_nan", "max_usd_per_minute: .nan, max_usd_per_hour: .nan, new_org_daily_cap_usd: 25", "NaN"},
		{"both_tiny", "max_usd_per_minute: 1e-7, max_usd_per_hour: 4e-7, new_org_daily_cap_usd: 25", "sub-micro"},
		{"all_nan", "max_usd_per_minute: .nan, max_usd_per_hour: .nan, new_org_daily_cap_usd: .nan", "NaN"},
		{"omitted", "", "enforcing velocity cap"},
		{"partial_zero", "max_usd_per_minute: 0", "enforcing velocity cap"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if err := loadHostedCaps(t, tc.guard); err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("want %s refusal, got %v", tc.want, err)
			}
		})
	}
}

func TestHostedCapLoadingControls(t *testing.T) {
	for _, field := range hostedCapFields {
		for _, value := range []string{"0.0000005", "0.000001", "0.01"} {
			t.Run(field+"/"+value, func(t *testing.T) {
				minute, hour, daily := "0", "0", "0"
				switch field {
				case "max_usd_per_minute":
					minute = value
				case "max_usd_per_hour":
					hour = value
				default:
					minute, daily = "0.01", value
				}
				if err := loadHostedCaps(t, fmt.Sprintf("max_usd_per_minute: %s, max_usd_per_hour: %s, new_org_daily_cap_usd: %s", minute, hour, daily)); err != nil {
					t.Fatal(err)
				}
			})
		}
	}
}

func TestHostedCapDefaultsAndReload(t *testing.T) {
	text := hostedFixture(t)
	path := filepath.Join(t.TempDir(), "explicit-caps.yaml")
	write := func(extra string) {
		t.Helper()
		if err := os.WriteFile(path, []byte(text+extra), 0600); err != nil {
			t.Fatal(err)
		}
	}
	write("")
	cfg, err := Load(path)
	if err != nil || cfg.Managed.SpendGuard != DefaultSpendGuard() {
		t.Fatalf("preparation lost existing defaults: %v", err)
	}
	t.Setenv(hostedActivationEnv, "true")
	if _, err := Load(path); err == nil || !strings.Contains(err.Error(), "enforcing velocity cap") {
		t.Fatalf("omitted managed block inherited activation caps: %v", err)
	}
	write("managed: {spend_guard: {max_usd_per_hour: 0.01}}\n")
	cfg, err = Load(path)
	if err != nil || cfg.Managed.SpendGuard.MaxUSDPerMinute != 0 || cfg.Managed.SpendGuard.MaxUSDPerHour != 0.01 {
		t.Fatalf("explicit hour control did not load alone: %v", err)
	}
	write("")
	if _, err := Load(path); err == nil {
		t.Fatal("reload inherited previously configured velocity caps")
	}
	t.Setenv(EnvDeploymentProfile, "self-hosted")
	cfg, err = Load(path)
	if err != nil || cfg.Managed.SpendGuard != DefaultSpendGuard() {
		t.Fatalf("self-hosted lost existing defaults: %v", err)
	}
}

func TestSpendGuardNaNRefusedOutsideActivation(t *testing.T) {
	for _, field := range append(append([]string{}, hostedCapFields...), "alert_usd_per_hour") {
		t.Run(field, func(t *testing.T) {
			text := hostedFixture(t) + "managed: {spend_guard: {" + field + ": .nan}}\n"
			t.Setenv(EnvDeploymentProfile, "self-hosted")
			if err := loadHostedFixture(t, text); err == nil {
				t.Fatal("NaN spend guard accepted")
			}
		})
	}
}
