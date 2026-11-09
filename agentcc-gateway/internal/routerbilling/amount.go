// Package routerbilling implements the accounting core for ruling 601. It does
// not enable catalogue offers. Admission must remain closed until the durable
// ownership/evidence adapter and BL-LEDGER projection are installed together.
package routerbilling

import (
	"errors"
	"math/big"
	"regexp"
	"strings"
)

var ErrAmount = errors.New("router billing: invalid or overflowing decimal amount")

// Bound input length and exponent before constructing a big.Rat. Monetary input
// is a JSON decimal number, never a fraction, quoted number, NaN, or infinity.
var decimalPattern = regexp.MustCompile(`^(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]{1,2})?$`)

func decimal(value string) (*big.Rat, error) {
	value = strings.TrimSpace(value)
	if len(value) == 0 || len(value) > 128 || !decimalPattern.MatchString(value) {
		return nil, ErrAmount
	}
	r, ok := new(big.Rat).SetString(value)
	if !ok || r.Sign() < 0 {
		return nil, ErrAmount
	}
	return r, nil
}

// rounded is nonnegative round-half-up with checked integer range.
func rounded(r *big.Rat) (int64, error) {
	n := new(big.Int).Mul(r.Num(), big.NewInt(2))
	n.Add(n, r.Denom())
	n.Quo(n, new(big.Int).Mul(r.Denom(), big.NewInt(2)))
	if n.Sign() < 0 || !n.IsInt64() {
		return 0, ErrAmount
	}
	return n.Int64(), nil
}

// Charge rounds upstream USD to whole micros, then rounds the one 5% fee.
// In particular 0.0000095 -> upstream 10 -> billed 11, not 10. No component
// prices are added to an inclusive upstream total.
func Charge(usd string) (upstream, billed int64, err error) {
	r, err := decimal(usd)
	if err != nil {
		return 0, 0, err
	}
	return chargeRat(r)
}

func chargeRat(usd *big.Rat) (upstream, billed int64, err error) {
	upstream, err = rounded(new(big.Rat).Mul(usd, big.NewRat(1_000_000, 1)))
	if err != nil {
		return 0, 0, err
	}
	billed, err = rounded(new(big.Rat).Mul(big.NewRat(upstream, 1), big.NewRat(105, 100)))
	return upstream, billed, err
}
