package protocol

import "encoding/json"

// MethodLogMessage is the notification an upstream sends a log line with.
const MethodLogMessage = "notifications/message"

// logSeverity orders the syslog levels MCP log messages use (RFC 5424),
// least severe first.
var logSeverity = map[string]int{
	"debug": 0, "info": 1, "notice": 2, "warning": 3,
	"error": 4, "critical": 5, "alert": 6, "emergency": 7,
}

// RelayLog decides whether an upstream notifications/message reaches a
// 2026-07-28 request's response: only when the request's _meta named a
// logLevel, and only for a message at or above it. A server must not send
// log messages for a request without one (2026-07-28 logging), so an unknown
// level on either side drops the message too.
func RelayLog(requestLevel string, message json.RawMessage) bool {
	min, ok := logSeverity[requestLevel]
	if !ok {
		return false
	}
	var p struct {
		Params struct {
			Level string `json:"level"`
		} `json:"params"`
	}
	if json.Unmarshal(message, &p) != nil {
		return false
	}
	got, ok := logSeverity[p.Params.Level]
	return ok && got >= min
}
