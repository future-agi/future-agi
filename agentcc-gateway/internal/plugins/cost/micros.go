package cost

import (
	"fmt"
	"math"
	"math/bits"
)

// Money is integer micros: 1 USD = 1,000,000 micros. Each
// attempt is rounded half up once; markup and fees are basis points applied
// to those integers, each rounded half up on its own.
const (
	microsPerUSD = 1_000_000
	bpsDenom     = 10_000
)

// usdToMicros rounds usd to whole micros, half up. The product is first
// snapped to a millionth of a micro, so float error (0.0000025 USD is
// 2.4999999999999996 micros in float64) cannot turn a half into less.
func usdToMicros(usd float64) int64 {
	if !(usd > 0) || math.IsInf(usd, 1) {
		return 0
	}
	m := math.Round(usd*microsPerUSD*1e6) / 1e6
	if m >= math.MaxInt64 {
		return math.MaxInt64
	}
	return int64(math.Floor(m + 0.5))
}

// scaleBps returns x × bps / 10,000, rounded half up, without overflowing
// on the way. A non-positive x or bps is 0.
func scaleBps(x int64, bps int) int64 {
	if x <= 0 || bps <= 0 {
		return 0
	}
	hi, lo := bits.Mul64(uint64(x), uint64(bps))
	lo, carry := bits.Add64(lo, bpsDenom/2, 0)
	hi += carry
	if hi >= bpsDenom {
		return math.MaxInt64
	}
	q, _ := bits.Div64(hi, lo, bpsDenom)
	if q > math.MaxInt64 {
		return math.MaxInt64
	}
	return int64(q)
}

// billedAndLanded applies the markup and the landed-cost fees to one
// attempt's upstream micros. A managed attempt is billed upstream ×
// (10,000 + markup) / 10,000 and lands upstream × (10,000 + upstream fee) /
// 10,000 plus billed × processing fee / 10,000. A BYOK attempt
// is billed at upstream, and lands nothing: the customer pays the upstream.
func billedAndLanded(upstream int64, managed bool, markupBps, upstreamFeeBps, processingFeeBps int) (billed, landed int64) {
	if !managed {
		return upstream, 0
	}
	billed = scaleBps(upstream, bpsDenom+max(markupBps, 0))
	landed = scaleBps(upstream, bpsDenom+max(upstreamFeeBps, 0)) + scaleBps(billed, processingFeeBps)
	return billed, landed
}

// markupBpsOf converts a markup percentage (managed.markup_percent) to basis
// points: 5 is 500.
func markupBpsOf(percent float64) int {
	if !(percent > 0) {
		return 0
	}
	return int(math.Round(percent * 100))
}

// formatMicros renders micros as USD with six decimals, exactly: 7350 is
// "0.007350".
func formatMicros(m int64) string {
	if m < 0 {
		return "-" + formatMicros(-m)
	}
	return fmt.Sprintf("%d.%06d", m/microsPerUSD, m%microsPerUSD)
}
