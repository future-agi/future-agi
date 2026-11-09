package manifest

import (
	"fmt"
	"net/netip"
	"net/url"
	"regexp"
	"slices"
	"strconv"
	"strings"
)

// connectorsHost is the variable a rest provider's URL is served from: the
// host of agentcc-connectors, filled by the gateway (§2.6).
const connectorsHost = "connectorsHost"

var templateVar = regexp.MustCompile(`[{]([A-Za-z_][A-Za-z0-9_]{0,31})[}]`)

// templateVars returns the variables a URL template uses, once each, in
// order of first use.
func templateVars(s string) []string {
	var out []string
	for _, m := range templateVar.FindAllStringSubmatch(s, -1) {
		if !slices.Contains(out, m[1]) {
			out = append(out, m[1])
		}
	}
	return out
}

// expand fills each variable of a URL template with value(name).
func expand(s string, value func(string) string) string {
	return templateVar.ReplaceAllStringFunc(s, func(m string) string { return value(m[1 : len(m)-1]) })
}

// checkURLSyntax is EE's safeEndpoint rule (EE:apps.ts:149-155) beyond the
// pattern: the URL parses, uses https, names a host, and has no userinfo,
// no fragment and a valid port.
func checkURLSyntax(s string) error {
	u, err := url.Parse(s)
	if err != nil {
		return fmt.Errorf("%q is not a URL", s)
	}
	host := u.Hostname()
	switch {
	case u.Scheme != "https" || host == "":
		return fmt.Errorf("%q must be an https URL with a host", s)
	case u.User != nil || u.Fragment != "" || strings.Contains(s, "#"):
		return fmt.Errorf("%q must have no userinfo and no fragment", s)
	case strings.HasPrefix(host, ".") || strings.HasSuffix(host, ".") || strings.Contains(host, ".."):
		return fmt.Errorf("%q has a malformed host", s)
	}
	if p := u.Port(); p != "" {
		if n, err := strconv.Atoi(p); err != nil || n < 1 || n > 65535 {
			return fmt.Errorf("%q has an invalid port", s)
		}
	}
	return nil
}

// checkHTTPSURL checks a URL's syntax and that its host is one EE would
// contact; catalog data names no private, local or metadata host.
func checkHTTPSURL(s string) error {
	if err := checkURLSyntax(s); err != nil {
		return err
	}
	return checkPublicHost(hostOf(s))
}

// checkURLTemplate checks a template as a URL with each variable filled by
// a sample value. A host that is one {variable} (Snowflake's account URL)
// is checked where its value is set, at add-from-catalog time.
func checkURLTemplate(s string) error {
	filled := expand(s, func(string) string { return "x0" })
	if err := checkURLSyntax(filled); err != nil {
		return err
	}
	if hostIsVariable(s) {
		return nil
	}
	return checkPublicHost(hostOf(filled))
}

// templateHost returns the host of an https URL template: what follows
// https:// up to its port, path or query. A variable holds none of those.
func templateHost(s string) string {
	rest := strings.TrimPrefix(s, "https://")
	if i := strings.IndexAny(rest, "/?:"); i >= 0 {
		rest = rest[:i]
	}
	return rest
}

// hostIsVariable reports whether a template's host is exactly one {variable}.
func hostIsVariable(s string) bool {
	host := templateHost(s)
	return host != "" && templateVar.FindString(host) == host
}

// The hosts EE refuses to contact (EE:harness-os/channel-webhook-policy.ts:
// 3-71 at 49321478e4), applied by contactableMcpEndpoint (EE:apps.ts:163-172).
var (
	eeForbiddenHosts    = []string{"instance-data.ec2.internal", "metadata.google.internal", "metadata.azure.internal"}
	eeForbiddenSuffixes = []string{"localhost", "local", "localdomain", "lan", "internal", "home.arpa"}
	eeBlockedPrefixes   = mustPrefixes(
		"0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24",
		"192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
		"224.0.0.0/4", "240.0.0.0/4",
		"::/128", "::1/128", "::ffff:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48", "100::/64", "2001::/23", "2001:db8::/32",
		"2002::/16", "fc00::/7", "fe80::/10", "fec0::/10", "ff00::/8")
)

func mustPrefixes(cidrs ...string) []netip.Prefix {
	out := make([]netip.Prefix, len(cidrs))
	for i, c := range cidrs {
		out[i] = netip.MustParsePrefix(c)
	}
	return out
}

// checkPublicHost refuses a host EE would not contact: empty, a cloud
// metadata name, single-label or under a local suffix, an IP literal in a
// blocked range, or a host a URL parser reads as an IPv4 address although it
// is not written in dotted-decimal form.
func checkPublicHost(host string) error {
	h := strings.TrimSuffix(strings.ToLower(strings.Trim(host, "[]")), ".")
	if h == "" || slices.Contains(eeForbiddenHosts, h) {
		return fmt.Errorf("host %s is not a public host", host)
	}
	if addr, err := netip.ParseAddr(h); err == nil {
		for _, p := range eeBlockedPrefixes {
			if p.Contains(addr) {
				return fmt.Errorf("host %s is not a public host", host)
			}
		}
		return nil
	}
	if endsInNumber(h) {
		return fmt.Errorf("host %s ends in a number, so URL parsers read it as an IPv4 address whatever its spelling (127.1 and 0x7f.0.0.1 are 127.0.0.1); write a name, or the address in dotted-decimal form", host)
	}
	if !strings.Contains(h, ".") {
		return fmt.Errorf("host %s is not a public host", host)
	}
	for _, suffix := range eeForbiddenSuffixes {
		if h == suffix || strings.HasSuffix(h, "."+suffix) {
			return fmt.Errorf("host %s is not a public host", host)
		}
	}
	return nil
}

// endsInNumber is the WHATWG URL Standard's "ends in a number" check, which
// EE's URL parser applies: a host whose last label is decimal digits, or 0x
// with hex digits, is parsed as an IPv4 address, each label read as decimal,
// hex or octal (0x7f.0.0.1, 0177.0.0.1 and 127.1 are all 127.0.0.1), and a
// host that does not parse so is not a URL at all.
func endsInNumber(h string) bool {
	labels := strings.Split(h, ".")
	if labels[len(labels)-1] == "" {
		if len(labels) == 1 {
			return false
		}
		labels = labels[:len(labels)-1]
	}
	last := labels[len(labels)-1]
	if last != "" && strings.Trim(last, "0123456789") == "" {
		return true
	}
	hex, ok := strings.CutPrefix(last, "0x")
	return ok && strings.Trim(hex, "0123456789abcdef") == ""
}

// hostOf returns the host of a URL that has no variables.
func hostOf(s string) string {
	u, err := url.Parse(s)
	if err != nil {
		return ""
	}
	return u.Hostname()
}
