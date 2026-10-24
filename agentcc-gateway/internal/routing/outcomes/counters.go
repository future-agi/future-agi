package outcomes

import (
	"math"
	"strconv"
	"strings"
	"time"
)

// Counters is one outcomes hash: decayed counts of the samples recorded, as
// of TsMs (Unix milliseconds). N counts every recorded response; Errors,
// Empty and ContentFilter the ones that failed, came back empty or were
// filtered; VerifyFailed the generations a caller judged failed. Latency
// holds the answered responses' latencies in log-spaced bins.
type Counters struct {
	N             float64
	Errors        float64
	Empty         float64
	ContentFilter float64
	VerifyFailed  float64
	Latency       [LatencyBins]float64
	TsMs          int64
}

// The hash's field names; readers look verify_failed up by name.
const (
	FieldN             = "n"
	FieldErrors        = "errors"
	FieldEmpty         = "empty"
	FieldContentFilter = "content_filter"
	FieldVerifyFailed  = "verify_failed"
	FieldTsMs          = "ts_ms"
	latencyFieldPrefix = "lat:"
)

// Latency bins grow by latencyBinGrowth from 1 ms: bin 0 holds answers
// under 1 ms, bin k answers in [growth^(k-1), growth^k) ms, and the last bin
// everything from about 15 minutes up.
const (
	LatencyBins      = 36
	latencyBinGrowth = 1.5
)

var logLatencyBinGrowth = math.Log(latencyBinGrowth)

// LatencyField is the hash field of latency bin i.
func LatencyField(i int) string { return latencyFieldPrefix + strconv.Itoa(i) }

// LatencyBin is the bin a latency of ms falls in.
func LatencyBin(ms int64) int {
	if ms < 1 {
		return 0
	}
	return min(1+int(math.Floor(math.Log(float64(ms))/logLatencyBinGrowth)), LatencyBins-1)
}

// latencyBinMid is the geometric middle of bin i, in ms.
func latencyBinMid(i int) float64 {
	if i == 0 {
		return 0.5
	}
	return math.Pow(latencyBinGrowth, float64(i)-0.5)
}

// Delta is what one record adds, as field increments.
type Delta map[string]float64

// delta is what o adds to its counters hash.
func (o Outcome) delta() Delta {
	d := Delta{FieldN: 1}
	if o.ErrorClass != "" {
		d[FieldErrors] = 1
		return d
	}
	if o.Empty {
		d[FieldEmpty] = 1
	}
	if o.ContentFilter {
		d[FieldContentFilter] = 1
	}
	if o.LatencyMs > 0 {
		d[LatencyField(LatencyBin(o.LatencyMs))] = 1
	}
	return d
}

// DecayFactor is how much a count recorded at tsMs weighs at nowMs, with
// half-life halfLifeMs: 0.5^((now-ts)/halfLife). A count from the future, or
// one with no timestamp, is not decayed.
func DecayFactor(tsMs, nowMs, halfLifeMs int64) float64 {
	if tsMs <= 0 || nowMs <= tsMs || halfLifeMs <= 0 {
		return 1
	}
	return math.Pow(0.5, float64(nowMs-tsMs)/float64(halfLifeMs))
}

// DecayedTo is c as of nowMs.
func (c Counters) DecayedTo(nowMs, halfLifeMs int64) Counters {
	f := DecayFactor(c.TsMs, nowMs, halfLifeMs)
	if f == 1 {
		return c
	}
	c.N *= f
	c.Errors *= f
	c.Empty *= f
	c.ContentFilter *= f
	c.VerifyFailed *= f
	for i := range c.Latency {
		c.Latency[i] *= f
	}
	c.TsMs = nowMs
	return c
}

// Add is c decayed to nowMs plus d: what recording d at nowMs leaves.
func (c Counters) Add(d Delta, nowMs, halfLifeMs int64) Counters {
	c = c.DecayedTo(nowMs, halfLifeMs)
	for field, v := range d {
		c.set(field, c.get(field)+v)
	}
	c.TsMs = max(c.TsMs, nowMs)
	return c
}

// Plus sums two hashes, each decayed to nowMs.
func (c Counters) Plus(o Counters, nowMs, halfLifeMs int64) Counters {
	c, o = c.DecayedTo(nowMs, halfLifeMs), o.DecayedTo(nowMs, halfLifeMs)
	c.N += o.N
	c.Errors += o.Errors
	c.Empty += o.Empty
	c.ContentFilter += o.ContentFilter
	c.VerifyFailed += o.VerifyFailed
	for i := range c.Latency {
		c.Latency[i] += o.Latency[i]
	}
	c.TsMs = max(c.TsMs, o.TsMs)
	return c
}

// SetField sets a counter from its hash field; it reports false for a field
// that is not one.
func (c *Counters) SetField(field string, v float64) bool {
	if field == FieldTsMs {
		c.TsMs = int64(v)
		return true
	}
	return c.set(field, v)
}

func (c *Counters) set(field string, v float64) bool {
	if p := c.ptr(field); p != nil {
		*p = v
		return true
	}
	return false
}

func (c *Counters) get(field string) float64 {
	if p := c.ptr(field); p != nil {
		return *p
	}
	return 0
}

func (c *Counters) ptr(field string) *float64 {
	switch field {
	case FieldN:
		return &c.N
	case FieldErrors:
		return &c.Errors
	case FieldEmpty:
		return &c.Empty
	case FieldContentFilter:
		return &c.ContentFilter
	case FieldVerifyFailed:
		return &c.VerifyFailed
	}
	if s, ok := strings.CutPrefix(field, latencyFieldPrefix); ok {
		if i, err := strconv.Atoi(s); err == nil && i >= 0 && i < LatencyBins {
			return &c.Latency[i]
		}
	}
	return nil
}

// Stats is a model's outcome record for one agent and step: the decayed
// number of responses, the share that failed, came back empty, were
// filtered or were judged failed by the caller, and the median latency of
// the answered ones (0 when none). A rate is 0 when N is.
type Stats struct {
	N                float64
	ErrRate          float64
	EmptyRate        float64
	FilterRate       float64
	VerifyFailedRate float64
	P50Ms            float64
}

// Stats is c's record.
func (c Counters) Stats() Stats {
	s := Stats{N: c.N, P50Ms: c.p50()}
	if c.N <= 0 {
		return s
	}
	rate := func(v float64) float64 { return math.Min(v/c.N, 1) }
	s.ErrRate, s.EmptyRate, s.FilterRate, s.VerifyFailedRate = rate(c.Errors), rate(c.Empty), rate(c.ContentFilter), rate(c.VerifyFailed)
	return s
}

func (c Counters) p50() float64 {
	total := 0.0
	for _, v := range c.Latency {
		total += v
	}
	if total <= 0 {
		return 0
	}
	seen := 0.0
	for i, v := range c.Latency {
		seen += v
		if seen >= total/2 {
			return latencyBinMid(i)
		}
	}
	return latencyBinMid(LatencyBins - 1)
}

// msOf is d in milliseconds.
func msOf(d time.Duration) int64 { return d.Milliseconds() }
