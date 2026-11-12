package capability

import (
	"bytes"
	"encoding/json"
	"io"
	"reflect"
	"sort"
	"strings"
	"unicode/utf8"
)

// Decode rejects unknown fields at every struct level and multiple JSON values.
// Ingress-only fields carry json:"-" and therefore cannot be set by the client.
func Decode(body []byte) (Request, error) {
	var req Request
	if len(bytes.TrimSpace(body)) == 0 || bytes.TrimSpace(body)[0] != '{' {
		return req, invalid("invalid_request", "body", "object required")
	}
	// encoding/json silently replaces invalid UTF-8 with U+FFFD, which would
	// submit a prompt the caller never sent.
	if !utf8.Valid(body) {
		return req, invalid("invalid_request", "body", "UTF-8 JSON required")
	}
	d := json.NewDecoder(bytes.NewReader(body))
	d.DisallowUnknownFields()
	d.UseNumber()
	if err := d.Decode(&req); err != nil {
		if field, ok := strings.CutPrefix(err.Error(), "json: unknown field "); ok {
			return req, invalid("unknown_field", strings.Trim(field, "\""), "unknown field")
		}
		return req, invalid("invalid_request", "body", "invalid JSON field or type")
	}
	if err := d.Decode(new(any)); err != io.EOF {
		return req, invalid("invalid_request", "body", "one JSON object required")
	}
	// Reject repeats before building maps: encoding/json merges repeated struct
	// values and ignores null assignments to numbers, while maps keep only the
	// last occurrence. Either behavior could hide an alias or explicit null.
	if name, dup := duplicateKey(body); dup {
		return req, invalid("invalid_parameter", name, "field repeated")
	}
	// encoding/json also matches field names case-insensitively, so an alias such
	// as "DURATION_SECONDS" sets the field without DisallowUnknownFields noticing.
	// Require the canonical spelling at every struct level, so the explicit-zero
	// check below sees every key that reached the struct.
	if name, ok := caseAlias(body, reflect.TypeOf(req)); ok {
		return req, invalid("unknown_field", name, "field names are case-sensitive")
	}

	var fields map[string]json.RawMessage
	_ = json.Unmarshal(body, &fields)
	for name, value := range map[string]float64{"duration_seconds": req.DurationSeconds, "fps": float64(req.FPS), "n": float64(req.N)} {
		if _, present := fields[name]; present && value == 0 {
			return req, invalid("invalid_parameter", name, "explicit zero or null is not a default")
		}
	}
	return req, nil
}

// caseAlias returns the first object key, in sorted order, that is not the exact
// JSON name of a field of t. It walks nested structs and slices of structs; map
// fields keep their keys verbatim and are not inspected. Decode runs it only
// after a successful DisallowUnknownFields decode, so every key it reports is a
// case-folded alias of a real field.
func caseAlias(raw []byte, t reflect.Type) (string, bool) {
	for t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	switch t.Kind() {
	case reflect.Slice, reflect.Array:
		var items []json.RawMessage
		if json.Unmarshal(raw, &items) != nil {
			return "", false
		}
		for _, item := range items {
			if name, ok := caseAlias(item, t.Elem()); ok {
				return name, true
			}
		}
	case reflect.Struct:
		var fields map[string]json.RawMessage
		if json.Unmarshal(raw, &fields) != nil {
			return "", false
		}
		keys := make([]string, 0, len(fields))
		for k := range fields {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			f, ok := jsonField(t, k)
			if !ok {
				return k, true
			}
			if name, ok := caseAlias(fields[k], f.Type); ok {
				return name, true
			}
		}
	}
	return "", false
}

// jsonField finds the exported field whose JSON name is exactly name.
func jsonField(t reflect.Type, name string) (reflect.StructField, bool) {
	for i := 0; i < t.NumField(); i++ {
		f := t.Field(i)
		if !f.IsExported() {
			continue
		}
		tag, _, _ := strings.Cut(f.Tag.Get("json"), ",")
		if tag == "-" {
			continue
		}
		if tag == "" {
			tag = f.Name
		}
		if tag == name {
			return f, true
		}
	}
	return reflect.StructField{}, false
}
func (r *Registry) Normalize(req Request, region string) (Resolved, error) {
	resolved, err := r.resolve(req, region)
	if err != nil {
		return Resolved{}, err
	}
	m := resolved.Capability
	if req.DurationSeconds == 0 {
		req.DurationSeconds = 5
		if m.DefaultDuration > 0 {
			req.DurationSeconds = float64(m.DefaultDuration)
		}
	}
	if req.Resolution == "" {
		req.Resolution = "720p"
		if m.DefaultResolution != "" {
			req.Resolution = m.DefaultResolution
		}
	}
	if req.AspectRatio == "" {
		req.AspectRatio = "16:9"
		spec := m.Operations[resolved.Operation]
		if len(spec.AspectRatios) == 1 {
			req.AspectRatio = spec.AspectRatios[0]
		}
	}
	// Models without a documented frame-rate control keep fps unset.
	if req.FPS == 0 && len(m.FPS) > 0 {
		req.FPS = m.FPS[0]
	}
	if req.N == 0 {
		req.N = 1
	}
	if req.Audio == nil {
		on := m.Audio.Generated != "never"
		req.Audio = &on
	}
	opts := make(map[string]any, len(req.ProviderOptions)+len(m.Options))
	for k, v := range req.ProviderOptions {
		opts[k] = v
	}
	for k, spec := range m.Options {
		if _, ok := opts[k]; !ok && spec.Default != nil {
			opts[k] = spec.Default
		}
	}
	req.ProviderOptions = opts
	req.Inputs = append([]Input(nil), req.Inputs...)
	for k, v := range opts {
		if m.Options[k].Type == "int" {
			if n, ok := integer(v); ok {
				opts[k] = n
			}
		}
	}
	resolved.Request = req
	if err := validateResolved(resolved); err != nil {
		return Resolved{}, err
	}
	return resolved, nil
}
func (r *Registry) Validate(req Request, region string) (Resolved, error) {
	return r.Normalize(req, region)
}
