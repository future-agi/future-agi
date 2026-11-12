package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"unicode/utf8"
)

// AdmissionPromptValidator is MANDATORY before PROMPT_GET_RENDER dispatch.
// WP03C must supply a separately qualified, scope/target-bound approved-schema
// validator: exact known argument names, string values only, no nesting, URL
// fetching, template evaluation or executable argument interpretation. It may
// not log values or forward them to accounts. Missing dependency fails closed;
// the local shape checks below are not an implementation of approved schemas.
type AdmissionPromptValidator interface {
	ValidatePrompt(context.Context, ScopeKey, AdmissionActor, string, map[string]string) error
}

func admissionPromptShape(raw json.RawMessage) (json.RawMessage, string, map[string]string, error) {
	fail := func() (json.RawMessage, string, map[string]string, error) { return nil, "", nil, errAdmissionDenied }
	if len(raw) > admissionUnitMaxBytes || !utf8.Valid(raw) {
		return fail()
	}
	fields, err := admissionUniqueObject(raw)
	if err != nil {
		return fail()
	}
	for key := range fields {
		if key != "name" && key != "arguments" {
			return fail()
		}
	}
	var name string
	if json.Unmarshal(fields["name"], &name) != nil || !admissionOpaque(name) || !utf8.ValidString(name) {
		return fail()
	}
	args := map[string]string{}
	if rawArgs, ok := fields["arguments"]; ok {
		values, err := admissionUniqueObject(rawArgs)
		if err != nil || len(values) > 32 {
			return fail()
		}
		for key, value := range values {
			if key == "" || len(key) > 128 || !utf8.ValidString(key) {
				return fail()
			}
			var text string
			// json.Unmarshal(null, *string) succeeds, but null is not a string.
			if len(value) == 0 || value[0] != '"' || json.Unmarshal(value, &text) != nil || len(text) > 4096 || !utf8.ValidString(text) {
				return fail()
			}
			args[key] = text
		}
	}
	// Compact, sorted-name JSON of the argument object, UTF-8 and without
	// HTML escaping. The encoder's framing newline is not part of the object.
	var canonical bytes.Buffer
	encoder := json.NewEncoder(&canonical)
	encoder.SetEscapeHTML(false)
	if encoder.Encode(args) != nil || canonical.Len()-1 > 16384 {
		return fail()
	}
	selector, _ := json.Marshal(struct {
		Name string `json:"name"`
	}{name})
	return selector, name, args, nil
}

// Reject duplicate keys instead of depending on last-value-wins parsing. Work
// is bounded by the outer body limit; never decode nested values into interfaces.
func admissionUniqueObject(raw []byte) (map[string]json.RawMessage, error) {
	d := json.NewDecoder(bytes.NewReader(raw))
	first, err := d.Token()
	if err != nil || first != json.Delim('{') {
		return nil, errAdmissionDenied
	}
	out := map[string]json.RawMessage{}
	for d.More() {
		token, err := d.Token()
		if err != nil {
			return nil, errAdmissionDenied
		}
		key, ok := token.(string)
		if !ok {
			return nil, errAdmissionDenied
		}
		if _, exists := out[key]; exists {
			return nil, errAdmissionDenied
		}
		var value json.RawMessage
		if d.Decode(&value) != nil {
			return nil, errAdmissionDenied
		}
		out[key] = value
	}
	if last, err := d.Token(); err != nil || last != json.Delim('}') {
		return nil, errAdmissionDenied
	}
	if _, err := d.Token(); err != io.EOF {
		return nil, errAdmissionDenied
	}
	return out, nil
}
