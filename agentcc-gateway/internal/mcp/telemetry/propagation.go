// Package telemetry implements MCP's bounded, best-effort tool-call traces.
// It owns neither an exporter nor a shutdown loop.
package telemetry

import (
	"encoding/hex"
	"strings"
)

// Parent prefers a valid params._meta traceparent, then the HTTP header.
// No tracestate or arbitrary baggage crosses this boundary.
func Parent(meta, header string) string {
	for _, p := range []string{meta, header} {
		if validParent(p) {
			return p
		}
	}
	return ""
}

func validParent(p string) bool {
	if len(p) != 55 || p[:3] != "00-" || p[35] != '-' || p[52] != '-' || strings.ToLower(p) != p {
		return false
	}
	for _, part := range []string{p[3:35], p[36:52], p[53:]} {
		if _, err := hex.DecodeString(part); err != nil {
			return false
		}
	}
	return strings.Trim(p[3:35], "0") != "" && strings.Trim(p[36:52], "0") != ""
}
