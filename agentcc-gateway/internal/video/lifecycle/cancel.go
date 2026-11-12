package lifecycle

import (
	"context"
	"errors"

	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/artifacts"
	"github.com/futureagi/agentcc-gateway/internal/video/capability"
	"strings"
)

func (s *Service) Get(ctx context.Context, id, org string) (*video.VideoJob, error) {
	j, e := s.store.GetForOrg(id, org)
	if e != nil {
		return nil, e
	}
	// A status read may close an expired, provably unsent job even when all
	// workers were unavailable. It never polls or submits to a provider.
	if j.Status == video.StatusSubmitting && j.Phase == video.PhasePrepared && !s.clock.Now().Before(j.SubmitBy) {
		l, e := s.store.Lease(ctx, id, video.NewID(), s.cfg.Submit.LeaseTTL)
		if e == nil {
			defer s.store.Release(ctx, l)
			fresh, e := s.store.GetForOrg(id, org)
			if e != nil {
				return nil, e
			}
			if fresh.Status == video.StatusSubmitting && fresh.Phase == video.PhasePrepared {
				if e = s.fail(ctx, fresh, l, "submit_deadline_exceeded", true); e != nil {
					return nil, e
				}
			}
			return s.store.GetForOrg(id, org)
		}
		if !errors.Is(e, video.ErrLeaseHeld) {
			return nil, e
		}
	}
	return j, nil
}
func (s *Service) List(org string, f video.VideoListFilters) ([]*video.VideoJob, int, error) {
	if f.Model != "" {
		service, id, ok := strings.Cut(f.Model, "/")
		if !ok {
			return nil, 0, api(400, "invalid_filter")
		}
		if _, e := capability.NewRegistry().Model(service, id); e != nil {
			return nil, 0, api(400, "invalid_filter")
		}
	}
	jobs, n, e := s.store.ListByOrg(org, f)
	if e != nil && !errors.Is(e, video.ErrStoreUnavailable) {
		return nil, 0, api(400, "invalid_filter")
	}
	return jobs, n, e
}
func (s *Service) Cancel(ctx context.Context, id, org string) (*video.VideoJob, error) {
	j, err := s.store.GetForOrg(id, org)
	if err != nil {
		return nil, err
	}
	if j.IsTerminal() {
		if j.Status != video.StatusCancelled {
			j.CancelState = "not_applicable"
		}
		return j, nil
	}
	l, err := s.store.Lease(ctx, id, video.NewID(), s.cfg.Submit.LeaseTTL)
	if err != nil {
		return nil, err
	}
	defer s.store.Release(ctx, l)
	j, err = s.store.GetForOrg(id, org)
	if err != nil {
		return nil, err
	}
	if j.IsTerminal() {
		j.CancelState = "not_applicable"
		return j, nil
	}
	now := s.clock.Now()
	j.CancelRequestedAt = &now
	if j.Status == video.StatusSubmitting && j.Phase == video.PhasePrepared {
		j.Status = video.StatusCancelled
		j.CancelState = "confirmed"
		j.CancelScope = "pre_submit"
		j.CompletedAt = &now
		j.BillingKnown = true
		j.BillingMicros = 0
		j.UpstreamMayContinue = false
		if err = s.save(ctx, j, l); err == nil {
			err = s.settle(ctx, j, l)
		}
		return j, err
	}
	a, ok := s.adapterFor(j)
	if !ok {
		return nil, api(409, "cancel_unsupported")
	}
	caps := a.Capabilities()
	if caps.CancelSupport == provider.CancelNone {
		return nil, api(409, "cancel_unsupported")
	}
	if j.ProviderJobID == "" || caps.CancelSupport == provider.CancelQueuedOnly && j.Status != video.StatusQueued {
		return nil, api(409, "cancel_not_available")
	}
	j.CancelState = "requested"
	if err = s.save(ctx, j, l); err != nil {
		return nil, err
	}
	var result provider.CancelResult
	err = s.withPermit(ctx, j, "cancel", func(ctx context.Context) error {
		ctx, cancel := context.WithTimeout(ctx, min(s.cfg.Submit.LeaseTTL/2, s.cfg.Providers[j.Service].Deadlines.Read))
		defer cancel()
		var e error
		result, e = a.Cancel(ctx, ref(j))
		return e
	})
	if errors.Is(err, provider.ErrCancelUnsupported) {
		return nil, api(409, "cancel_unsupported")
	}
	var upstream *provider.UpstreamError
	if errors.As(err, &upstream) && upstream.Code == "cancel_not_available" {
		j.CancelState = "not_available"
		_ = s.save(ctx, j, l)
		return nil, api(409, "cancel_not_available")
	}
	if err == nil && result.State == provider.CancelNotAvailable {
		j.CancelState = "not_available"
		if e := s.save(ctx, j, l); e != nil {
			return nil, e
		}
		return nil, api(409, "cancel_not_available")
	}
	if err == nil && result.State == provider.CancelConfirmed {
		j.Status = video.StatusCancelled
		j.CancelState = "confirmed"
		j.CancelScope = "provider"
		j.CompletedAt = &now
		j.UpstreamMayContinue = false
		if result.ReleasesCharge {
			j.BillingKnown = true
			j.BillingMicros = 0
		} else {
			j.SettlementState = video.SettlementUnsettled
		}
		if err = s.save(ctx, j, l); err == nil {
			err = s.settle(ctx, j, l)
		}
		return j, err
	}
	// A receipt or timeout is only a request. Polling establishes the outcome.
	if e := s.saveLater(ctx, j, l, s.baseInterval(j)); e != nil {
		return nil, e
	}
	return j, nil
}
func (s *Service) Delete(ctx context.Context, id, org string) (*video.VideoJob, error) {
	j, err := s.store.GetAccounting(ctx, id)
	if err != nil {
		return nil, err
	}
	if org == "" || j.OrgID != org {
		return nil, video.ErrVideoJobNotFound
	}
	if j.DeletedAt != nil {
		return j, nil
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
	// Unsent work is abandoned locally; no upstream cancel method is invoked.
	if j.Status == video.StatusSubmitting && j.Phase == video.PhasePrepared {
		j.Status = video.StatusCancelled
		j.CancelState = "confirmed"
		j.CancelScope = "pre_submit"
		j.BillingKnown = true
		j.BillingMicros = 0
		j.UpstreamMayContinue = false
		now := s.clock.Now()
		j.CompletedAt = &now
		if err = s.save(ctx, j, l); err != nil {
			return nil, err
		}
		if err = s.settle(ctx, j, l); err != nil {
			return nil, err
		}
	}
	if j.Status == video.StatusSubmitting && j.Phase == video.PhaseCalling {
		if err = s.unknown(ctx, j, l, "locally_deleted"); err != nil {
			return nil, err
		}
	}
	// Purge under the same lease as copy. A failed purge is retryable and does
	// not discard the keys needed to finish deletion.
	for _, a := range j.Artifacts {
		if a.BlobKey != "" {
			if err = s.blobs.Delete(ctx, a.BlobKey); err != nil {
				return nil, err
			}
		}
	}
	for _, a := range j.Ingress {
		if err = s.blobs.Delete(ctx, a.Key); err != nil {
			return nil, err
		}
	}
	if err = s.store.Tombstone(ctx, id, org, l); err != nil {
		return nil, err
	}
	return s.store.GetAccounting(ctx, id)
}
func (s *Service) Content(ctx context.Context, id, org string, index int, rangeHeader string) (artifacts.Object, error) {
	var empty artifacts.Object
	j, err := s.Get(ctx, id, org)
	if err != nil {
		return empty, err
	}
	if !j.IsTerminal() {
		return empty, api(409, "result_not_ready")
	}
	if index < 0 || index >= len(j.Artifacts) {
		return empty, api(404, "artifact_not_found")
	}
	a := j.Artifacts[index]
	if !s.clock.Now().Before(a.ExpiresAt) || a.State == video.ArtifactExpired {
		return empty, api(410, "output_expired")
	}
	if a.State != video.ArtifactAvailable {
		return empty, api(410, "output_unavailable")
	}
	obj, err := s.blobs.Open(ctx, a.BlobKey, rangeHeader)
	if errors.Is(err, artifacts.ErrRange) {
		return artifacts.Object{Meta: artifacts.Meta{Bytes: a.Bytes}}, err
	}
	if errors.Is(err, artifacts.ErrNotFound) {
		return empty, api(410, "output_unavailable")
	}
	return obj, err
}
