package config

import (
	"errors"
	"fmt"
	"net"
	"strconv"
	"strings"
)

// MCPEgressConfig is the operator's exception list for the egress guard
// (internal/mcp/egress). The guard refuses a loopback, private/LAN, link-local
// or cloud-metadata address for the callbacks an organization's config names:
// a guardrail webhook, an external guardrail service, a FutureAGI eval base_url
// other than the operator's own FI_BASE_URL, and an alert channel.
type MCPEgressConfig struct {
	// PrivateAllow lists CIDRs, or IPs or host names with an optional port,
	// that may be reached although they are private or loopback. Cloud
	// metadata ranges and host names are refused at load even if listed.
	PrivateAllow []string `yaml:"private_allow" json:"private_allow"`
}

func (e MCPEgressConfig) validate() error {
	for i, entry := range e.PrivateAllow {
		if err := checkPrivateAllowEntry(entry); err != nil {
			return fmt.Errorf("mcp.egress.private_allow[%d] %q: %w", i, entry, err)
		}
	}
	return nil
}

// Cloud metadata services, which mcp.egress.private_allow can never open. The
// dial-time guard (netguard, internal/mcp/egress) refuses them as well.
var (
	metadataNets = mustParseEgressCIDRs(
		"169.254.0.0/16",     // link-local, including the cloud metadata endpoints
		"fd00:ec2::254/128",  // AWS IMDS over IPv6
		"fd20:ce::254/128",   // GCP metadata over IPv6 (IPv6-only instances)
		"100.100.100.200/32", // Alibaba Cloud
		"168.63.129.16/32",   // Azure WireServer
	)
	linkLocalNet      = metadataNets[0]
	metadataHostnames = map[string]bool{
		"metadata.google.internal":   true,
		"metadata.goog":              true,
		"metadata":                   true,
		"instance-data":              true,
		"instance-data.ec2.internal": true,
	}
)

func mustParseEgressCIDRs(cidrs ...string) []*net.IPNet {
	nets := make([]*net.IPNet, len(cidrs))
	for i, s := range cidrs {
		_, n, err := net.ParseCIDR(s)
		if err != nil {
			panic(err)
		}
		nets[i] = n
	}
	return nets
}

// cidrCovers reports whether n contains ip. Unlike net.IPNet.Contains it
// compares 16-byte forms for an IPv6 range, so a range that spans the
// IPv4-mapped block (::/0, ::/1 ...) covers the IPv4 addresses mapped into it.
func cidrCovers(n *net.IPNet, ip net.IP) bool {
	if n.Contains(ip) {
		return true
	}
	ip16, base := ip.To16(), n.IP.To16()
	if len(n.Mask) != net.IPv6len || ip16 == nil || base == nil {
		return false
	}
	for i := range ip16 {
		if ip16[i]&n.Mask[i] != base[i]&n.Mask[i] {
			return false
		}
	}
	return true
}

// checkPrivateAllowEntry checks one mcp.egress.private_allow entry: a CIDR or
// host[:port]. It refuses an entry inside a metadata range, a metadata
// hostname, and any range that covers part of the link-local block (so
// 0.0.0.0/0 too). A wider private range that only contains a single metadata
// address (100.64.0.0/10 holds 100.100.100.200, fc00::/7 holds fd00:ec2::254
// and fd20:ce::254) is accepted: the dial-time guard still refuses that
// address.
func checkPrivateAllowEntry(entry string) error {
	e := strings.TrimSpace(entry)
	if e == "" {
		return errors.New("is empty")
	}
	errMetadata := errors.New("is a cloud-metadata address, which is never reachable")
	if strings.Contains(e, "/") {
		_, n, err := net.ParseCIDR(e)
		if err != nil {
			return errors.New("is not a valid CIDR")
		}
		if cidrCovers(n, linkLocalNet.IP) {
			return errMetadata
		}
		for _, mn := range metadataNets {
			if mn.Contains(n.IP) {
				return errMetadata
			}
		}
		return nil
	}
	host := e
	if h, port, err := net.SplitHostPort(e); err == nil {
		if p, err := strconv.Atoi(port); err != nil || p < 1 || p > 65535 {
			return errors.New("has an invalid port")
		}
		host = h
	}
	host = strings.TrimSuffix(strings.ToLower(strings.Trim(host, "[]")), ".")
	if host == "" {
		return errors.New("has no host")
	}
	if ip := net.ParseIP(host); ip != nil {
		for _, mn := range metadataNets {
			if mn.Contains(ip) {
				return errMetadata
			}
		}
		return nil
	}
	if metadataHostnames[host] {
		return errMetadata
	}
	return nil
}
