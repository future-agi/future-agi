package audit

import (
	"context"
	"sync"
	"testing"
	"time"

	auditpkg "github.com/futureagi/agentcc-gateway/internal/audit"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// testSink collects events for test assertions.
type testSink struct {
	mu     sync.Mutex
	events []*auditpkg.Event
}

func (s *testSink) Write(events []*auditpkg.Event) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.events = append(s.events, events...)
	return nil
}

func (s *testSink) Close() error { return nil }

func (s *testSink) count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.events)
}

func (s *testSink) find(category, action string) *auditpkg.Event {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, e := range s.events {
		if e.Category == category && e.Action == action {
			return e
		}
	}
	return nil
}

func waitFor(sink *testSink, n int) bool {
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if sink.count() >= n {
			return true
		}
		time.Sleep(time.Millisecond)
	}
	return false
}

func testLogger(sink *testSink) *auditpkg.Logger {
	return auditpkg.NewLoggerWithSinks([]auditpkg.Sink{sink}, nil, auditpkg.SeverityInfo, 100)
}

func makeRC(model, provider string, metadata map[string]string) *models.RequestContext {
	rc := &models.RequestContext{
		Model:     model,
		Provider:  provider,
		RequestID: "req-test-123",
		Metadata:  make(map[string]string),
	}
	for k, v := range metadata {
		rc.Metadata[k] = v
	}
	return rc
}

func TestPlugin_Disabled(t *testing.T) {
	sink := &testSink{}
	p := New(testLogger(sink), false)
	rc := makeRC("gpt-4o", "openai", nil)
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Error("disabled should continue")
	}
	p.Close()
	if sink.count() != 0 {
		t.Error("disabled should emit no events")
	}
}

func TestPlugin_NilLogger(t *testing.T) {
	p := New(nil, true)
	rc := makeRC("gpt-4o", "openai", nil)
	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Error("nil logger should continue")
	}
}

func TestPlugin_RequestStartedEvent(t *testing.T) {
	sink := &testSink{}
	logger := testLogger(sink)
	p := New(logger, true)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"auth_key_owner": "alice",
		"key_team":       "engineering",
		"rbac_role":      "member",
	})

	p.ProcessRequest(context.Background(), rc)
	p.Close()

	if !waitFor(sink, 1) {
		t.Fatal("expected at least 1 event")
	}

	e := sink.find("request", "request_started")
	if e == nil {
		t.Fatal("request_started event not found")
	}
	if e.Actor.Owner != "alice" {
		t.Errorf("actor.owner = %q", e.Actor.Owner)
	}
	if e.Actor.Team != "engineering" {
		t.Errorf("actor.team = %q", e.Actor.Team)
	}
	if e.Resource == nil || e.Resource.ID != "gpt-4o" {
		t.Error("resource should be gpt-4o")
	}
}

func TestPlugin_BudgetWarningEvent(t *testing.T) {
	sink := &testSink{}
	logger := testLogger(sink)
	p := New(logger, true)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"budget_warning":   "true",
		"budget_remaining": "50.00",
	})

	p.ProcessRequest(context.Background(), rc)
	p.Close()

	if !waitFor(sink, 2) {
		t.Fatalf("expected 2 events, got %d", sink.count())
	}

	e := sink.find("budget", "budget_checked")
	if e == nil {
		t.Fatal("budget_checked event not found")
	}
	if e.Severity != "warn" {
		t.Errorf("severity = %q, want warn", e.Severity)
	}
}

func TestPlugin_BudgetBlockedEvent(t *testing.T) {
	sink := &testSink{}
	logger := testLogger(sink)
	p := New(logger, true)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"budget_warning":    "true",
		"budget_blocked_by": "user:alice",
	})

	p.ProcessRequest(context.Background(), rc)
	p.Close()

	if !waitFor(sink, 2) {
		t.Fatalf("expected 2 events, got %d", sink.count())
	}

	e := sink.find("budget", "budget_checked")
	if e == nil {
		t.Fatal("budget_checked event not found")
	}
	if e.Severity != "error" {
		t.Errorf("severity = %q, want error", e.Severity)
	}
	if e.Outcome != "denied" {
		t.Errorf("outcome = %q, want denied", e.Outcome)
	}
}

func TestPlugin_RequestCompletedEvent(t *testing.T) {
	sink := &testSink{}
	logger := testLogger(sink)
	p := New(logger, true)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"cost":      "0.005000",
		"rbac_role": "member",
	})
	rc.Response = &models.ChatCompletionResponse{}

	p.ProcessResponse(context.Background(), rc)
	p.Close()

	if !waitFor(sink, 1) {
		t.Fatal("expected 1 event")
	}

	e := sink.find("request", "request_completed")
	if e == nil {
		t.Fatal("request_completed event not found")
	}
	if e.Outcome != "success" {
		t.Errorf("outcome = %q", e.Outcome)
	}
	if e.Metadata["cost"] != "0.005000" {
		t.Errorf("cost metadata = %q", e.Metadata["cost"])
	}
}

func TestPlugin_RequestCompletedWithErrors(t *testing.T) {
	sink := &testSink{}
	logger := testLogger(sink)
	p := New(logger, true)

	rc := makeRC("gpt-4o", "openai", nil)
	rc.Errors = append(rc.Errors, models.ErrInternal("provider timeout"))

	p.ProcessResponse(context.Background(), rc)
	p.Close()

	if !waitFor(sink, 1) {
		t.Fatal("expected 1 event")
	}

	e := sink.find("request", "request_completed")
	if e == nil {
		t.Fatal("request_completed event not found")
	}
	if e.Outcome != "error" {
		t.Errorf("outcome = %q", e.Outcome)
	}
	if e.Severity != "error" {
		t.Errorf("severity = %q", e.Severity)
	}
}

func TestPlugin_GuardrailTriggeredEvent(t *testing.T) {
	sink := &testSink{}
	logger := testLogger(sink)
	p := New(logger, true)

	rc := makeRC("gpt-4o", "openai", map[string]string{
		"guardrail_triggered": "pii-detection",
		"guardrail_action":    "block",
	})
	rc.Flags.GuardrailTriggered = true

	p.ProcessResponse(context.Background(), rc)
	p.Close()

	if !waitFor(sink, 2) {
		t.Fatalf("expected 2 events, got %d", sink.count())
	}

	e := sink.find("guardrail", "guardrail_triggered")
	if e == nil {
		t.Fatal("guardrail_triggered event not found")
	}
}

func TestPlugin_NilResource(t *testing.T) {
	sink := &testSink{}
	logger := testLogger(sink)
	p := New(logger, true)

	rc := makeRC("", "", nil)
	p.ProcessRequest(context.Background(), rc)
	p.Close()

	if !waitFor(sink, 1) {
		t.Fatal("expected 1 event")
	}

	e := sink.find("request", "request_started")
	if e == nil {
		t.Fatal("event not found")
	}
	if e.Resource != nil {
		t.Error("resource should be nil for empty model/provider")
	}
}

func TestPlugin_NameAndPriority(t *testing.T) {
	p := New(nil, true)
	if p.Name() != "audit" {
		t.Errorf("name = %q", p.Name())
	}
	if p.Priority() != 900 {
		t.Errorf("priority = %d", p.Priority())
	}
}

func TestPlugin_CopyMetadata(t *testing.T) {
	rc := makeRC("gpt-4o", "openai", map[string]string{
		"cost":      "1.50",
		"rbac_role": "admin",
		"other":     "ignored",
	})
	m := copyMetadata(rc, "cost", "rbac_role", "missing_key")
	if m["cost"] != "1.50" {
		t.Errorf("cost = %q", m["cost"])
	}
	if m["rbac_role"] != "admin" {
		t.Errorf("rbac_role = %q", m["rbac_role"])
	}
	if _, ok := m["missing_key"]; ok {
		t.Error("missing key should not be in metadata")
	}
	if _, ok := m["other"]; ok {
		t.Error("unspecified key should not be in metadata")
	}
}
