package spendguard

import (
	"context"
	"log/slog"
	"time"

	"github.com/oklog/ulid/v2"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// EventKindAnomaly is the alerting event the spend guard raises when an
// org's managed spend jumps.
const EventKindAnomaly = "managed_spend.anomaly"

// Why an anomaly was raised.
const (
	// AnomalySpike: the last 5 minutes' spend is over 10 x the trailing
	// hour's average per 5 minutes and over $1.
	AnomalySpike = "spike"
	// AnomalyHourlyThreshold: the last hour's spend is over the org's
	// alert_usd_per_hour.
	AnomalyHourlyThreshold = "hourly_threshold"
)

const (
	spikeFactor      = 10
	spikeFloorMicros = 1_000_000 // $1
)

// AnomalyEvent is the payload of a managed_spend.anomaly event. It carries
// the org's spend windows, never content.
type AnomalyEvent struct {
	EventID string `json:"event_id"`
	OrgID   string `json:"org_id"`
	Reason  string `json:"reason"`
	// Spend5mUSD is the last 5 minutes' managed spend; TrailingAvg5mUSD the
	// average per 5 minutes over the hour before them; Spend1hUSD the last
	// hour's. Each counts requests in flight at their worst case.
	Spend5mUSD       float64 `json:"spend_5m_usd"`
	TrailingAvg5mUSD float64 `json:"trailing_avg_5m_usd"`
	Spend1hUSD       float64 `json:"spend_1h_usd"`
	// AlertUSDPerHour is the threshold in force, 0 when unset.
	AlertUSDPerHour float64 `json:"alert_usd_per_hour"`
	// AutoPaused is true when the org's managed requests were paused too.
	AutoPaused bool  `json:"auto_paused"`
	TsMs       int64 `json:"ts_ms"`
}

// Alerter delivers an anomaly event. It is called at most once per org per
// hour, on the request path, so it must not block.
type Alerter func(AnomalyEvent)

// anomalyReason is why sums are anomalous, or "" when they are not.
func anomalyReason(sums redisstate.SpendGuardSums, alertPerHourMicros int64) string {
	// Over 10 x the per-5-minute average of the trailing hour (12 periods).
	if sums.Last5MinMicros > spikeFloorMicros && 12*sums.Last5MinMicros > spikeFactor*sums.Prior60MinMicros {
		return AnomalySpike
	}
	if alertPerHourMicros > 0 && sums.LastHourMicros > alertPerHourMicros {
		return AnomalyHourlyThreshold
	}
	return ""
}

// checkAnomaly raises an anomaly alert for the org's spend windows after a
// settlement, at most once per org per hour across replicas, and with
// auto_pause pauses the org's managed requests in the same step.
func (g *Guard) checkAnomaly(ctx context.Context, orgID string, sums redisstate.SpendGuardSums, now time.Time) {
	s := g.settings(orgID)
	reason := anomalyReason(sums, s.alertPerHourMicros)
	if reason == "" {
		return
	}
	first, err := g.store.MarkAnomaly(ctx, orgID, s.autoPause)
	if err != nil {
		slog.Warn("managed spend guard: anomaly not recorded", "org_id", orgID, "reason", reason, "error", err)
		return
	}
	if !first {
		return
	}
	ev := AnomalyEvent{
		EventID:          ulid.Make().String(),
		OrgID:            orgID,
		Reason:           reason,
		Spend5mUSD:       billing.MicrosToUSD(sums.Last5MinMicros),
		TrailingAvg5mUSD: float64(sums.Prior60MinMicros) / 12 / 1e6,
		Spend1hUSD:       billing.MicrosToUSD(sums.LastHourMicros),
		AlertUSDPerHour:  billing.MicrosToUSD(s.alertPerHourMicros),
		AutoPaused:       s.autoPause,
		TsMs:             now.UnixMilli(),
	}
	slog.Warn("managed spend anomaly", "org_id", orgID, "reason", reason, "spend_5m_usd", ev.Spend5mUSD,
		"trailing_avg_5m_usd", ev.TrailingAvg5mUSD, "spend_1h_usd", ev.Spend1hUSD, "auto_paused", ev.AutoPaused)
	if g.alert != nil {
		g.alert(ev)
	}
}
