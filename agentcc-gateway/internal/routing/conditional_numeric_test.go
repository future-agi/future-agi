package routing

import (
	"math"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Invalid operands must not be ordered below valid numbers by a failure sentinel.
func TestEvalOpNumericComparisonsRejectNonNumeric(t *testing.T) {
	cases := []struct {
		name  string
		value interface{}
	}{
		{"text", "not-a-number"},
		{"empty", ""},
		{"missing", nil},
		{"slice", []interface{}{1, 2}},
		{"object", map[string]interface{}{"tokens": 5}},
		{"boolean", false},
		{"overflow", "1e1000"},
		{"nan-string", "NaN"},
		{"nan-float", math.NaN()},
		{"nan-float32", float32(math.NaN())},
	}
	for _, tc := range cases {
		for _, op := range []string{OpGt, OpLt, OpGte, OpLte} {
			t.Run(tc.name+"/"+op, func(t *testing.T) {
				if evalOp(op, tc.value, 1000, nil) {
					t.Errorf("invalid resolved operand matched %s", op)
				}
				if evalOp(op, 1000, tc.value, nil) {
					t.Errorf("invalid expected operand matched %s", op)
				}
				if evalOp(op, tc.value, tc.value, nil) {
					t.Errorf("two invalid operands matched %s", op)
				}
			})
		}
	}
}

func TestEvalOpNumericComparisons(t *testing.T) {
	cases := []struct {
		name     string
		resolved interface{}
		expected interface{}
		order    int
	}{
		{"less", 5, 10, -1},
		{"greater", 10, 5, 1},
		{"equal", 10, 10, 0},
		{"zero", 0, 0, 0},
		{"negative", -5, 0, -1},
		{"fraction", 0.5, 1.0, -1},
		{"float32", float32(5), float64(10), -1},
		{"int32", int32(5), int64(10), -1},
		{"int64", int64(10), 5, 1},
		{"numeric-strings", "5", "10", -1},
		{"mixed-types", "1e3", 1000, 0},
	}
	for _, tc := range cases {
		for _, op := range []struct {
			name string
			want bool
		}{
			{OpLt, tc.order < 0},
			{OpLte, tc.order <= 0},
			{OpGt, tc.order > 0},
			{OpGte, tc.order >= 0},
		} {
			t.Run(tc.name+"/"+op.name, func(t *testing.T) {
				if got := evalOp(op.name, tc.resolved, tc.expected, nil); got != op.want {
					t.Errorf("evalOp(%q, %v, %v) = %v, want %v", op.name, tc.resolved, tc.expected, got, op.want)
				}
			})
		}
	}
}

func TestConditionalNumericRouteSkipsInvalidMetadata(t *testing.T) {
	for _, op := range []string{OpLt, OpLte} {
		t.Run(op, func(t *testing.T) {
			router, err := NewConditionalRouter([]config.ConditionalRouteConfig{
				{
					Name: "numeric", Priority: 1,
					Condition: config.ConditionConfig{Field: "metadata.tokens", Op: op, Value: 1000},
					Action:    config.RouteActionConfig{Provider: "numeric-provider"},
				},
				{
					Name: "fallback", Priority: 2,
					Condition: config.ConditionConfig{Field: "model", Op: OpEq, Value: "test-model"},
					Action:    config.RouteActionConfig{Provider: "fallback-provider"},
				},
			})
			if err != nil {
				t.Fatal(err)
			}
			for _, tc := range []struct {
				name     string
				metadata map[string]string
				want     string
			}{
				{"missing", nil, "fallback-provider"},
				{"empty", map[string]string{"tokens": ""}, "fallback-provider"},
				{"text", map[string]string{"tokens": "unknown"}, "fallback-provider"},
				{"nan", map[string]string{"tokens": "NaN"}, "fallback-provider"},
				{"zero", map[string]string{"tokens": "0"}, "numeric-provider"},
				{"below", map[string]string{"tokens": "999"}, "numeric-provider"},
				{"above", map[string]string{"tokens": "1001"}, "fallback-provider"},
			} {
				t.Run(tc.name, func(t *testing.T) {
					rc := testRC("test-model", "", false, tc.metadata)
					if got := router.Evaluate(rc); got == nil || got.Provider != tc.want {
						t.Fatalf("route = %+v, want provider %q", got, tc.want)
					}
				})
			}
		})
	}
}
