package routing

import "testing"

// A non-numeric (or missing) resolved value must not satisfy ANY numeric
// comparison operator. Regression: $lt and $lte previously matched because
// compareNumeric returned a -2 sentinel on conversion failure, and -2 < 0 and
// -2 <= 0 are both true, so a conditional route matched every request whose
// field was absent or non-numeric.
func TestEvalOpNumericComparisonsRejectNonNumeric(t *testing.T) {
	nonNumeric := []interface{}{"not-a-number", "", nil, []interface{}{1, 2}}
	ops := []string{OpGt, OpLt, OpGte, OpLte}
	for _, v := range nonNumeric {
		for _, op := range ops {
			if evalOp(op, v, 1000, nil) {
				t.Errorf("evalOp(%q, %#v, 1000) = true, want false (non-numeric must not match)", op, v)
			}
		}
	}
}

// The numeric comparisons themselves must still behave correctly.
func TestEvalOpNumericComparisons(t *testing.T) {
	cases := []struct {
		op       string
		resolved interface{}
		expected interface{}
		want     bool
	}{
		{OpLt, 5, 10, true},
		{OpLt, 10, 5, false},
		{OpLt, 10, 10, false},
		{OpLte, 10, 10, true},
		{OpGt, 10, 5, true},
		{OpGt, 5, 10, false},
		{OpGte, 10, 10, true},
		{OpGte, 5, 10, false},
		{OpLt, "5", "10", true}, // numeric strings are parsed
	}
	for _, c := range cases {
		if got := evalOp(c.op, c.resolved, c.expected, nil); got != c.want {
			t.Errorf("evalOp(%q, %v, %v) = %v, want %v", c.op, c.resolved, c.expected, got, c.want)
		}
	}
}
