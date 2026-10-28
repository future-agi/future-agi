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

// Validate applies the shared controls to the actual execution stage. The
// provider's config.mode is deliberately opaque; only a static rule's Mode can
// declare asynchronous execution.
func Validate(cfg map[string]interface{}, name, stage, action, mode string) (Policy, error) {
	p, err := Parse(cfg)
	if err != nil {
		return Policy{}, err
	}
	if p.Enabled {
		if stage != "post" {
			return Policy{}, fmt.Errorf("retry with feedback requires a post-response check")
		}
		if action != "block" {
			return Policy{}, fmt.Errorf("retry with feedback requires action=block")
		}
		if mode != "" && mode != "sync" {
			return Policy{}, fmt.Errorf("retry with feedback requires synchronous execution")
		}
	}
	// Only explicit new controls tighten legacy stage behavior. Static stage
	// remains authoritative over config.stage for the actual execution decision.
	if _, explicit := cfg["stage"]; explicit || p.Enabled {
		if stage == "post" && (name == "tool-permissions" || cfg["provider"] == "tool_permission" || cfg["provider"] == "tool_permissions") && cfg["apply_to"] == "request" {
			return Policy{}, fmt.Errorf("tool permission response checks require apply_to=response or both")
		}
		switch name {
		case "prompt-injection", "injection-detector", "topic-restriction", "language-detection", "system-prompt-protection":
			if stage != "pre" {
				return Policy{}, fmt.Errorf("%s only supports request checks", name)
			}
		case "hallucination-detection", "data-leakage-prevention":
			if stage != "post" {
				return Policy{}, fmt.Errorf("%s only supports response checks", name)
			}
		}
	}
	return p, nil
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
