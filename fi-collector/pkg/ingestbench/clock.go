package ingestbench

import (
	"context"
	"math"
	"time"
)

// The durable lag compares ClickHouse's own insert timestamp (created_at) with
// the generator's schedule, so the two clocks must be compared. The offset is
// measured NTP-style over the read connection: for each probe, server time minus
// the midpoint of the request, keeping the probe with the shortest round trip;
// the uncertainty is half that round trip (it includes HTTP and query overhead,
// so it is conservative).

const clockProbes = 5

const clockMethod = "server now64(6) over the verification connection minus the generator's mid-request time; best of 5 probes by round trip; uncertainty = half the round trip"

// clockLimit is the offset (or uncertainty) above which durable lags are
// flagged as clock-limited.
const clockLimit = 10 * time.Millisecond

type ClockSample struct {
	Measured      bool    `json:"measured"`
	OffsetMS      float64 `json:"server_minus_generator_ms"`
	RTTMS         float64 `json:"best_round_trip_ms"`
	UncertaintyMS float64 `json:"uncertainty_ms"`
	Probes        int     `json:"probes"`
	Error         string  `json:"error,omitempty"`
}

type ClockResult struct {
	Method string      `json:"method"`
	Start  ClockSample `json:"before_load"`
	End    ClockSample `json:"after_settle"`
	// Limited: the offset or its uncertainty exceeds 10 ms, so a durable lag
	// below that is not resolved.
	Limited bool `json:"offset_or_uncertainty_exceeds_10ms"`
}

func measureClock(ctx context.Context, ch clickHouse) ClockSample {
	best := ClockSample{}
	bestRTT := time.Duration(math.MaxInt64)
	for i := 0; i < clockProbes && ctx.Err() == nil; i++ {
		before := time.Now()
		body, err := ch.query(ctx, "SELECT toUnixTimestamp64Micro(now64(6)) AS t", nil)
		after := time.Now()
		if err != nil {
			best.Error = err.Error()
			continue
		}
		var row struct {
			T *int64 `json:"t"`
		}
		if decodeOne(body, &row) != nil || row.T == nil {
			best.Error = "invalid server time response"
			continue
		}
		rtt := after.Sub(before)
		best.Probes++
		if rtt < bestRTT {
			bestRTT = rtt
			mid := before.Add(rtt / 2)
			server := time.UnixMicro(*row.T)
			best.OffsetMS = float64(server.Sub(mid)) / float64(time.Millisecond)
			best.RTTMS = float64(rtt) / float64(time.Millisecond)
			best.UncertaintyMS = best.RTTMS / 2
			best.Measured = true
		}
	}
	if best.Measured {
		best.Error = ""
	} else if best.Error == "" {
		best.Error = "no clock probe completed"
	}
	return best
}

func (c *ClockResult) conclude() {
	limit := float64(clockLimit) / float64(time.Millisecond)
	c.Limited = !c.Start.Measured || math.Abs(c.Start.OffsetMS) > limit || c.Start.UncertaintyMS > limit ||
		(c.End.Measured && (math.Abs(c.End.OffsetMS) > limit || c.End.UncertaintyMS > limit))
}
