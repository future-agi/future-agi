package lifecycle

import (
	"context"
	"errors"
	"math/rand/v2"
	"time"

	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/video"
)

func (s *Service) poll(ctx context.Context, j *video.VideoJob, l video.Lease) error {
	if !j.IsTerminal() && !j.RunBy.IsZero() && !s.clock.Now().Before(j.RunBy) {
		j.ReconcileState = video.ReconcilePending
		j.ReconcileReason = "provider_timeout"
		j.ReconcileBy = s.clock.Now().Add(s.cfg.Poll.TimeoutReconcileWindow)
		j.UpstreamMayContinue = true
		j.SettlementState = video.SettlementUnsettled
		j.NextPollAt = s.clock.Now().Add(5 * time.Minute)
		return s.fail(ctx, j, l, "provider_timeout", false)
	}
	a, ok := s.adapterFor(j)
	if !ok {
		j.ReconcileReason = "provider_binding_unavailable"
		return s.saveLater(ctx, j, l, time.Minute)
	}
	allowed, delay, err := s.rate(ctx, j, "poll")
	if err != nil {
		return err
	}
	if !allowed {
		return s.saveLater(ctx, j, l, delay)
	}
	var obs provider.Observation
	err = s.withPermit(ctx, j, "poll", func(ctx context.Context) error {
		ctx, cancel := context.WithTimeout(ctx, s.cfg.Providers[j.Service].Deadlines.Read)
		defer cancel()
		var e error
		finishSpan := s.span("video.provider.poll", j)
		obs, e = a.Poll(ctx, ref(j))
		finishSpan()
		outcome := "success"
		if e != nil {
			outcome = "error"
			var up *provider.UpstreamError
			if errors.As(e, &up) && up.Status == 429 {
				outcome = "rate_limited"
			}
			var schema *provider.SchemaError
			if errors.As(e, &schema) {
				outcome = "schema_error"
			}
		}
		s.event("video_poll_requests_total", j, outcome)
		return e
	})
	if errors.Is(err, errLimited) {
		return s.saveLater(ctx, j, l, time.Second)
	}
	if e := s.hit("poll.received"); e != nil {
		return e
	}
	now := s.clock.Now()
	j.LastCheckedAt = &now
	if err != nil {
		return s.pollError(ctx, j, l, err)
	}
	if !obs.Normalized.Valid() {
		return s.pollError(ctx, j, l, &provider.SchemaError{Field: "status"})
	}
	return s.observe(ctx, j, l, obs)
}
func (s *Service) pollError(ctx context.Context, j *video.VideoJob, l video.Lease, err error) error {
	var up *provider.UpstreamError
	if errors.As(err, &up) && up.Status == 429 {
		delay := up.RetryAfter
		if delay <= 0 {
			delay = s.backoff(j)
		}
		return s.saveLater(ctx, j, l, delay)
	}
	j.PollFailures++
	var schema *provider.SchemaError
	if errors.As(err, &schema) {
		j.SchemaFailures++
		if j.SchemaFailures >= s.cfg.Poll.MaxUnknownStatus {
			j.UpstreamMayContinue = true
			j.ReconcileState = video.ReconcilePending
			j.ReconcileBy = s.clock.Now().Add(s.cfg.Poll.TimeoutReconcileWindow)
			j.SettlementState = video.SettlementUnsettled
			j.NextPollAt = s.clock.Now().Add(5 * time.Minute)
			return s.fail(ctx, j, l, "adapter_schema_error", false)
		}
	}
	if j.PollFailures >= s.cfg.Poll.MaxConsecutiveFailures && j.ReconcileState != video.ReconcilePending {
		j.ReconcileState = video.ReconcilePending
		j.ReconcileReason = "poll_failures"
		j.ReconcileBy = s.clock.Now().Add(s.cfg.Poll.TimeoutReconcileWindow)
	}
	return s.saveLater(ctx, j, l, s.backoff(j))
}
func (s *Service) backoff(j *video.VideoJob) time.Duration {
	base := s.baseInterval(j)
	prev := time.Duration(j.PollIntervalMS) * time.Millisecond
	if prev < base {
		prev = base
	}
	cap := min(s.cfg.Poll.MaxInterval, prev+prev/2)
	j.PollIntervalMS = cap.Milliseconds()
	// Jitter never violates the adapter's minimum polling interval.
	if cap <= base {
		return base
	}
	return base + time.Duration(rand.Int64N(int64(cap-base)+1))
}
func (s *Service) observe(ctx context.Context, j *video.VideoJob, l video.Lease, obs provider.Observation) error {
	j.PollFailures = 0
	j.SchemaFailures = 0
	j.ProviderState = obs.ProviderState
	if obs.Progress != nil {
		j.Progress = *obs.Progress
		j.ProgressReported = true
	}
	terminal := obs.Normalized == provider.StateCompleted || obs.Normalized == provider.StateFailed || obs.Normalized == provider.StateCancelled
	if !terminal {
		if !j.IsTerminal() {
			if obs.Normalized == provider.StateRunning {
				j.Status = video.StatusRunning
				j.PollIntervalMS = s.baseInterval(j).Milliseconds()
			}
			if j.Status == video.StatusSubmissionUnknown {
				j.Status = video.StatusQueued
			}
		}
		delay := s.backoff(j)
		if obs.Normalized == provider.StateRunning {
			delay = s.baseInterval(j)
		}
		if obs.RetryAfter != nil {
			delay = max(delay, *obs.RetryAfter)
		}
		if j.IsTerminal() {
			delay = 5 * time.Minute
		}
		return s.saveLater(ctx, j, l, delay)
	}
	// Persist provider usage before any copy. Raw responses and supplier error
	// messages are not necessary for recovery and are never retained or logged.
	obs.Raw = nil
	if obs.Error != nil {
		obs.Error = &provider.ProviderError{Code: safeProviderCode(obs.Error.Code), Message: "provider generation failed", Retryable: obs.Error.Retryable}
	}
	j.Observation = &obs
	j.UpstreamMayContinue = false
	if obs.Usage != nil {
		j.Usage = &video.VideoUsage{Lines: append([]provider.UsageLine(nil), obs.Usage.Lines...), Model: j.Model}
	}
	s.priceObservation(j, obs)
	if j.IsTerminal() {
		if obs.Normalized == provider.StateCompleted && j.Status != video.StatusCompleted {
			now := s.clock.Now()
			j.LateSuccessAt = &now
		}
		j.ReconcileState = video.ReconcileResolved
		if e := s.save(ctx, j, l); e != nil {
			return e
		}
		return s.settle(ctx, j, l)
	}
	if obs.Normalized == provider.StateCompleted {
		if j.CopyBy.IsZero() {
			j.CopyBy = s.clock.Now().Add(s.cfg.Copy.Deadline)
		}
		if e := s.save(ctx, j, l); e != nil {
			return e
		}
		if e := s.hit("poll.terminal"); e != nil {
			return e
		}
		return s.copyResult(ctx, j, l)
	}
	now := s.clock.Now()
	j.CompletedAt = &now
	j.Status = string(obs.Normalized)
	j.ReconcileState = video.ReconcileResolved
	if obs.Normalized == provider.StateCancelled {
		j.CancelState = "confirmed"
		j.CancelScope = "provider"
	}
	if obs.Error != nil {
		j.Error = &video.VideoError{Code: obs.Error.Code, Message: obs.Error.Message, Retryable: obs.Error.Retryable}
	}
	if e := s.save(ctx, j, l); e != nil {
		return e
	}
	return s.settle(ctx, j, l)
}
func safeProviderCode(code string) string {
	switch code {
	case "content_policy_violation", "moderation_blocked", "provider_timeout", "provider_access_denied", "submit_rejected":
		return code
	default:
		return "provider_failed"
	}
}
