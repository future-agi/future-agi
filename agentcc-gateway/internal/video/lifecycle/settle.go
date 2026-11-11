package lifecycle

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
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

// maxUsagePolls bounds accounting-only reads of a terminal job (one per five
// minutes, about an hour) while its charge is still unknown.
const maxUsagePolls = 12

// maxUsageRefreshAge also ends accounting reads, including rate-limited ones
// that do not count against maxUsagePolls, a day after the job ended.
const maxUsageRefreshAge = 24 * time.Hour

// ReceiptTTL is the least lifetime of a budget operation receipt (reserve,
// refund, settle): job metadata retention plus the window in which a terminal
// job's charge may still settle. Replays of one operation (lost replies, lease
// takeover, orphan recovery) happen well inside it.
func ReceiptTTL(cfg config.VideoConfig) time.Duration {
	return cfg.Retention.JobMetadata + maxUsageRefreshAge
}

// refreshUsage re-reads a terminal job's provider status only to learn a
// charge that was unknown when the job ended (for example OpenRouter reporting
// usage.cost:null at completion). It never copies output, resubmits, or changes
// the job's lifecycle state; the reservation stays held until a reported
// charge settles it.
func (s *Service) refreshUsage(ctx context.Context, j *video.VideoJob, l video.Lease) error {
	a, ok := s.adapterFor(j)
	if !ok {
		return s.saveLater(ctx, j, l, 5*time.Minute)
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
		obs, e = a.Poll(ctx, ref(j))
		return e
	})
	if errors.Is(err, errLimited) {
		return s.saveLater(ctx, j, l, time.Second)
	}
	// A provider cooldown is honoured and does not use up an accounting read.
	var up *provider.UpstreamError
	if errors.As(err, &up) && up.Status == 429 {
		return s.saveLater(ctx, j, l, max(up.RetryAfter, 5*time.Minute))
	}
	j.UsagePolls++
	next := 5 * time.Minute
	if up != nil && up.RetryAfter > next {
		next = up.RetryAfter
	}
	if err == nil && obs.RetryAfter != nil && *obs.RetryAfter > next {
		next = *obs.RetryAfter
	}
	terminal := obs.Normalized == provider.StateCompleted || obs.Normalized == provider.StateFailed || obs.Normalized == provider.StateCancelled
	if err != nil || !terminal || obs.Usage == nil || len(obs.Usage.Lines) == 0 {
		return s.saveLater(ctx, j, l, next)
	}
	if j.Usage == nil {
		j.Usage = &video.VideoUsage{Model: j.Model}
	}
	j.Usage.Lines = append([]provider.UsageLine(nil), obs.Usage.Lines...)
	s.priceObservation(j, obs)
	if !j.BillingKnown {
		// Usage arrived but could not be priced; keep the provider's cooldown.
		j.SettlementState = video.SettlementUnsettled
		return s.saveLater(ctx, j, l, next)
	}
	if e := s.save(ctx, j, l); e != nil {
		return e
	}
	return s.settle(ctx, j, l)
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
		_, ok := s.budget.WithOperation("video:"+j.ID+":settle", j.CreatedAt).RecordSpend(scope.BudgetOrg(j.OrgID), scope.Level, scope.Key, scope.Period, j.Model, delta)
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
			for k, v := range j.BudgetIdentity {
				rc.Metadata[k] = v
			}
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
