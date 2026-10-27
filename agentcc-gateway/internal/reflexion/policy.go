// Package reflexion defines the common, per-rule response repair policy.
package reflexion

import (
	"encoding/json"
	"fmt"
	"math"
)

const MaxAttempts = 5

// Policy counts additional generations, not the original provider call.
// A zero value rejects. Parse supplies the default of one additional attempt.
type Policy struct {
	Enabled     bool
	MaxAttempts int
	Debug       bool
}

// Parse accepts both YAML integers and JSON numbers, but never coerces strings,
// booleans, fractions, or an out-of-range value into a retry allowance.
func Parse(cfg map[string]interface{}) (Policy, error) {
	p := Policy{MaxAttempts: 1}
	if v, ok := cfg["on_block"]; ok {
		s, valid := v.(string)
		if !valid || (s != "reject" && s != "reflect") {
			return Policy{}, fmt.Errorf("config.on_block must be reject or reflect")
		}
		p.Enabled = s == "reflect"
	}
	if v, ok := cfg["reflect_max_attempts"]; ok {
		var n float64
		switch v := v.(type) {
		case int:
			n = float64(v)
		case int64:
			n = float64(v)
		case float64:
			n = v
		case json.Number:
			var err error
			n, err = v.Float64()
			if err != nil {
				return Policy{}, fmt.Errorf("config.reflect_max_attempts must be an integer from 1 to 5")
			}
		default:
			return Policy{}, fmt.Errorf("config.reflect_max_attempts must be an integer from 1 to 5")
		}
		if math.IsNaN(n) || math.IsInf(n, 0) || n != math.Trunc(n) || n < 1 || n > MaxAttempts {
			return Policy{}, fmt.Errorf("config.reflect_max_attempts must be an integer from 1 to 5")
		}
		p.MaxAttempts = int(n)
	}
	if v, ok := cfg["reflect_debug"]; ok {
		var valid bool
		p.Debug, valid = v.(bool)
		if !valid {
			return Policy{}, fmt.Errorf("config.reflect_debug must be a boolean")
		}
	}
	if v, ok := cfg["stage"]; ok {
		s, valid := v.(string)
		if !valid || (s != "pre" && s != "post") {
			return Policy{}, fmt.Errorf("config.stage must be pre or post")
		}
	}
	return p, nil
}
