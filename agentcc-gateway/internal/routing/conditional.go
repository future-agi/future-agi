package routing

import (
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Operator constants for condition evaluation.
const (
	OpEq     = "$eq"
	OpNe     = "$ne"
	OpIn     = "$in"
	OpNin    = "$nin"
	OpRegex  = "$regex"
	OpGt     = "$gt"
	OpLt     = "$lt"
	OpGte    = "$gte"
	OpLte    = "$lte"
	OpExists = "$exists"
)

// validOps is the set of valid operators.
var validOps = map[string]bool{
	OpEq: true, OpNe: true, OpIn: true, OpNin: true,
	OpRegex: true, OpGt: true, OpLt: true, OpGte: true,
	OpLte: true, OpExists: true,
}

// RouteAction describes what to do when a conditional route matches.
type RouteAction struct {
	Name          string // Rule name for observability
	Provider      string
	ModelOverride string
}

// Condition is a recursive evaluation tree.
type Condition struct {
	// Leaf fields
	Field string
	Op    string
	Value interface{}
	Regex *regexp.Regexp // pre-compiled for $regex

	// Combinators
	And []*Condition
	Or  []*Condition
	Not *Condition
}

// ConditionalRoute pairs a condition with an action.
type ConditionalRoute struct {
	Name      string
	Priority  int
	Condition *Condition
	Action    RouteAction
}

// ConditionalRouter evaluates conditional routes against a request context.
type ConditionalRouter struct {
	routes []ConditionalRoute // sorted by priority ascending
}

// NewConditionalRouter creates a ConditionalRouter from config.
// It validates all conditions, pre-compiles regexes, and sorts by priority.
func NewConditionalRouter(configs []config.ConditionalRouteConfig) (*ConditionalRouter, error) {
	if len(configs) == 0 {
		return nil, nil
	}

	routes := make([]ConditionalRoute, 0, len(configs))
	for i, cfg := range configs {
		if cfg.Action.Provider == "" {
			return nil, fmt.Errorf("conditional_routes[%d] %q: action.provider is required", i, cfg.Name)
		}

		cond, err := parseCondition(cfg.Condition)
		if err != nil {
			return nil, fmt.Errorf("conditional_routes[%d] %q: %w", i, cfg.Name, err)
		}

		routes = append(routes, ConditionalRoute{
			Name:      cfg.Name,
			Priority:  cfg.Priority,
			Condition: cond,
			Action: RouteAction{
				Name:          cfg.Name,
				Provider:      cfg.Action.Provider,
				ModelOverride: cfg.Action.ModelOverride,
			},
		})
	}

	sort.Slice(routes, func(i, j int) bool {
		return routes[i].Priority < routes[j].Priority
	})

	return &ConditionalRouter{routes: routes}, nil
}

// Evaluate checks all routes in priority order and returns the first match.
// Returns nil if no route matches.
func (cr *ConditionalRouter) Evaluate(rc *models.RequestContext) *RouteAction {
	if cr == nil || len(cr.routes) == 0 {
		return nil
	}

	for i := range cr.routes {
		if cr.routes[i].Condition.evaluate(rc) {
			action := cr.routes[i].Action
			return &action
		}
	}
	return nil
}

// RouteCount returns the number of configured routes.
func (cr *ConditionalRouter) RouteCount() int {
	if cr == nil {
		return 0
	}
	return len(cr.routes)
}

// parseCondition recursively parses a ConditionConfig into a Condition tree.
func parseCondition(cfg config.ConditionConfig) (*Condition, error) {
	cond := &Condition{}

	// Check for logical combinators.
	hasAnd := len(cfg.And) > 0
	hasOr := len(cfg.Or) > 0
	hasNot := cfg.Not != nil
	hasLeaf := cfg.Field != "" || cfg.Op != ""

	combinatorCount := 0
	if hasAnd {
		combinatorCount++
	}
	if hasOr {
		combinatorCount++
	}
	if hasNot {
		combinatorCount++
	}

	if combinatorCount > 0 && hasLeaf {
		return nil, fmt.Errorf("condition cannot have both leaf (field/op) and combinator ($and/$or/$not)")
	}
	if combinatorCount > 1 {
		return nil, fmt.Errorf("condition cannot have multiple combinators ($and, $or, $not); use nesting")
	}

	if hasAnd {
		cond.And = make([]*Condition, 0, len(cfg.And))
		for i, child := range cfg.And {
			c, err := parseCondition(child)
			if err != nil {
				return nil, fmt.Errorf("$and[%d]: %w", i, err)
			}
			cond.And = append(cond.And, c)
		}
		return cond, nil
	}

	if hasOr {
		cond.Or = make([]*Condition, 0, len(cfg.Or))
		for i, child := range cfg.Or {
			c, err := parseCondition(child)
			if err != nil {
				return nil, fmt.Errorf("$or[%d]: %w", i, err)
			}
			cond.Or = append(cond.Or, c)
		}
		return cond, nil
	}

	if hasNot {
		c, err := parseCondition(*cfg.Not)
		if err != nil {
			return nil, fmt.Errorf("$not: %w", err)
		}
		cond.Not = c
		return cond, nil
	}

	// Leaf condition.
	if cfg.Field == "" {
		return nil, fmt.Errorf("leaf condition requires a field")
	}
	if cfg.Op == "" {
		return nil, fmt.Errorf("leaf condition requires an op")
	}
	if !validOps[cfg.Op] {
		return nil, fmt.Errorf("invalid operator %q", cfg.Op)
	}

	cond.Field = cfg.Field
	cond.Op = cfg.Op
	cond.Value = cfg.Value

	// Pre-compile regex.
	if cfg.Op == OpRegex {
		pattern, ok := cfg.Value.(string)
		if !ok {
			return nil, fmt.Errorf("$regex value must be a string, got %T", cfg.Value)
		}
		re, err := regexp.Compile(pattern)
		if err != nil {
			return nil, fmt.Errorf("invalid regex %q: %w", pattern, err)
		}
		cond.Regex = re
	}

	return cond, nil
}

// evaluate recursively evaluates the condition against a RequestContext.
func (c *Condition) evaluate(rc *models.RequestContext) bool {
	if c == nil {
		return true
	}

	// Logical combinators.
	if len(c.And) > 0 {
		for _, child := range c.And {
			if !child.evaluate(rc) {
				return false
			}
		}
		return true
	}

	if len(c.Or) > 0 {
		for _, child := range c.Or {
			if child.evaluate(rc) {
				return true
			}
		}
		return false
	}

	if c.Not != nil {
		return !c.Not.evaluate(rc)
	}

	// Leaf evaluation.
	resolved := resolveField(rc, c.Field)
	return evalOp(c.Op, resolved, c.Value, c.Regex)
}

// resolveField extracts a value from the RequestContext by field path.
func resolveField(rc *models.RequestContext, field string) interface{} {
	switch field {
	case "model":
		return rc.Model
	case "user":
		return rc.UserID
	case "stream":
		return rc.IsStream
	case "provider":
		return rc.Provider
	case "session_id":
		return rc.SessionID
	case "request_id":
		return rc.RequestID
	default:
		if strings.HasPrefix(field, "metadata.") {
			key := field[len("metadata."):]
			if v, ok := rc.Metadata[key]; ok {
				return v
			}
			return nil
		}
		return nil
	}
}

// evalOp applies an operator to a resolved value and an expected value.
func evalOp(op string, resolved interface{}, expected interface{}, re *regexp.Regexp) bool {
	switch op {
	case OpEq:
		return equalValues(resolved, expected)
	case OpNe:
		return !equalValues(resolved, expected)
	case OpIn:
		return inSlice(resolved, expected)
	case OpNin:
		return !inSlice(resolved, expected)
	case OpRegex:
		if re == nil {
			return false
		}
		return re.MatchString(fmt.Sprint(resolved))
	case OpGt:
		return compareNumeric(resolved, expected) > 0
	case OpLt:
		return compareNumeric(resolved, expected) < 0
	case OpGte:
		cmp := compareNumeric(resolved, expected)
		return cmp >= 0
	case OpLte:
		cmp := compareNumeric(resolved, expected)
		return cmp <= 0
	case OpExists:
		exists := resolved != nil
		if b, ok := expected.(bool); ok {
			return exists == b
		}
		return exists
	default:
		return false
	}
}

// equalValues compares two values with type coercion.
func equalValues(a, b interface{}) bool {
	if a == nil && b == nil {
		return true
	}
	if a == nil || b == nil {
		return false
	}

	// Direct comparison.
	if a == b {
		return true
	}

	// String comparison.
	aStr := fmt.Sprint(a)
	bStr := fmt.Sprint(b)
	return aStr == bStr
}

// inSlice checks if resolved is in the expected slice.
func inSlice(resolved interface{}, expected interface{}) bool {
	if resolved == nil {
		return false
	}

	// expected should be a slice.
	switch s := expected.(type) {
	case []interface{}:
		for _, v := range s {
			if equalValues(resolved, v) {
				return true
			}
		}
	case []string:
		rs := fmt.Sprint(resolved)
		for _, v := range s {
			if rs == v {
				return true
			}
		}
	}
	return false
}

// compareNumeric converts both values to float64 and returns -1, 0, or 1.
// Returns -2 on conversion failure (which makes all comparisons false).
func compareNumeric(a, b interface{}) int {
	af, aOk := toFloat64(a)
	bf, bOk := toFloat64(b)
	if !aOk || !bOk {
		return -2
	}
	if af < bf {
		return -1
	}
	if af > bf {
		return 1
	}
	return 0
}

// toFloat64 attempts to convert a value to float64.
func toFloat64(v interface{}) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case float32:
		return float64(n), true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case int32:
		return float64(n), true
	case string:
		f, err := strconv.ParseFloat(n, 64)
		return f, err == nil
	default:
		return 0, false
	}
}
