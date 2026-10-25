package egress

import (
	"errors"
	"fmt"
	"net"
	"strconv"
	"strings"
)

// allowEntry is one mcp.egress.private_allow entry, in the forms config
// load accepts (internal/config checkPrivateAllowEntry): a CIDR, or an IP or
// a host name with an optional port.
type allowEntry struct {
	cidr *net.IPNet // a CIDR entry: any address in it, any port
	ip   net.IP     // an IP entry: that address
	host string     // a host-name entry: that name as dialled, lower case, no trailing dot
	port string     // "" matches any port
}

type allowList []allowEntry

func parseAllowList(entries []string) (allowList, error) {
	list := make(allowList, 0, len(entries))
	for i, raw := range entries {
		e, err := parseAllowEntry(raw)
		if err != nil {
			return nil, fmt.Errorf("mcp.egress.private_allow[%d] %q: %w", i, raw, err)
		}
		list = append(list, e)
	}
	return list, nil
}

func parseAllowEntry(raw string) (allowEntry, error) {
	e := strings.TrimSpace(raw)
	if e == "" {
		return allowEntry{}, errors.New("is empty")
	}
	if strings.Contains(e, "/") {
		_, n, err := net.ParseCIDR(e)
		if err != nil {
			return allowEntry{}, errors.New("is not a valid CIDR")
		}
		return allowEntry{cidr: n}, nil
	}
	host, port := e, ""
	if h, p, err := net.SplitHostPort(e); err == nil {
		if n, err := strconv.Atoi(p); err != nil || n < 1 || n > 65535 {
			return allowEntry{}, errors.New("has an invalid port")
		}
		host, port = h, p
	}
	host = normalizeHost(host)
	if host == "" {
		return allowEntry{}, errors.New("has no host")
	}
	if ip := net.ParseIP(host); ip != nil {
		return allowEntry{ip: ip, port: port}, nil
	}
	return allowEntry{host: host, port: port}, nil
}

// normalizeHost lower-cases a host, drops IPv6 brackets and a trailing dot.
func normalizeHost(h string) string {
	return strings.TrimSuffix(strings.ToLower(strings.Trim(h, "[]")), ".")
}

// match reports whether an entry opens a connection to ip:port made for the
// dialled host: a CIDR or IP entry matches the resolved address, a host-name
// entry the name the caller dialled (never the address it resolved to).
func (l allowList) match(host string, ip net.IP, port string) bool {
	host = normalizeHost(host)
	for _, e := range l {
		if e.port != "" && e.port != port {
			continue
		}
		switch {
		case e.cidr != nil:
			if e.cidr.Contains(ip) {
				return true
			}
		case e.ip != nil:
			if e.ip.Equal(ip) {
				return true
			}
		case e.host == host:
			return true
		}
	}
	return false
}
