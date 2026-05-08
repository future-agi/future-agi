package alerting

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	alertpkg "github.com/futureagi/agentcc-gateway/internal/alerting"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

type captureChannel struct {
	mu     sync.Mutex
	alerts []alertpkg.Alert
}

func (c *captureChannel) Send(alert alertpkg.Alert) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.alerts = append(c.alerts, alert)
	return nil
}

func (c *captureChannel) Alerts() []alertpkg.Alert {
	c.mu.Lock()
	defer c.mu.Unlock()
	cp := make([]alertpkg.Alert, len(c.alerts))
	copy(cp, c.alerts)
	return cp
}

func newTestRC() *models.RequestContext {
	return &models.RequestContext{
		RequestID: "req-123",
		StartTime: time.Now().Add(-100 * time.Millisecond),
		Model:     "gpt-4",
		Provider:  "openai",
		Metadata:  map[string]string{},
		Timings:   map[string]time.Duration{},
		Errors:    []error{},
		Response: &models.ChatCompletionResponse{
			Usage: &models.Usage{
				PromptTokens:     150,
				CompletionTokens: 50,
			},
		},
	}
}

func TestPlugin_Disabled(t *testing.T) {
	p := New(nil, false, nil)
	result := p.ProcessResponse(context.Background(), newTestRC())
	if result.Action != pipeline.Continue {
		t.Fatal("disabled plugin should continue")
	}
}

func TestPlugin_NilManager(t *testing.T) {
	p := New(nil, true, nil)
	result := p.ProcessResponse(context.Background(), newTestRC())
	if result.Action != pipeline.Continue {
		t.Fatal("nil manager should continue")
	}
}

func TestPlugin_NameAndPriority(t *testing.T) {
	p := New(nil, true, nil)
	if p.Name() != "alerting" {
		t.Fatalf("Name() = %q, want alerting", p.Name())
	}
	if p.Priority() != 997 {
		t.Fatalf("Priority() = %d, want 997", p.Priority())
	}
}

func TestPlugin_ProcessRequest_NoOp(t *testing.T) {
	p := New(nil, true, nil)
	result := p.ProcessRequest(context.Background(), newTestRC())
	if result.Action != pipeline.Continue {
		t.Fatal("ProcessRequest should be no-op")
	}
}

func TestPlugin_RecordsRequestCount(t *testing.T) {
	ch := &captureChannel{}
	rule := alertpkg.NewRule("many-requests", "request_count", ">=", 3, time.Minute, 0, []string{"test"})
	mgr := alertpkg.NewManagerWithChannels([]*alertpkg.Rule{rule}, map[string]alertpkg.Channel{"test": ch})
	p := New(mgr, true, nil)

	for i := 0; i < 3; i++ {
		p.ProcessResponse(context.Background(), newTestRC())
	}

	alerts := ch.Alerts()
	if len(alerts) == 0 {
		t.Fatal("should fire after 3 requests")
	}
	if alerts[0].Name != "many-requests" {
		t.Fatalf("alert name = %q, want many-requests", alerts[0].Name)
	}
}

func TestPlugin_RecordsErrorCount(t *testing.T) {
	ch := &captureChannel{}
	rule := alertpkg.NewRule("errors", "error_count", ">=", 1, time.Minute, 0, []string{"test"})
	mgr := alertpkg.NewManagerWithChannels([]*alertpkg.Rule{rule}, map[string]alertpkg.Channel{"test": ch})
	p := New(mgr, true, nil)

	rc := newTestRC()
	rc.Errors = append(rc.Errors, errors.New("provider timeout"))
	p.ProcessResponse(context.Background(), rc)

	if len(ch.Alerts()) == 0 {
		t.Fatal("should fire on error")
	}
}

func TestPlugin_RecordsCost(t *testing.T) {
	ch := &captureChannel{}
	rule := alertpkg.NewRule("cost-spike", "cost_total", ">=", 0.5, time.Minute, 0, []string{"test"})
	mgr := alertpkg.NewManagerWithChannels([]*alertpkg.Rule{rule}, map[string]alertpkg.Channel{"test": ch})
	p := New(mgr, true, nil)

	rc := newTestRC()
	rc.Metadata["cost"] = "1.000000"
	p.ProcessResponse(context.Background(), rc)

	if len(ch.Alerts()) == 0 {
		t.Fatal("should fire on high cost")
	}
}

func TestPlugin_RecordsTokens(t *testing.T) {
	ch := &captureChannel{}
	rule := alertpkg.NewRule("tokens", "tokens_total", ">=", 100, time.Minute, 0, []string{"test"})
	mgr := alertpkg.NewManagerWithChannels([]*alertpkg.Rule{rule}, map[string]alertpkg.Channel{"test": ch})
	p := New(mgr, true, nil)

	p.ProcessResponse(context.Background(), newTestRC()) // 150 + 50 = 200 tokens

	if len(ch.Alerts()) == 0 {
		t.Fatal("should fire on token count")
	}
}

func TestPlugin_RecordsLatency(t *testing.T) {
	ch := &captureChannel{}
	rule := alertpkg.NewRule("slow", "latency_avg", ">=", 50, time.Minute, 0, []string{"test"})
	mgr := alertpkg.NewManagerWithChannels([]*alertpkg.Rule{rule}, map[string]alertpkg.Channel{"test": ch})
	p := New(mgr, true, nil)

	rc := newTestRC()
	rc.StartTime = time.Now().Add(-200 * time.Millisecond) // 200ms latency
	p.ProcessResponse(context.Background(), rc)

	if len(ch.Alerts()) == 0 {
		t.Fatal("should fire on high latency")
	}
}

func TestPlugin_NilResponse(t *testing.T) {
	ch := &captureChannel{}
	rule := alertpkg.NewRule("requests", "request_count", ">=", 1, time.Minute, 0, []string{"test"})
	mgr := alertpkg.NewManagerWithChannels([]*alertpkg.Rule{rule}, map[string]alertpkg.Channel{"test": ch})
	p := New(mgr, true, nil)

	rc := newTestRC()
	rc.Response = nil
	p.ProcessResponse(context.Background(), rc)

	// Should still count request.
	if len(ch.Alerts()) == 0 {
		t.Fatal("should fire on request count even with nil response")
	}
}

func TestPlugin_RespectsCooldown(t *testing.T) {
	ch := &captureChannel{}
	rule := alertpkg.NewRule("test", "request_count", ">=", 1, time.Minute, time.Hour, []string{"test"})
	mgr := alertpkg.NewManagerWithChannels([]*alertpkg.Rule{rule}, map[string]alertpkg.Channel{"test": ch})
	p := New(mgr, true, nil)

	p.ProcessResponse(context.Background(), newTestRC())
	p.ProcessResponse(context.Background(), newTestRC())

	alerts := ch.Alerts()
	if len(alerts) != 1 {
		t.Fatalf("expected 1 alert (cooldown should block second), got %d", len(alerts))
	}
}
