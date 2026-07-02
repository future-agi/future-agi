package server

import (
	"context"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
)

// UsageEmitter is the billing emission contract the server depends on.
type UsageEmitter interface {
	EmitIngestion(orgID string, numTraces, numSpans int, payloadBytes int64, dedupKey string)
}

// Metering is the quota enforcement contract the server depends on.
type Metering interface {
	CheckUsage(ctx context.Context, orgID, eventType string, amount int64) auth.CheckResult
}

// NoopUsageEmitter is used when Redis is not configured — all calls are silent no-ops.
type NoopUsageEmitter struct{}

func (NoopUsageEmitter) EmitIngestion(string, int, int, int64, string) {}

// NoopMetering allows all requests when Redis is not configured.
type NoopMetering struct{}

func (NoopMetering) CheckUsage(_ context.Context, _, _ string, _ int64) auth.CheckResult {
	return auth.CheckResult{Allowed: true}
}
