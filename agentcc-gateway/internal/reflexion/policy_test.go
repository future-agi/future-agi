package reflexion

import (
	"encoding/json"
	"math"
	"testing"
)

func TestParsePolicy(t *testing.T) {
	for _, tc := range []struct {
		name     string
		cfg      map[string]interface{}
		enabled  bool
		attempts int
		invalid  bool
	}{
		{"default rejects", nil, false, 1, false},
		{"opt in defaults to one", map[string]interface{}{"on_block": "reflect"}, true, 1, false},
		{"yaml integer", map[string]interface{}{"on_block": "reflect", "reflect_max_attempts": 5}, true, 5, false},
		{"json number", map[string]interface{}{"reflect_max_attempts": float64(2)}, false, 2, false},
		{"decoder number", map[string]interface{}{"reflect_max_attempts": json.Number("3")}, false, 3, false},
		{"zero", map[string]interface{}{"reflect_max_attempts": 0}, false, 0, true},
		{"above cap", map[string]interface{}{"reflect_max_attempts": 6}, false, 0, true},
		{"fraction", map[string]interface{}{"reflect_max_attempts": 1.5}, false, 0, true},
		{"nan", map[string]interface{}{"reflect_max_attempts": math.NaN()}, false, 0, true},
		{"boolean", map[string]interface{}{"reflect_max_attempts": true}, false, 0, true},
		{"numeric string", map[string]interface{}{"reflect_max_attempts": "2"}, false, 0, true},
		{"invalid policy", map[string]interface{}{"on_block": "retry"}, false, 0, true},
		{"invalid debug", map[string]interface{}{"reflect_debug": "true"}, false, 0, true},
		{"invalid stage", map[string]interface{}{"stage": "both"}, false, 0, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p, err := Parse(tc.cfg)
			if (err != nil) != tc.invalid {
				t.Fatalf("policy=%+v error=%v", p, err)
			}
			if err == nil && (p.Enabled != tc.enabled || p.MaxAttempts != tc.attempts) {
				t.Fatalf("policy=%+v", p)
			}
		})
	}
}
