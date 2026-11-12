package lifecycle

import (
	"context"
	"errors"
	"time"

	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/video"
)

func (s *Service) reconcile(ctx context.Context, j *video.VideoJob, l video.Lease) error {
	if j.ReconcileState == video.ReconcileUnresolved {
		return s.saveLater(ctx, j, l, time.Hour)
	}
	if j.ProviderJobID != "" && !j.IsTerminal() {
		return s.poll(ctx, j, l)
	}
	if !j.ReconcileBy.IsZero() && !s.clock.Now().Before(j.ReconcileBy) {
		return s.unresolved(ctx, j, l)
	}
	if j.ProviderJobID != "" {
		return s.poll(ctx, j, l)
	}
	a, ok := s.adapterFor(j)
	if !ok {
		return s.saveLater(ctx, j, l, time.Minute)
	}
	caps := a.Capabilities()
	view, err := s.view(ctx, j, false)
	if err != nil {
		return err
	}
	correlation := provider.Correlation{Token: j.CorrelationToken, Data: j.CorrelationData}
	var result provider.ReconcileResult
	err = s.withPermit(ctx, j, "reconcile", func(ctx context.Context) error {
		ctx, cancel := context.WithTimeout(ctx, s.cfg.Providers[j.Service].Deadlines.Reconcile)
		defer cancel()
		if caps.CorrelationLookup == provider.LookupNone && caps.SubmitIdempotency == provider.IdempotencyToken && j.DeletedAt == nil {
			// The adapter explicitly guarantees that this exact token cannot create a
			// second task. This path is never used for BytePlus or heuristic lookup.
			allowed, _, e := s.rate(ctx, j, "submit")
			if e != nil {
				return e
			}
			if !allowed {
				return errLimited
			}
			view, e = s.view(ctx, j, true)
			if e != nil {
				return e
			}
			r, e := a.Submit(ctx, view, correlation)
			if e == nil && r.ProviderJobID != "" {
				result = provider.ReconcileResult{Found: true, ProviderJobID: r.ProviderJobID}
			}
			return e
		}
		var e error
		result, e = a.Reconcile(ctx, view, correlation)
		return e
	})
	if errors.Is(err, ErrCrash) {
		return err
	}
	if err == nil && result.Found && result.ProviderJobID != "" && !result.ProvenAbsent {
		j.ProviderJobID = result.ProviderJobID
		j.Phase = video.PhaseReceived
		j.ReconcileState = video.ReconcileResolved
		if !j.IsTerminal() {
			j.Status = video.StatusQueued
		}
		if j.RunBy.IsZero() {
			now := s.clock.Now()
			j.SubmittedAt = &now
			j.RunBy = now.Add(s.cfg.Providers[j.Service].Deadlines.Run)
		}
		return s.saveLater(ctx, j, l, s.baseInterval(j))
	}
	if err == nil && result.ProvenAbsent && !result.Found && s.cfg.Reconcile.AllowAbsentResubmit && caps.CorrelationLookup == provider.LookupByToken && j.DeletedAt == nil && j.Status == video.StatusSubmissionUnknown && s.clock.Now().Before(j.SubmitBy) {
		j.Status = video.StatusSubmitting
		j.Phase = video.PhasePrepared
		j.ResubmitAuthorized = true
		j.ReconcileState = video.ReconcileNone
		j.UpstreamMayContinue = false
		return s.saveLater(ctx, j, l, time.Second)
	}
	return s.saveLater(ctx, j, l, max(s.baseInterval(j), 5*time.Second))
}
func (s *Service) unresolved(ctx context.Context, j *video.VideoJob, l video.Lease) error {
	j.ReconcileState = video.ReconcileUnresolved
	j.SettlementState = video.SettlementUnsettled
	j.UpstreamMayContinue = true
	j.RetrySafe = false
	j.NextPollAt = s.clock.Now().Add(time.Hour)
	return s.fail(ctx, j, l, "submission_unresolved", false)
}

// Attach implements N7. A terminal failure is never reopened; observations after
// operator attachment can only settle accounting and record late_success_at.
func (s *Service) Attach(ctx context.Context, id, providerID string) (*video.VideoJob, error) {
	if providerID == "" || len(providerID) > 255 {
		return nil, api(400, "invalid_provider_job_id")
	}
	for _, c := range providerID {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_' || c == '.' || c == ':') {
			return nil, api(400, "invalid_provider_job_id")
		}
	}
	j, err := s.store.GetAccounting(ctx, id)
	if err != nil {
		return nil, err
	}
	l, err := s.store.Lease(ctx, id, video.NewID(), s.cfg.Submit.LeaseTTL)
	if err != nil {
		return nil, err
	}
	defer s.store.Release(ctx, l)
	j, err = s.store.GetAccounting(ctx, id)
	if err != nil {
		return nil, err
	}
	if j.ReconcileState != video.ReconcilePending && j.ReconcileState != video.ReconcileUnresolved {
		return nil, api(409, "attach_not_available")
	}
	if j.ProviderJobID != "" && j.ProviderJobID != providerID {
		return nil, api(409, "attach_conflict")
	}
	j.ProviderJobID = providerID
	j.Phase = video.PhaseReceived
	j.ReconcileState = video.ReconcilePending
	j.ReconcileReason = "operator_attached"
	j.ReconcileBy = s.clock.Now().Add(s.cfg.Poll.TimeoutReconcileWindow)
	j.NextPollAt = s.clock.Now()
	if j.Status == video.StatusSubmissionUnknown {
		j.Status = video.StatusQueued
		j.ReconcileState = video.ReconcileResolved
	}
	if j.RunBy.IsZero() {
		j.RunBy = s.clock.Now().Add(s.cfg.Providers[j.Service].Deadlines.Run)
	}
	if err = s.save(ctx, j, l); err != nil {
		return nil, err
	}
	return j, nil
}
func (s *Service) Unsettled(ctx context.Context) ([]*video.VideoJob, error) {
	jobs, e := s.store.ListAccounting(ctx)
	if e != nil {
		return nil, e
	}
	out := []*video.VideoJob{}
	for _, j := range jobs {
		if j.SettlementState == video.SettlementReserved || j.SettlementState == video.SettlementUnsettled || j.ReconcileState == video.ReconcilePending || j.ReconcileState == video.ReconcileUnresolved {
			out = append(out, j)
		}
	}
	return out, nil
}
