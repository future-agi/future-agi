package lifecycle

import (
	"context"
	"fmt"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	provider "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/video"
	"github.com/futureagi/agentcc-gateway/internal/video/tariff"
)

func (s *Service) priceObservation(j *video.VideoJob, obs provider.Observation) {
	if j.SettlementState == video.SettlementSettled || j.SettlementState == video.SettlementReleased {
		return
	}
	if obs.Normalized != provider.StateCompleted {
		a, ok := s.adapterFor(j)
		if ok && a.Capabilities().BillsOnFailure == provider.NotBilled {
			j.BillingKnown = true
			j.BillingMicros = 0
			return
		}
	}
	if obs.Usage == nil || len(obs.Usage.Lines) == 0 || j.RateUSDPerMillion <= 0 {
		j.SettlementState = video.SettlementUnsettled
		return
	}
	var micros int64
	for i, line := range obs.Usage.Lines {
		if line.Unit != j.EstimateUnit {
			j.SettlementState = video.SettlementUnsettled
			return
		}
		rate := tariff.Rate{Unit: line.Unit, Revision: j.TariffRevision, Currency: "USD", USDPerMillion: j.RateUSDPerMillion}
		n, e := rate.Price(line.Quantity)
		if e != nil || n > int64(^uint64(0)>>1)-micros {
			j.SettlementState = video.SettlementUnsettled
			return
		}
		micros += n
		if j.Usage != nil {
			j.Usage.Lines[i].USD = float64(n) / 1e6
			j.Usage.Lines[i].TariffRevision = j.TariffRevision
			j.Usage.Lines[i].Currency = "USD"
		}
	}
	j.BillingKnown = true
	j.BillingMicros = micros
}
func (s *Service) settle(ctx context.Context, j *video.VideoJob, l video.Lease) error {
	if j.SettlementState == video.SettlementSettled || j.SettlementState == video.SettlementReleased {
		return nil
	}
	if !j.BillingKnown {
		j.SettlementState = video.SettlementUnsettled
		return s.saveLater(ctx, j, l, 5*time.Minute)
	}
	defer s.span("video.settle", j)()
	if e := s.hit("settle.before"); e != nil {
		return e
	}
	// Check lease immediately before touching money. Atomic budget receipts also
	// fence paused workers: replaying the same operation cannot reapply its delta.
	if e := s.store.Renew(ctx, l, s.cfg.Submit.LeaseTTL); e != nil {
		return e
	}
	delta := float64(j.BillingMicros-j.ReservedMicros) / 1e6
	for _, scope := range j.Reservations {
		if s.budget == nil {
			return video.ErrStoreUnavailable
		}
		_, ok := s.budget.WithOperation("video:"+j.ID+":settle", j.CreatedAt).RecordSpend(j.OrgID, scope.Level, scope.Key, scope.Period, j.Model, delta)
		if !ok {
			return video.ErrStoreUnavailable
		}
		if e := s.hit("settle.recorded"); e != nil {
			return e
		}
	}
	j.SettledMicros = j.BillingMicros
	j.Cost = float64(j.SettledMicros) / 1e6
	// Post plugins are observers for BYOK video. A persisted claim gives them
	// at-most-once dispatch across process death; money is independently durable.
	if !j.PostPluginsDone {
		j.PostPluginsDone = true
		if e := s.save(ctx, j, l); e != nil {
			return e
		}
		if s.engine != nil {
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.EndpointType = "video"
			rc.Model = j.Model
			rc.ResolvedModel = j.ModelID
			rc.Provider = j.Service
			rc.TraceID = j.TraceID
			rc.RequestID = j.ID
			rc.Metadata["org_id"] = j.OrgID
			rc.Metadata["key_org_id"] = j.OrgID
			rc.Metadata["auth_key_id"] = j.KeyID
			rc.Metadata["key_type"] = j.KeyType
			rc.Metadata["video_id"] = j.ID
			rc.Metadata["video_settlement"] = "direct"
			rc.Metadata["video_cost_usd"] = fmt.Sprintf("%.6f", j.Cost)
			s.engine.RunPostPlugins(ctx, rc)
		}
	}
	j.SettlementState = video.SettlementSettled
	if j.SettledMicros == 0 {
		j.SettlementState = video.SettlementReleased
	}
	j.NextPollAt = time.Time{}
	if e := s.save(ctx, j, l); e != nil {
		return e
	}
	return s.hit("settle.saved")
}
