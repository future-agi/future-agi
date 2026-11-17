package config

import (
	"fmt"
	"strings"
	"testing"
)

// Hosted URL tightening, a hardening of the hosted
// verifier. Under the hosted profile a provider base_url must name a
// public host, whether or not one-key activation is on: one refusal per
// address family that used to load, the checks that already refused, and
// public controls on each side of every new boundary. No name is resolved:
// Load stays network-free.

type hostedURLCase struct{ family, url, want string }

const (
	hostedRefusesPrivate   = "hosted profile refuses private provider URLs"
	hostedRefusesDev       = "hosted profile refuses development provider URLs"
	hostedRefusesNonPublic = "hosted profile refuses non-public provider hostnames"
	hostedRefusesNumeric   = "hosted profile refuses numeric provider hostnames"
)

var hostedURLRefusals = []hostedURLCase{
	{"teredo", "https://[2001:0:a00:1::1]", hostedRefusesPrivate},
	{"teredo", "https://[2001:0:808:808::f5ff:fffe]", hostedRefusesPrivate},
	{"compatible", "https://[::7f00:1]", hostedRefusesPrivate},
	{"compatible", "https://[::a00:1]", hostedRefusesPrivate},
	{"compatible", "https://[::a9fe:a9fe]", hostedRefusesPrivate},
	{"reserved", "https://0.0.0.1", hostedRefusesPrivate},
	{"reserved", "https://224.0.0.1", hostedRefusesPrivate},
	{"reserved", "https://[ff02::1]", hostedRefusesPrivate},
	{"reserved", "https://255.255.255.255", hostedRefusesPrivate},
	{"metadata", "https://168.63.129.16", hostedRefusesPrivate},
	{"internal-name", "https://localhost.localdomain", hostedRefusesNonPublic},
	{"internal-name", "https://api.localdomain", hostedRefusesNonPublic},
	{"internal-name", "https://backend.futureagi.svc", hostedRefusesNonPublic},
	{"internal-name", "https://kubernetes.default", hostedRefusesNonPublic},
	{"internal-name", "https://backend.kube-system", hostedRefusesNonPublic},
	{"internal-name", "https://backend.kube-public", hostedRefusesNonPublic},
	{"internal-name", "https://backend.kube-node-lease", hostedRefusesNonPublic},
	{"internal-name", "https://x.intranet", hostedRefusesNonPublic},
	{"internal-name", "https://x.private", hostedRefusesNonPublic},
	{"internal-name", "https://x.home", hostedRefusesNonPublic},
	{"internal-name", "https://10.0.0.1..", hostedRefusesNonPublic},
	{"internal-name", "https://.example.com", hostedRefusesNonPublic},
	{"internal-name", "https://api..example.com", hostedRefusesNonPublic},
	// Carrier-grade NAT, 100.64.0.0/10.
	{"cgnat", "https://100.64.0.1", hostedRefusesPrivate},
	{"cgnat", "https://100.127.255.254", hostedRefusesPrivate},
	// NAT64 well-known prefix 64:ff9b::/96 embedding a private IPv4 address.
	{"nat64", "https://[64:ff9b::a00:1]", hostedRefusesPrivate},         // 10.0.0.1
	{"nat64", "https://[64:ff9b::7f00:1]", hostedRefusesPrivate},        // 127.0.0.1
	{"nat64", "https://[64:ff9b::a9fe:a9fe]", hostedRefusesPrivate},     // 169.254.169.254
	{"nat64", "https://[64:ff9b::6440:1]", hostedRefusesPrivate},        // 100.64.0.1
	{"nat64-local", "https://[64:ff9b:1::a00:1]", hostedRefusesPrivate}, // RFC 8215 local-use /48
	// 6to4, 2002::/16, embedding a private IPv4 address.
	{"6to4", "https://[2002:a00:1::1]", hostedRefusesPrivate},     // 10.0.0.1
	{"6to4", "https://[2002:a9fe:a9fe::1]", hostedRefusesPrivate}, // 169.254.169.254
	// An IPv4-mapped unspecified address, refused with activation off too.
	{"mapped", "https://[::ffff:0.0.0.0]", hostedRefusesPrivate},
	// Names that are never public DNS.
	{"suffix", "https://metadata.google.internal", hostedRefusesNonPublic},
	{"suffix", "https://metadata.google.internal.", hostedRefusesNonPublic},
	{"suffix", "https://api.lan", hostedRefusesNonPublic},
	{"suffix", "https://router.home.arpa", hostedRefusesNonPublic},
	{"suffix", "https://git.corp", hostedRefusesNonPublic},
	{"single-label", "https://upstream", hostedRefusesNonPublic},
	{"single-label", "https://upstream:8443", hostedRefusesNonPublic},
	// Numeric host forms a resolver may read as an IPv4 address.
	{"numeric", "https://2130706433", hostedRefusesNumeric},
	{"numeric", "https://0x7f.0.0.1", hostedRefusesNumeric},
	{"numeric", "https://0177.0.0.1", hostedRefusesNumeric},
	{"numeric", "https://0x7f000001", hostedRefusesNumeric},
	{"numeric", "https://127.1", hostedRefusesNumeric},
	// Checks that already refused keep their answers.
	{"existing", "https://10.0.0.1", hostedRefusesPrivate},
	{"existing", "https://169.254.169.254", hostedRefusesPrivate},
	{"existing", "https://[::1]", hostedRefusesPrivate},
	{"existing", "https://localhost", hostedRefusesDev},
	{"existing", "https://api.localhost", hostedRefusesDev},
	{"existing", "https://api.local", hostedRefusesDev},
}

var hostedURLPublicControls = []string{
	"https://example.com",
	"https://example.ai",
	"https://default.com",
	"https://kubernetes.io",
	"https://kube-system.org",
	"https://private.example.org",
	"https://[::808:808]",
	"https://[2001:0:808:808::f7f7:f7f7]",
	"https://api.openai.com",
	"https://openrouter.ai/api",
	"https://upstream.synthetic.invalid",
	"https://1password.com",
	"https://internal.example.com",
	"https://corp.example.org",
	"https://8.8.8.8",
	"https://100.63.255.255",
	"https://100.128.0.1",
	"https://[2606:4700:4700::1111]",
	"https://[64:ff9b::808:808]", // NAT64 of 8.8.8.8
	"https://[2002:808:808::1]",  // 6to4 of 8.8.8.8
}

func TestHostedProviderURLFamilies(t *testing.T) {
	for _, activated := range []bool{false, true} {
		flag, managed := "", ""
		if activated {
			flag, managed = "true", ", managed: true"
		}
		provider := func(url string) string {
			return fmt.Sprintf("providers: {upstream: {base_url: '%s', api_format: openai%s}}\n", url, managed)
		}
		for i, tc := range hostedURLRefusals {
			t.Run(fmt.Sprintf("activation=%v/refuse/%s/%d", activated, tc.family, i), func(t *testing.T) {
				text := hostedFixture(t) + provider(tc.url)
				t.Setenv(hostedActivationEnv, flag)
				assertHostedOneKeyLoad(t, text, tc.want)
				err := loadHostedFixture(t, text)
				host := strings.TrimPrefix(tc.url, "https://")
				if err != nil && strings.Contains(err.Error(), strings.Trim(host, "[]")) {
					t.Fatal("refusal echoed the operator's provider host")
				}
			})
		}
		for i, url := range hostedURLPublicControls {
			t.Run(fmt.Sprintf("activation=%v/public/%d", activated, i), func(t *testing.T) {
				text := hostedFixture(t) + provider(url)
				t.Setenv(hostedActivationEnv, flag)
				assertHostedOneKeyLoad(t, text, "")
			})
		}
	}
}
