package middleware

// A dedicated header wins over a property of
// the same metadata key, as both win over x-agentcc-metadata; so the logged
// client_request_id is always the one x-agentcc-client-request-id echoes.

import (
	"testing"
)

func TestR7SessionNamePathDedicatedHeaderWinsOverAProperty(t *testing.T) {
	_, seen := r7Serve(t, map[string]string{
		"x-agentcc-session-name":          "real-name",
		"x-agentcc-property-session_name": "spoof-name",
		"x-agentcc-session-path":          "/real/path",
		"x-agentcc-property-session_path": "/spoof/path",
		"x-agentcc-metadata":              `{"session_name":"meta-name","team":"ml"}`,
	})
	m := r7Metadata(t, seen)
	if m["session_name"] != "real-name" || m["session_path"] != "/real/path" {
		t.Errorf("metadata session_name %q session_path %q, want the dedicated headers' real-name and /real/path", m["session_name"], m["session_path"])
	}
	if m["team"] != "ml" {
		t.Errorf("metadata team = %q, want ml kept from x-agentcc-metadata", m["team"])
	}
}

func TestR7ClientRequestIDDedicatedHeaderWinsOverAProperty(t *testing.T) {
	w, seen := r7Serve(t, map[string]string{
		"x-agentcc-request-id":                 "real-client-id",
		"x-agentcc-property-client_request_id": "spoof-client-id",
	})
	if got := w.Header().Get("x-agentcc-client-request-id"); got != "real-client-id" {
		t.Errorf("x-agentcc-client-request-id = %q, want real-client-id", got)
	}
	if got := r7Metadata(t, seen)["client_request_id"]; got != "real-client-id" {
		t.Errorf("metadata client_request_id = %q, want the echoed real-client-id", got)
	}
}

// A property still wins over the same key in x-agentcc-metadata.
func TestR7PropertyHeaderWinsOverTheMetadataHeader(t *testing.T) {
	_, seen := r7Serve(t, map[string]string{
		"x-agentcc-property-session_name": "prop-name",
		"x-agentcc-metadata":              `{"session_name":"meta-name"}`,
	})
	if got := r7Metadata(t, seen)["session_name"]; got != "prop-name" {
		t.Errorf("metadata session_name = %q, want the property's prop-name", got)
	}
}
