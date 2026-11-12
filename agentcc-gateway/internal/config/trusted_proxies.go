package config

import (
	"fmt"
	"net"
	"strings"
)

// ParseTrustedProxies checks server.trusted_proxies and server.trusted_hops
// and returns the trusted networks. Each entry is an address ("10.0.0.7",
// "::1") or a CIDR ("10.0.0.0/8"). At most one of the two settings may be
// set, and trusted_hops must not be negative. Validate refuses a config this
// refuses, so the gateway does not start on a mistyped proxy range.
func ParseTrustedProxies(entries []string, hops int) ([]*net.IPNet, error) {
	if hops < 0 {
		return nil, fmt.Errorf("server.trusted_hops must not be negative, got %d", hops)
	}
	if len(entries) > 0 && hops > 0 {
		return nil, fmt.Errorf("set server.trusted_proxies or server.trusted_hops, not both")
	}
	nets := make([]*net.IPNet, 0, len(entries))
	for _, entry := range entries {
		n := parseAddressOrCIDR(entry)
		if n == nil {
			return nil, fmt.Errorf("server.trusted_proxies: %q is not an IP address or CIDR", entry)
		}
		nets = append(nets, n)
	}
	return nets, nil
}

func parseAddressOrCIDR(s string) *net.IPNet {
	s = strings.TrimSpace(s)
	if _, n, err := net.ParseCIDR(s); err == nil {
		return n
	}
	ip := net.ParseIP(s)
	if ip == nil {
		return nil
	}
	if v4 := ip.To4(); v4 != nil {
		return &net.IPNet{IP: v4, Mask: net.CIDRMask(32, 32)}
	}
	return &net.IPNet{IP: ip, Mask: net.CIDRMask(128, 128)}
}
