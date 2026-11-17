package billing

import "math"

// SpendCapMicros is the shared admission-cap conversion for startup validation
// and runtime Redis limits. Zero is deliberately unset. Every other value must
// be finite and round to positive micros; the problem is a value class, never
// operator input. In particular, a positive value that rounds to zero is not a cap.
func SpendCapMicros(usd float64) (micros int64, problem string) {
	switch {
	case math.IsNaN(usd):
		return 0, "NaN"
	case math.IsInf(usd, 0):
		return 0, "infinite"
	case usd < 0:
		return 0, "negative"
	case usd == 0:
		return 0, ""
	}
	micros = USDToMicros(usd)
	if micros <= 0 {
		return 0, "sub-micro (rounds to zero micros)"
	}
	return micros, ""
}
