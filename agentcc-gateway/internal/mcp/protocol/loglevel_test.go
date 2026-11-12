package protocol

import (
	"encoding/json"
	"testing"
)

func TestRelayLog(t *testing.T) {
	msg := func(level string) json.RawMessage {
		return json.RawMessage(`{"jsonrpc":"2.0","method":"notifications/message","params":{"level":"` + level + `"}}`)
	}
	for _, tc := range []struct {
		req, msg string
		want     bool
	}{
		{"", "emergency", false}, {"warning", "error", true}, {"warning", "warning", true},
		{"warning", "info", false}, {"debug", "debug", true}, {"loud", "error", false}, {"info", "verbose", false},
	} {
		if got := RelayLog(tc.req, msg(tc.msg)); got != tc.want {
			t.Errorf("request %q, message %q = %v", tc.req, tc.msg, got)
		}
	}
}
