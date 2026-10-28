package guardrails

import (
	"encoding/json"
	"io"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestAsyncSaturationDoesNotClonePayload(t *testing.T) {
	logger := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(io.Discard, &slog.HandlerOptions{Level: slog.LevelError})))
	defer slog.SetDefault(logger)
	engine := NewEngine(config.GuardrailsConfig{}, nil)
	for i := 0; i < maxAsyncGuardrails; i++ {
		engine.asyncSem <- struct{}{}
	}
	content, _ := json.Marshal(strings.Repeat("large request ", 1<<17))
	input := &CheckInput{Request: &models.ChatCompletionRequest{Messages: []models.Message{{Role: "user", Content: content}}}}
	rule := resolvedRule{guardrail: &callbackGuardrail{name: "async", check: func(*CheckInput) *CheckResult {
		t.Error("saturated check executed")
		return nil
	}}}
	if allocations := testing.AllocsPerRun(5, func() { engine.runAsync(rule, input) }); allocations > 10 {
		t.Fatalf("saturated check allocated %.0f times; payload must not be cloned", allocations)
	}
	if len(engine.asyncSem) != maxAsyncGuardrails {
		t.Fatal("dropped check changed semaphore ownership")
	}
}

func TestAsyncSnapshotFailureAndWorkerReleaseAdmission(t *testing.T) {
	engine := NewEngine(config.GuardrailsConfig{}, nil)
	engine.asyncSem = make(chan struct{}, 1)
	seen := make(chan *CheckInput, 1)
	release := make(chan struct{}, 1)
	defer close(release)
	rule := resolvedRule{timeout: time.Second, guardrail: &callbackGuardrail{name: "async", check: func(input *CheckInput) *CheckResult {
		<-release
		seen <- input
		return nil
	}}}
	invalid := &CheckInput{Request: &models.ChatCompletionRequest{Stop: json.RawMessage(`{`)}}
	engine.runAsync(rule, invalid)
	if len(engine.asyncSem) != 0 {
		t.Fatal("failed snapshot leaked admission slot")
	}
	input := &CheckInput{Request: &models.ChatCompletionRequest{Model: "original"}, Metadata: map[string]string{"value": "original"}}
	engine.runAsync(rule, input)
	input.Request.Model = "changed"
	input.Metadata["value"] = "changed"
	release <- struct{}{}
	select {
	case owned := <-seen:
		if owned.Request.Model != "original" || owned.Metadata["value"] != "original" {
			t.Fatal("async check did not receive an owned snapshot")
		}
	case <-time.After(time.Second):
		t.Fatal("admitted worker did not run")
	}
	select {
	case engine.asyncSem <- struct{}{}:
		<-engine.asyncSem
	case <-time.After(time.Second):
		t.Fatal("completed worker leaked admission slot")
	}
}
