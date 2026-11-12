package server

import (
	"net/http"
	"testing"
)

// A served capability call keeps its response on
// the core's call record, not on rc, and its request log entry still says
// status_code 200; a call no vendor answered keeps the refusal's status.
func TestCapabilityServedCallLogsStatus200(t *testing.T) {
	g := newCapcoreGateway(t, capcoreOrgConfig([]string{"echo-a"}, true), 500)
	g.grant(t, 100_000)
	if w := g.post(t, "req-status-ok", `{"input":{"q":"hello"}}`); w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body)
	}
	g.vendors.mu.Lock()
	g.vendors.status["echo-a"] = http.StatusBadRequest
	g.vendors.mu.Unlock()
	if w := g.post(t, "req-status-bad", `{"input":{"q":"bad"}}`); w.Code != http.StatusBadGateway {
		t.Fatalf("failed call status %d: %s", w.Code, w.Body)
	}
	entries := g.flushed(t)
	if len(entries) != 2 {
		t.Fatalf("%d log entries, want 2", len(entries))
	}
	if got := entries[0]["status_code"]; got != float64(200) {
		t.Errorf("served capability call logged status_code %v, want 200", got)
	}
	if got := entries[1]["status_code"]; got == float64(200) || got == float64(0) {
		t.Errorf("refused capability call logged status_code %v, want its error status", got)
	}
}
