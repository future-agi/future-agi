package config

import (
	"bytes"
	"log/slog"
	"net"
	"net/netip"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/netguard"
)

// Stay equal to netguard except the documented hosted-only ranges and their
// IPv4 embeddings. Public neighbours keep this from masking broad refusals.
func TestHostedAddressClassifierParity(t *testing.T) {
	cases := hostedReservedAddressCases()
	for _, literal := range []string{
		"2001:0:a00:1::1", "2001:0:808:808::f5ff:fffe", "2001:0:808:808::f7f7:f7f7",
		"::7f00:1", "::a00:1", "::a9fe:a9fe", "::808:808", "::", "::1",
		"0.0.0.1", "224.0.0.1", "ff02::1", "255.255.255.255", "168.63.129.16",
		"64:ff9b::a00:1", "64:ff9b::808:808",
		"2002:a00:1::1", "2002:808:808::1", "::ffff:127.0.0.1",
	} {
		cases = append(cases, hostedAddressCase{literal, false})
	}
	cases = append(cases, hostedAddressCase{"64:ff9b:1::808:808", true})
	for _, tc := range cases {
		t.Run(tc.literal, func(t *testing.T) {
			ip := netip.MustParseAddr(tc.literal)
			want := netguard.Classify(net.ParseIP(tc.literal)) != netguard.Public || tc.hostedOnly
			if got := hostedNonPublicAddr(ip); got != want {
				t.Fatalf("hostedNonPublicAddr(%s)=%v, shared policy plus documented additions=%v", tc.literal, got, want)
			}
		})
	}
}

func TestHostedActivatedMoneySettings(t *testing.T) {
	for _, tc := range []struct{ name, yaml, want string }{
		{"credits", "require_credits: false", "managed.require_credits=false"},
		{"redis", "require_redis: false", "managed.require_redis=false"},
		{"guard", "spend_guard: {max_usd_per_minute: 0, max_usd_per_hour: 0, alert_usd_per_hour: 0, new_org_daily_cap_usd: 0}", "managed.spend_guard"},
		{"guard_alert_default", "spend_guard: {max_usd_per_minute: 0, max_usd_per_hour: 0, new_org_daily_cap_usd: 0}", "managed.spend_guard"},
		{"guard_alert_pause", "spend_guard: {max_usd_per_minute: 0, max_usd_per_hour: 0, new_org_daily_cap_usd: 0, auto_pause: true}", "managed.spend_guard"},
		{"guard_alert_custom", "spend_guard: {max_usd_per_minute: 0, max_usd_per_hour: 0, new_org_daily_cap_usd: 0, alert_usd_per_hour: 1, auto_pause: false}", "managed.spend_guard"},
		{"grant", "grant_only_credits: true", "managed.grant_only_credits=true"},
	} {
		for _, profile := range []string{"hosted", "self-hosted"} {
			for _, flag := range []string{"false", "true"} {
				t.Run(tc.name+"/"+profile+"/"+flag, func(t *testing.T) {
					text := hostedFixture(t) + "managed: {" + tc.yaml + "}\n"
					t.Setenv(EnvDeploymentProfile, profile)
					t.Setenv(hostedActivationEnv, flag)
					want := ""
					if profile == "hosted" && flag == "true" {
						want = tc.want
					}
					assertHostedOneKeyLoad(t, text, want)
				})
			}
		}
	}
	// An explicit velocity cap is required; daily caps, alerts and auto_pause
	// do not qualify activation. Either velocity cap may stand alone.
	for _, guard := range []string{
		"max_usd_per_minute: 0.01",
		"max_usd_per_minute: 0.01, max_usd_per_hour: 0, new_org_daily_cap_usd: 0, alert_usd_per_hour: 0",
		"max_usd_per_minute: 0, max_usd_per_hour: 0.01, new_org_daily_cap_usd: 0, alert_usd_per_hour: 0",
	} {
		t.Run("enforcing_control/"+guard, func(t *testing.T) {
			text := hostedFixture(t) + "managed: {spend_guard: {" + guard + "}}\n"
			t.Setenv(hostedActivationEnv, "true")
			assertHostedOneKeyLoad(t, text, "")
		})
	}
}

func TestHostedActivationWithoutProfileWarning(t *testing.T) {
	original := slog.Default()
	t.Cleanup(func() { slog.SetDefault(original) })
	for _, profile := range []string{"", "self-hosted", "hosted"} {
		for _, flag := range []string{"", "false", "true", "synthetic-invalid-flag-do-not-log"} {
			t.Run(profile+"/"+flag, func(t *testing.T) {
				text := hostedFixture(t)
				t.Setenv(EnvDeploymentProfile, profile)
				t.Setenv(hostedActivationEnv, flag)
				var logs bytes.Buffer
				slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
				err := loadHostedFixture(t, text)
				if profile != "hosted" && err != nil {
					t.Fatalf("changed self-hosted behavior: %v", err)
				}
				warned := strings.Contains(logs.String(), "level=WARN") && strings.Contains(logs.String(), hostedActivationEnv) && strings.Contains(logs.String(), "AGENTCC_DEPLOYMENT_PROFILE=hosted")
				if want := profile != "hosted" && flag != ""; warned != want {
					t.Fatalf("warning=%v, want %v; logs=%s", warned, want, logs.String())
				}
				if strings.Contains(logs.String(), "synthetic-invalid-flag-do-not-log") {
					t.Fatal("warning exposed the operator value")
				}
			})
		}
	}
}
