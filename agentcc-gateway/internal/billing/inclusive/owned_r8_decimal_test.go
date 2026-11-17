package inclusive

import (
	"encoding/json"
	"math/big"
	"math/rand"
	"strconv"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Round-seven review: every lexeme the grammar and the 1 MiB bound admit is a
// provider total that must price exactly. These sit on both sides of
// math/big's 1e6 exponent limit, which Rat.SetString enforces on the written
// exponent minus the fractional digit count.
func TestOwnedR8ExactDecimalBoundary(t *testing.T) {
	for _, tc := range []struct {
		name, raw  string
		up, billed int64
	}{
		{"exp-1000000", "1e-1000000", 0, 0},
		{"exp-1000001", "1e-1000001", 0, 0},
		{"exp-2097152", "1e-2097152", 0, 0},
		{"exp-2097153", "1e-2097153", 0, 0},
		{"exp-beyond-int64", "1e-999999999999999999999", 0, 0},
		{"fraction-shifted", "0.00000949999999999999999e-999990", 0, 0},
		{"long-mantissa-down", "0.0000094" + strings.Repeat("9", 1000000), 9, 9},
		{"long-mantissa-up", "0.0000095" + strings.Repeat("0", 1000000) + "1", 10, 11},
		{"long-zero-tail", "0.0000095" + strings.Repeat("0", 1000000), 10, 11},
		{"fraction-shifted-up", "0." + strings.Repeat("0", 1000003) + "95e999998", 10, 11},
		{"padded-exponent", "1e-" + strings.Repeat("0", 1000000) + "6", 1, 1},
		{"trailing-zero-integer", "1" + strings.Repeat("0", 1000000) + "e-1000006", 1, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if len(tc.raw) > 1024*1024 {
				t.Fatalf("fixture exceeds the 1 MiB bound: %d", len(tc.raw))
			}
			up, billed, err := Charge(tc.raw)
			if err != nil || up != tc.up || billed != tc.billed {
				t.Fatalf("boundary charge=(%d,%d) err=%v want=(%d,%d)", up, billed, err, tc.up, tc.billed)
			}
			var u models.Usage
			if err := json.Unmarshal([]byte(`{"cost":`+tc.raw+`}`), &u); err != nil {
				t.Fatal(err)
			}
			got := Price(&u, 84000)
			if got.Estimated || got.UpstreamMicros != tc.up || got.BilledMicros != tc.billed || got.ProviderTotal != tc.raw {
				t.Fatalf("boundary catalogue estimated=%t upstream=%d billed=%d", got.Estimated, got.UpstreamMicros, got.BilledMicros)
			}
		})
	}
	// Truly out of the money range, or not a nonnegative number: refused.
	for _, raw := range []string{
		"1e+999999999999999999999", "1e1000001", "1e19", "1" + strings.Repeat("0", 1000000),
		"1152921504606.846976", "-1e-1000001", "-0.0000095", strings.Repeat("1", 1024*1024+1),
	} {
		if _, _, err := Charge(raw); err == nil {
			t.Errorf("accepted out-of-range cost of %d bytes", len(raw))
		}
	}
	for _, raw := range []string{"-0", "-0.000e999999999999999999999", "0e-999999999999999999999", "0." + strings.Repeat("0", 1000000)} {
		if up, billed, err := Charge(raw); err != nil || up != 0 || billed != 0 {
			t.Errorf("zero spelling of %d bytes charge=(%d,%d) err=%v", len(raw), up, billed, err)
		}
	}
}

// amount is the exact rational admission bounds and refund checks compare. Its
// documented domain is what math/big represents: a normalised power of ten of
// magnitude at most 1e6. Outside it the value is refused, never zero.
func TestOwnedR8AmountExactDomain(t *testing.T) {
	for raw, want := range map[string]string{
		"1e-1000000":       "1e-1000000",
		"1000000e-1000006": "1e-1000000",
		"9.5e-999999":      "95e-1000000",
		"1e1000000":        "1e1000000",
	} {
		got, err := amount(raw)
		if err != nil {
			t.Fatalf("%s refused inside the exact domain: %v", raw, err)
		}
		exact, ok := ratFromDecimal(want)
		if !ok || got.Cmp(exact) != 0 {
			t.Fatalf("%s is not exactly %s", raw, want)
		}
	}
	for _, raw := range []string{"1e-1000001", "0.95e-999999", "1e-2097153", "1e-999999999999999999999", "1e1000001", "1e+999999999999999999999"} {
		if got, err := amount(raw); err == nil {
			t.Fatalf("%s outside the exact domain returned %s", raw, got.FloatString(6))
		}
	}
	for _, raw := range []string{"0", "-0", "0e-999999999999999999999", "-0.000e+999999999999999999999"} {
		if got, err := amount(raw); err != nil || got.Sign() != 0 {
			t.Fatalf("%s zero spelling err=%v", raw, err)
		}
	}
}

// Inside math/big's own domain the digit rounding must agree with the exact
// rational path it replaces, including each refusal, for every spelling.
func TestOwnedR8ChargeMatchesRational(t *testing.T) {
	cases := []string{
		"0", "-0", "0.0", "0e5", "-1", "-0.0000095", "0.0000095", "9.5e-6", "95e-7", "0.000009499999999999999", "0.0000005",
		"0.0000004999", "4.99999999e-7", "0.08", "1e999999", "1000000000000000", "1e-1000000", "1152921504606.846976",
		"1152921504606.8469765", "1152921504606.84697649999", "1098020480577.949501", "1098020480577.949502",
		"1098020480577.9495015", "1098020480577.9495014999", "0.0000094" + strings.Repeat("9", 1000),
		"949999999999999999e-" + strings.Repeat("0", 100) + "23", "1e-0000006", "5E+0", "5e-00", "123.456E-3",
	}
	rng := rand.New(rand.NewSource(8))
	for len(cases) < 40000 {
		cases = append(cases, randomDecimal(rng))
	}
	for _, raw := range cases {
		if !decimal.MatchString(raw) {
			t.Fatalf("generator produced a non-JSON number %q", raw)
		}
		r, ok := ratFromDecimal(raw)
		if !ok {
			continue // outside math/big's domain; covered by the boundary tests
		}
		wantUp, wantBilled, wantErr := int64(0), int64(0), false
		switch {
		case r.Sign() < 0:
			wantErr = true
		case r.Sign() > 0:
			var err error
			wantUp, wantBilled, err = charge(r)
			wantErr = err != nil
		}
		up, billed, err := Charge(raw)
		if up != wantUp || billed != wantBilled || (err != nil) != wantErr {
			t.Fatalf("%s charge=(%d,%d,%v) rational=(%d,%d,err=%t)", raw, up, billed, err, wantUp, wantBilled, wantErr)
		}
		got, err := amount(raw)
		if (err != nil) != (r.Sign() < 0) || (err == nil && got.Cmp(r) != 0) {
			t.Fatalf("%s amount=%v err=%v want exactly %s", raw, got, err, r.RatString())
		}
	}
}

// ratFromDecimal is the original exact path: math/big on the raw spelling.
func ratFromDecimal(s string) (*big.Rat, bool) {
	return new(big.Rat).SetString(s)
}

func randomDecimal(rng *rand.Rand) string {
	digits := func(n int, lead bool) string {
		var b strings.Builder
		for i := 0; i < n; i++ {
			d := byte('0' + rng.Intn(10))
			if i == 0 && lead && d == '0' {
				d = '1' + byte(rng.Intn(9))
			}
			switch rng.Intn(6) { // bias towards rounding-sensitive digits
			case 0:
				d = '9'
			case 1:
				if !(i == 0 && lead) {
					d = '0'
				}
			case 2:
				d = '4' + byte(rng.Intn(2))
			}
			b.WriteByte(d)
		}
		return b.String()
	}
	var s strings.Builder
	if rng.Intn(10) == 0 {
		s.WriteByte('-')
	}
	if n := rng.Intn(15); n == 0 {
		s.WriteByte('0')
	} else {
		s.WriteString(digits(n, true))
	}
	if rng.Intn(2) == 0 {
		s.WriteString("." + digits(1+rng.Intn(24), false))
	}
	if rng.Intn(2) == 0 {
		s.WriteByte("eE"[rng.Intn(2)])
		s.WriteString([]string{"", "+", "-"}[rng.Intn(3)])
		s.WriteString(strings.Repeat("0", rng.Intn(3)))
		s.WriteString(strconv.Itoa(rng.Intn(20)))
	}
	return s.String()
}
