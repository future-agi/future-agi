package cost

import (
	"errors"
	"math/big"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// PriceOwnedAttempt is the shared journal and cost-plugin pricing path for owned token hops.
// Preserve the provider's wire decimal through upstream half-up rounding, then
// apply markup to integer micros. Never snap to a fraction of a micro: a total
// just below 9.5 upstream micros is 9, not 10. Card-only estimates use the same
// exact arithmetic on decimal rates, without float multiplication or summation.
func PriceOwnedAttempt(a *models.Attempt, u *models.Usage, p models.PriceCard, markupBps, upstreamFeeBps int) (upstreamMicros, billedMicros, landedMicros int64, source string, ok bool) {
	var err error
	if u.HasReportedCost() || (u != nil && ownedNumericCost(u.CostDecimal)) {
		upstreamMicros, err = reportedUpstreamMicros(u)
		source = string(models.CostSourceProviderReported)
	} else {
		lines, priced := tokenCharges(tokensOf(a, u), p)
		if !priced {
			return 0, 0, 0, string(models.CostSourceUnpriced), false
		}
		total := new(big.Rat)
		for _, line := range lines {
			rate, valid := new(big.Rat).SetString(strconv.FormatFloat(line.rate, 'g', -1, 64))
			if !valid || rate.Sign() < 0 || line.count < 0 {
				return 0, 0, 0, string(models.CostSourceUnpriced), false
			}
			total.Add(total, rate.Mul(rate, new(big.Rat).SetInt64(int64(line.count))))
		}
		upstreamMicros, _, err = inclusive.ChargeRational(total)
		source = string(costSourceOf(p.Source))
	}
	if err != nil {
		return 0, 0, 0, string(models.CostSourceUnpriced), false
	}
	managed, processing := false, 0
	if a != nil {
		managed, processing = a.CredentialType == models.CredentialManaged, a.ProcessingFeeBps
	}
	billedMicros, landedMicros = billedAndLanded(upstreamMicros, managed, markupBps, upstreamFeeBps, processing)
	return upstreamMicros, billedMicros, landedMicros, source, true
}

// reportedUpstreamMicros never substitutes a float for a preserved wire
// decimal. The exact parser accepts JSON exponent and mantissa spellings;
// unsupported/invalid reports remain unresolved, never rounded into a charge.
func reportedUpstreamMicros(u *models.Usage) (int64, error) {
	if u.CostDecimal != "" {
		upstream, _, err := inclusive.Charge(u.CostDecimal)
		return upstream, err
	}
	shortest, ok := new(big.Rat).SetString(strconv.FormatFloat(*u.Cost, 'g', -1, 64))
	if !ok {
		return 0, errors.New("reported cost is not a decimal")
	}
	upstream, _, err := inclusive.ChargeRational(shortest)
	return upstream, err
}
