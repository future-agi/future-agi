package lifecycle

import (
	"context"
	"encoding/hex"
	"log/slog"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/otel"
	"github.com/futureagi/agentcc-gateway/internal/video"
)

// SetTelemetry is startup-only, before any worker or handler uses the service.
func (s *Service) SetTelemetry(reg *metrics.Registry, exporter otel.SpanExporter, serviceName string) {
	s.metrics = reg
	s.exporter = exporter
	s.telemetryService = serviceName
	if s.telemetryService == "" {
		s.telemetryService = "agentcc-gateway"
	}
}
func (s *Service) span(name string, j *video.VideoJob) func() {
	span := otel.NewSpan(name, s.telemetryService)
	if raw, e := hex.DecodeString(j.TraceID); e == nil && len(raw) == 16 && !strings.EqualFold(j.TraceID, strings.Repeat("0", 32)) {
		span.TraceID = j.TraceID
	} else {
		j.TraceID = span.TraceID
	}
	if name == "video.submit" {
		j.TraceParentID = span.SpanID
	} else {
		span.ParentID = j.TraceParentID
	}
	return func() {
		if name == "video.submit" && j.TraceID != span.TraceID {
			span.TraceID = j.TraceID
			span.ParentID = j.TraceParentID
		}
		span.Attributes = map[string]any{"video_id": j.ID, "org_id": j.OrgID, "service": j.Service, "model_id": j.ModelID, "provider_job_id": j.ProviderJobID, "attempt_id": j.AttemptID, "state": j.Status}
		span.End()
		if s.exporter != nil {
			if e := s.exporter.Export([]*otel.Span{span}); e != nil {
				slog.Warn("video trace export failed", "video_id", j.ID)
			}
		}
	}
}
func (s *Service) event(name string, j *video.VideoJob, outcome string) {
	if s.metrics == nil {
		return
	}
	labels := map[string]string{}
	if j != nil {
		labels["service"] = j.Service
		labels["model"] = j.ModelID
		labels["status"] = j.Status
	}
	labels["outcome"] = outcome
	s.metrics.VideoEvent(name, labels)
}
func (s *Service) save(ctx context.Context, j *video.VideoJob, l video.Lease) error {
	old, e := s.store.GetAccounting(ctx, j.ID)
	if e != nil {
		return e
	}
	changed := old.Status != j.Status
	if changed {
		j.StateChangedAt = s.clock.Now()
	}
	if e = s.store.Save(ctx, j, l); e != nil {
		return e
	}
	if changed {
		s.event("video_jobs_total", j, "")
		if s.metrics != nil {
			since := old.StateChangedAt
			if since.IsZero() {
				since = old.CreatedAt
			}
			s.metrics.VideoObserve("video_state_duration_seconds", map[string]string{"state": old.Status}, s.clock.Now().Sub(since).Seconds())
		}
		if j.Status == video.StatusSubmissionUnknown {
			s.event("video_submission_unknown_total", j, "")
		}
		slog.Info("video state changed", "video_id", j.ID, "org_id", j.OrgID, "service", j.Service, "model_id", j.ModelID, "status", j.Status)
	}
	if old.ReconcileState != video.ReconcileUnresolved && j.ReconcileState == video.ReconcileUnresolved {
		s.event("video_unresolved_total", j, "")
	}
	if changed && j.Status == video.StatusCompleted {
		for _, a := range j.Artifacts {
			if a.State == video.ArtifactUnavailable {
				s.event("video_result_copy_failures_total", j, "")
				break
			}
		}
	}
	return nil
}

// RefreshMetrics reads the shared ledger rather than accumulating local gauges,
// so a replica restart cannot reset outstanding financial exposure to zero.
func (s *Service) RefreshMetrics(ctx context.Context) error {
	if s.metrics == nil {
		return nil
	}
	jobs, e := s.store.ListAccounting(ctx)
	if e != nil {
		return e
	}
	var reserved, unsettled int64
	for _, j := range jobs {
		switch j.SettlementState {
		case video.SettlementReserved:
			reserved += j.ReservedMicros
		case video.SettlementUnsettled:
			unsettled += j.ReservedMicros
		}
	}
	s.metrics.VideoBalances(reserved, unsettled)
	return nil
}
func (s *Service) claimMetrics(c video.Claim) {
	if c.Lease.Takeover {
		s.event("video_lease_takeovers_total", c.Job, "")
	}
	if s.metrics != nil && c.Job.Status == video.StatusSubmitting {
		s.metrics.VideoObserve("video_queue_age_seconds", nil, s.clock.Now().Sub(c.Job.CreatedAt).Seconds())
	}
}
