package config

import (
	"fmt"
	"net/netip"
	"strings"
	"testing"
)

type hostedAddressCase struct {
	literal    string
	hostedOnly bool
}

// Independent policy vectors: the six hosted-only ranges (both edges), their
// public neighbours, and each supported IPv4 embedding. Do not derive the
// expected answer from the implementation's prefix list or unwrap helper.
func hostedReservedAddressCases() []hostedAddressCase {
	cases := []hostedAddressCase{
		{"fec0::", true}, {"fec0::1", true}, {"feff:ffff:ffff:ffff:ffff:ffff:ffff:ffff", true},
		{"2001:db8::", true}, {"2001:db8::1", true}, {"2001:db8:ffff:ffff:ffff:ffff:ffff:ffff", true},
		{"2001:db7:ffff:ffff:ffff:ffff:ffff:ffff", false}, {"2001:db9::", false},
		{"fe7f::1", false}, {"2606:4700:4700::1111", false},
	}
	for _, tc := range []hostedAddressCase{
		{"198.18.0.0", true}, {"198.18.0.1", true}, {"198.19.255.255", true},
		{"240.0.0.0", true}, {"240.0.0.1", true}, {"255.255.255.254", true},
		{"192.0.0.0", true}, {"192.0.0.8", true}, {"192.0.0.255", true},
		{"192.0.2.0", true}, {"192.0.2.1", true}, {"192.0.2.255", true},
		{"198.17.255.255", false}, {"198.20.0.0", false},
		{"191.255.255.255", false}, {"192.0.1.0", false}, {"192.0.1.255", false}, {"192.0.3.0", false},
		{"8.8.8.8", false},
	} {
		v := netip.MustParseAddr(tc.literal).As4()
		hi, lo := uint16(v[0])<<8|uint16(v[1]), uint16(v[2])<<8|uint16(v[3])
		for _, literal := range []string{
			tc.literal,
			"::ffff:" + tc.literal,
			fmt.Sprintf("::ffff:%x:%x", hi, lo),
			fmt.Sprintf("64:ff9b::%x:%x", hi, lo),
			fmt.Sprintf("2002:%x:%x::1", hi, lo),
			fmt.Sprintf("::%x:%x", hi, lo),
			fmt.Sprintf("2001:0:%x:%x::f7f7:f7f7", hi, lo), // Teredo server, public client
			fmt.Sprintf("2001:0:808:808::%x:%x", ^hi, ^lo), // public server, Teredo client
		} {
			cases = append(cases, hostedAddressCase{literal, tc.hostedOnly})
		}
	}
	return cases
}

func TestHostedReservedProviderURLs(t *testing.T) {
	for _, active := range []bool{false, true} {
		for _, tc := range hostedReservedAddressCases() {
			t.Run(fmt.Sprintf("activation=%v/%s", active, tc.literal), func(t *testing.T) {
				host := tc.literal
				if strings.Contains(host, ":") {
					host = "[" + host + "]"
				}
				text := hostedFixture(t) + fmt.Sprintf("providers: {upstream: {base_url: 'https://%s', api_format: openai, managed: %v}}\n", host, active)
				t.Setenv(hostedActivationEnv, fmt.Sprint(active))
				want := ""
				if tc.hostedOnly {
					want = hostedRefusesPrivate
				}
				assertHostedOneKeyLoad(t, text, want)
				if err := loadHostedFixture(t, text); err != nil && strings.Contains(err.Error(), tc.literal) {
					t.Fatal("refusal echoed the operator's provider address")
				}
				// Hosted-only additions must not change generic operator config.
				t.Setenv(EnvDeploymentProfile, "self-hosted")
				assertHostedOneKeyLoad(t, text, "")
			})
		}
	}
}
