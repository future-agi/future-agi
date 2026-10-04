package lifecycle

import (
	"context"
	"errors"
	"net"
	"strings"
	"time"

	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/video"
)

func stringsSafe(code string) string { return strings.ReplaceAll(code, "_", " ") }
func (s *Service) submit(ctx context.Context, j *video.VideoJob, l video.Lease) error {
	if j.Phase == video.PhaseCalling {
		return s.unknown(ctx, j, l, "worker_lost_receipt")
	}
	if !s.clock.Now().Before(j.SubmitBy) {
		code := "submit_deadline_exceeded"
		if j.ReconcileReason == "provider_disabled" {
			code = "provider_disabled"
		}
		return s.fail(ctx, j, l, code, true)
	}
	killed, err := s.Killed(ctx)
	if err != nil {
		return err
	}
	p := s.cfg.Providers[j.Service]
	if killed || !p.Enabled {
		j.ReconcileReason = "provider_disabled"
		return s.saveLater(ctx, j, l, time.Second)
	}
	ok, wait, err := s.rate(ctx, j, "submit")
	if err != nil {
		return err
	}
	if !ok {
		return s.saveLater(ctx, j, l, wait)
	}
	a, ok := s.adapterFor(j)
	if !ok {
		return s.saveLater(ctx, j, l, time.Second)
	}
	err = s.withPermit(ctx, j, "submit", func(ctx context.Context) error {
		if err := s.hit("submit.prepared"); err != nil {
			return err
		}
		view, err := s.view(ctx, j, true)
		if err != nil {
			return s.fail(ctx, j, l, "media_unavailable", true)
		}
		j.AttemptID = video.NewID()
		j.AttemptStartedAt = s.clock.Now()
		view.AttemptID = j.AttemptID
		view.AttemptStartedAt = j.AttemptStartedAt
		correlation, err := a.Prepare(ctx, view)
		if err != nil {
			return s.fail(ctx, j, l, "submit_rejected", true)
		}
		j.Phase = video.PhaseCalling
		j.ResubmitAuthorized = false
		j.CorrelationToken = correlation.Token
		j.CorrelationData = correlation.Data
		if err = s.save(ctx, j, l); err != nil {
			return err
		}
		if err = s.hit("submit.call"); err != nil {
			return err
		}
		callCtx, cancel := context.WithTimeout(ctx, min(p.Deadlines.Submit, j.SubmitBy.Sub(s.clock.Now())))
		defer cancel()
		finishSpan := s.span("video.provider.submit", j)
		receipt, err := a.Submit(callCtx, view, correlation)
		finishSpan()
		if hookErr := s.hit("submit.sent"); hookErr != nil {
			return hookErr
		}
		if err != nil {
			return s.submitError(ctx, j, l, err)
		}
		if err = s.hit("submit.received"); err != nil {
			return err
		}
		if receipt.ProviderJobID == "" || !receipt.Normalized.Valid() {
			return s.unknown(ctx, j, l, "adapter_schema_error")
		}
		j.ProviderJobID = receipt.ProviderJobID
		j.ProviderRequestID = receipt.ProviderRequestID
		j.ProviderState = receipt.ProviderState
		j.Phase = video.PhaseReceived
		j.Status = video.StatusQueued
		if receipt.Normalized == provider.StateRunning {
			j.Status = video.StatusRunning
		}
		now := s.clock.Now()
		j.SubmittedAt = &now
		j.RunBy = now.Add(p.Deadlines.Run)
		j.NextPollAt = now.Add(s.baseInterval(j))
		j.UpstreamMayContinue = true
		j.ReconcileState = video.ReconcileNone
		if err = s.save(ctx, j, l); err != nil {
			return err
		}
		return s.hit("submit.saved")
	})
	if errors.Is(err, errLimited) {
		return s.saveLater(ctx, j, l, time.Second)
	}
	return err
}
func (s *Service) submitError(ctx context.Context, j *video.VideoJob, l video.Lease, err error) error {
	var up *provider.UpstreamError
	if errors.As(err, &up) && up.Status >= 400 && up.Status < 500 && up.Status != 408 && up.Status != 429 {
		code := "submit_rejected"
		if up.Status == 401 || up.Status == 403 {
			code = "provider_access_denied"
		}
		return s.fail(ctx, j, l, code, true)
	}
	// Only a failed dial proves that no body was sent. A 429, 5xx, timeout or EOF
	// can only be observed after the provider read the request, and Seedance has
	// no idempotency token, so those stay submission_unknown for reconciliation
	// instead of authorizing a second paid submit.
	var op *net.OpError
	if errors.As(err, &op) && op.Op == "dial" {
		j.Status = video.StatusSubmitting
		j.Phase = video.PhasePrepared
		j.ResubmitAuthorized = true
		j.ReconcileState = video.ReconcileNone
		j.UpstreamMayContinue = false
		return s.saveLater(ctx, j, l, time.Second)
	}
	return s.unknown(ctx, j, l, "submit_outcome_unknown")
}
func (s *Service) unknown(ctx context.Context, j *video.VideoJob, l video.Lease, reason string) error {
	j.Status = video.StatusSubmissionUnknown
	j.ReconcileState = video.ReconcilePending
	j.ReconcileReason = reason
	j.UpstreamMayContinue = true
	j.RetrySafe = false
	if j.ReconcileBy.IsZero() {
		j.ReconcileBy = s.clock.Now().Add(s.cfg.Submit.UnknownReconcileDeadline)
	}
	return s.saveLater(ctx, j, l, time.Second)
}
func (s *Service) baseInterval(j *video.VideoJob) time.Duration {
	d := s.cfg.Providers[j.Service].Poll.BaseInterval
	if a, ok := s.adapterFor(j); ok {
		d = max(d, a.Capabilities().PollMinInterval)
	}
	return max(d, time.Second)
}
