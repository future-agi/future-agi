// Package evaltarget defines the operator-only destination for fa-eval.
// It is independent of config and guardrails so startup and request validation
// cannot disagree about which URLs may receive a deployment signature.
package evaltarget

import (
	"fmt"
	"net"
	"net/url"
	"strconv"
	"strings"
)

// Normalize returns an HTTP(S) origin. fe1's eval audience serves exactly
// /sdk/api/v1/eval/: path prefixes, userinfo, queries and fragments cannot be
// used as service-auth backends. Only scheme/host case, default ports and
// trailing slashes are insignificant. No DNS lookup is performed.
func Normalize(raw string) (string, error) {
	raw = strings.TrimSpace(raw)
	u, err := url.Parse(raw)
	if err != nil || u == nil {
		return "", fmt.Errorf("eval backend must be an absolute HTTP(S) origin")
	}
	scheme := strings.ToLower(u.Scheme)
	if (scheme != "http" && scheme != "https") || u.Hostname() == "" || u.User != nil ||
		strings.Trim(u.EscapedPath(), "/") != "" || u.RawQuery != "" || u.ForceQuery ||
		strings.ContainsAny(raw, "?#\\ \t\r\n") || strings.HasSuffix(u.Host, ":") || u.Opaque != "" {
		return "", fmt.Errorf("eval backend must be an absolute HTTP(S) origin without credentials, path, query or fragment")
	}
	host, port := strings.ToLower(u.Hostname()), u.Port()
	if port != "" {
		n, err := strconv.Atoi(port)
		if err != nil || n < 1 || n > 65535 {
			return "", fmt.Errorf("eval backend has an invalid port")
		}
		port = strconv.Itoa(n)
	}
	if (scheme == "http" && port == "80") || (scheme == "https" && port == "443") {
		port = ""
	}
	if port != "" {
		host = net.JoinHostPort(host, port)
	} else if strings.Contains(host, ":") {
		host = "[" + host + "]"
	}
	return scheme + "://" + host, nil
}

// ServiceBase never treats an explicitly different URL as an alias for the
// operator. An omitted base inherits FI_BASE_URL, but there is no public default.
func ServiceBase(base, operator string) (string, error) {
	op, err := Normalize(operator)
	if err != nil {
		return "", fmt.Errorf("gateway_service requires FI_BASE_URL to be a valid eval backend origin")
	}
	if base == "" {
		return op, nil
	}
	target, err := Normalize(base)
	if err != nil || target != op {
		return "", fmt.Errorf("gateway_service base_url must match the operator FI_BASE_URL after normalization; check Django BASE_URL and gateway FI_BASE_URL")
	}
	return op, nil
}
