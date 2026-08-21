package server

import (
	"context"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/future-agi/future-agi/fi-collector/pkg/catalogwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/propertycatalog"
)

// UsageEmitter is the billing emission contract the server depends on.
type UsageEmitter interface {
	EmitIngestion(orgID string, numTraces, numSpans int, payloadBytes int64, dedupKey string)
}

// Metering is the quota enforcement contract the server depends on.
type Metering interface {
	CheckUsage(ctx context.Context, orgID, eventType string, amount int64) auth.CheckResult
}

// AttributeCatalogWriter is the deliberately narrow, optional ingestion seam
// for the independent span-attribute catalog. Implementations compact the
// already-canonical span rows and durably stage their own work; they must never
// share the span dead-letter or surface catalog failures as span failures.
//
// The interface intentionally excludes replay, coverage, and activation. The
// server only produces catalog work after ClickHouse acknowledged the canonical
// span insert; a separate worker owns catalog delivery. With no option supplied
// (the production default in this change), the path is completely dormant.
type AttributeCatalogWriter interface {
	StageCanonicalSpansByProject([]map[string]any) []catalogwriter.StagedProjectJob
	Enqueue(catalogwriter.Job) error
}

// PropertyCatalogWriter receives canonical rows plus authenticated tenant
// scope out-of-band. It must not require workspace metadata to be persisted in
// the existing spans table.
type PropertyCatalogWriter interface {
	EnqueueCanonicalSpans([]propertycatalog.ScopedSpan) error
}

// NoopUsageEmitter is used when Redis is not configured — all calls are silent no-ops.
type NoopUsageEmitter struct{}

func (NoopUsageEmitter) EmitIngestion(string, int, int, int64, string) {}

// NoopMetering allows all requests when Redis is not configured.
type NoopMetering struct{}

func (NoopMetering) CheckUsage(_ context.Context, _, _ string, _ int64) auth.CheckResult {
	return auth.CheckResult{Allowed: true}
}
