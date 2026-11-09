package logging

import (
	"context"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestOptBTraceEmitterPlatformErrorPublication(t *testing.T) {
	const canary = "opaque synthetic full credential not a real key"
	emitter := &TraceEmitter{ch: make(chan TraceRecord, 1)}
	p := &Plugin{cfg: config.RequestLoggingConfig{Enabled: true}, emitter: emitter}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req_trace_optb"
	rc.Model = "fixture"
	rc.Provider = "fixture"
	rc.CredentialSource = "platform"
	original := &models.APIError{Status: 400, ProviderStatus: 400, UpstreamStatus: 400, Message: canary, Code: canary, Type: canary}
	rc.Errors = []error{original}
	p.ProcessResponse(context.Background(), rc)
	select {
	case rec := <-emitter.ch:
		if strings.Contains(rec.ErrorMessage, canary) || strings.Contains(rec.ErrorCode, canary) {
			t.Fatal("trace emitter received platform provider text")
		}
		if rec.ErrorMessage == "" {
			t.Fatal("nonempty diagnostic must have closed category")
		}
	default:
		t.Fatal("trace was not emitted")
	}
	if original.Message != canary || original.Code != canary {
		t.Fatal("trace projection mutated internal error")
	}
}
