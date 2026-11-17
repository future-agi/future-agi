package inclusive

import (
	"errors"
	"math/big"
	"regexp"
	"strconv"
	"strings"
)

// A wire decimal is a JSON number of at most 1 MiB, the adapter's SSE line and
// error-body limit. It never round-trips through float64: lex reads it once as
// significant digits times a power of ten, and two consumers share that form.
//
//   - Charge prices EVERY lexeme the grammar and the bound admit. It rounds
//     digits x 10^exp to whole micros from the digits themselves, so no 10^n is
//     ever built. A value below half a micro is exactly 0 micros, and only a
//     value that rounds above 2^60 micros is outside the money range.
//   - amount is the exact big.Rat that admission bounds and refund checks
//     compare. Its domain is what math/big's Rat.SetString represents: a
//     normalised power of ten within +-1e6 (go1.26 ratconv.go refuses larger
//     exponents). A smaller power is refused as outside the exact range and a
//     larger one as outside the money range. It never returns zero for them.
var decimal = regexp.MustCompile(`^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?$`)

const (
	maxDecimalBytes = 1024 * 1024
	maxRatExponent  = 1000000
	// A written exponent of at least exponentCap is saturated. With at most
	// 2^20 digits, every such value is far below a micro or far above 2^60
	// micros, so saturation keeps both outcomes and leaves int64 headroom.
	exponentCap = 1 << 40
)

var (
	errDecimal  = errors.New("invalid decimal cost")
	errNegative = errors.New("negative decimal cost")
	errRange    = errors.New("cost exceeds supported money range")
	errExact    = errors.New("decimal exponent below exact range")
)

// wire is digits x 10^exp. digits has no leading or trailing zeros, and is
// empty exactly when the value is zero.
type wire struct {
	digits string
	exp    int64
}

func lex(s string) (wire, error) {
	if len(s) > maxDecimalBytes || !decimal.MatchString(s) {
		return wire{}, errDecimal
	}
	negative := s[0] == '-'
	s = strings.TrimPrefix(s, "-")
	mantissa, written := s, ""
	if i := strings.IndexAny(s, "eE"); i >= 0 {
		mantissa, written = s[:i], s[i+1:]
	}
	whole, fraction, _ := strings.Cut(mantissa, ".")
	digits := strings.TrimLeft(whole+fraction, "0")
	if digits == "" {
		return wire{}, nil // also negative zero, with any exponent
	}
	if negative {
		return wire{}, errNegative
	}
	trimmed := strings.TrimRight(digits, "0")
	exp := int64(len(digits)-len(trimmed)) - int64(len(fraction))
	minus := strings.HasPrefix(written, "-")
	if magnitude := strings.TrimLeft(strings.TrimLeft(written, "+-"), "0"); magnitude != "" {
		n, err := strconv.ParseInt(magnitude, 10, 64)
		if err != nil || n > exponentCap {
			n = exponentCap
		}
		if minus {
			n = -n
		}
		exp += n
	}
	return wire{digits: trimmed, exp: exp}, nil
}

// micros rounds digits x 10^(exp+6) half-up, the rule round() applies to a
// rational. The discarded tail is at least one half exactly when its first
// digit is 5 or more, so the digits alone decide.
func (w wire) micros() (int64, error) {
	if w.digits == "" {
		return 0, nil
	}
	n, shift := int64(len(w.digits)), w.exp+6
	whole, up := w.digits, false
	switch {
	case shift >= 0:
		if n+shift > 19 {
			return 0, errRange
		}
		whole += strings.Repeat("0", int(shift))
	case n+shift < 0:
		return 0, nil // below a tenth of a micro
	default:
		cut := n + shift
		if cut > 19 {
			return 0, errRange
		}
		whole, up = w.digits[:cut], w.digits[cut] >= '5'
	}
	var q uint64
	if whole != "" {
		var err error
		if q, err = strconv.ParseUint(whole, 10, 64); err != nil {
			return 0, errRange
		}
	}
	if up {
		q++
	}
	if q > 1<<60 {
		return 0, errRange
	}
	return int64(q), nil
}

func amount(s string) (*big.Rat, error) {
	w, err := lex(s)
	if err != nil {
		return nil, err
	}
	if w.digits == "" {
		return new(big.Rat), nil
	}
	if w.exp > maxRatExponent {
		return nil, errRange
	}
	if w.exp < -maxRatExponent {
		return nil, errExact
	}
	r, ok := new(big.Rat).SetString(w.digits + "e" + strconv.FormatInt(w.exp, 10))
	if !ok {
		return nil, errDecimal
	}
	return r, nil
}
