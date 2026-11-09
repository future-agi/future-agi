package config

import (
	"net"
	"net/netip"

	"github.com/futureagi/agentcc-gateway/internal/netguard"
)

// These are hosted-only additions to netguard's policy, not changes to the
// generic org/request dialer or self-hosted operator configuration. Include
// both ends of each reserved range, even where an individual address has a
// special-purpose assignment. Hosted providers must use public endpoints.
var hostedReservedPrefixes = []netip.Prefix{
	netip.MustParsePrefix("198.18.0.0/15"),  // benchmarking
	netip.MustParsePrefix("240.0.0.0/4"),    // reserved
	netip.MustParsePrefix("192.0.0.0/24"),   // protocol assignments
	netip.MustParsePrefix("192.0.2.0/24"),   // documentation
	netip.MustParsePrefix("fec0::/10"),      // deprecated site-local
	netip.MustParsePrefix("2001:db8::/32"),  // documentation
	netip.MustParsePrefix("64:ff9b:1::/48"), // local NAT64; layout is operator-owned
}

var (
	hostedNAT64     = netip.MustParsePrefix("64:ff9b::/96")
	hostedSixToFour = netip.MustParsePrefix("2002::/16")
	hostedTeredo    = netip.MustParsePrefix("2001::/32")
	hostedV4Compat  = netip.MustParsePrefix("::/96")
)

// Preserve netguard parity everywhere except the documented hosted additions,
// including their tunnel/translator forms. No DNS or connection is needed, so
// Load and -check-config remain network-free with activation on or off.
func hostedNonPublicAddr(ip netip.Addr) bool {
	if netguard.Classify(net.IP(ip.AsSlice())) != netguard.Public {
		return true
	}
	return hostedReservedAddr(ip.Unmap().WithZone(""))
}

func hostedReservedAddr(ip netip.Addr) bool {
	for _, prefix := range hostedReservedPrefixes {
		if prefix.Contains(ip) {
			return true
		}
	}
	if !ip.Is6() {
		return false
	}
	// Mirror netguard's supported embeddings for the hosted-only additions.
	// Recursion always receives an IPv4 address, so it stops after one level.
	v := ip.As16()
	switch {
	case hostedNAT64.Contains(ip), hostedV4Compat.Contains(ip):
		return hostedReservedAddr(netip.AddrFrom4([4]byte{v[12], v[13], v[14], v[15]}))
	case hostedSixToFour.Contains(ip):
		return hostedReservedAddr(netip.AddrFrom4([4]byte{v[2], v[3], v[4], v[5]}))
	case hostedTeredo.Contains(ip):
		// Either the server or the inverted client can be the destination.
		return hostedReservedAddr(netip.AddrFrom4([4]byte{v[4], v[5], v[6], v[7]})) ||
			hostedReservedAddr(netip.AddrFrom4([4]byte{^v[12], ^v[13], ^v[14], ^v[15]}))
	}
	return false
}
