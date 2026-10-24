package mcp

import (
	"encoding/json"
	"fmt"
)

// hookValidateArgs is step 8 of the tools/call chain (chain.go).
const hookValidateArgs = "validateArgs"

// validateArgs checks the call's arguments against the tool's input schema
// through Deps.Validator.
//
// Default: the basic check (basicValidator). Refusal:
// invalid_arguments.
// Keep observeHook as its first statement.
func validateArgs(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookValidateArgs)
	if cc.legacy.tool == nil {
		return nil
	}
	return s.Deps().Validator.Validate(cc, cc.legacy.tool.Tool.InputSchema)
}

// basicValidator is the default check: required arguments and top-level types
// (validateArgsAgainstSchema, hook_validate.go).
type basicValidator struct{}

func (basicValidator) Validate(cc *CallContext, schema json.RawMessage) *Denial {
	if len(schema) == 0 {
		return nil
	}
	if errMsg := validateArgsAgainstSchema(schema, cc.legacy.arguments); errMsg != "" {
		return rpcDenial(ErrCodeInvalidParams, "invalid_arguments", errMsg)
	}
	return nil
}

// validateArgsAgainstSchema validates tool call arguments against a JSON Schema.
// Checks required fields and basic type validation.
func validateArgsAgainstSchema(schemaRaw json.RawMessage, args map[string]interface{}) string {
	var schema struct {
		Type       string                            `json:"type"`
		Required   []string                          `json:"required"`
		Properties map[string]map[string]interface{} `json:"properties"`
	}
	if err := json.Unmarshal(schemaRaw, &schema); err != nil {
		return "" // Can't parse schema, skip validation
	}

	// Check required fields.
	for _, req := range schema.Required {
		if _, ok := args[req]; !ok {
			return fmt.Sprintf("missing required argument: %q", req)
		}
	}

	// Check types of provided arguments.
	for name, val := range args {
		prop, ok := schema.Properties[name]
		if !ok {
			continue // Extra args are allowed
		}
		expectedType, _ := prop["type"].(string)
		if expectedType == "" {
			continue
		}
		if !matchesJSONType(val, expectedType) {
			return fmt.Sprintf("argument %q: expected type %q", name, expectedType)
		}
	}

	return ""
}

// matchesJSONType checks if a Go value matches a JSON Schema type.
func matchesJSONType(val interface{}, jsonType string) bool {
	switch jsonType {
	case "string":
		_, ok := val.(string)
		return ok
	case "number":
		switch val.(type) {
		case float64, int, int64, float32:
			return true
		}
		return false
	case "integer":
		switch v := val.(type) {
		case float64:
			return v == float64(int64(v))
		case int, int64:
			return true
		}
		return false
	case "boolean":
		_, ok := val.(bool)
		return ok
	case "array":
		_, ok := val.([]interface{})
		return ok
	case "object":
		_, ok := val.(map[string]interface{})
		return ok
	case "null":
		return val == nil
	}
	return true // Unknown type, allow
}
