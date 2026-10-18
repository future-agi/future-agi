package capability

import (
	"bytes"
	"encoding/json"
	"io"
	"strings"
)

// Decode rejects unknown fields at every struct level and multiple JSON values.
// Ingress-only fields carry json:"-" and therefore cannot be set by the client.
func Decode(body []byte) (Request, error) {
	var req Request
	if len(bytes.TrimSpace(body)) == 0 || bytes.TrimSpace(body)[0] != '{' {
		return req, invalid("invalid_request", "body", "object required")
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

	var fields map[string]json.RawMessage
	_ = json.Unmarshal(body, &fields)
	for name, value := range map[string]float64{"duration_seconds": req.DurationSeconds, "fps": float64(req.FPS), "n": float64(req.N)} {
		if _, present := fields[name]; present && value == 0 {
			return req, invalid("invalid_parameter", name, "explicit zero or null is not a default")
		}
	}
	return req, nil
}
func (r *Registry) Normalize(req Request, region string) (Resolved, error) {
	resolved, err := r.resolve(req, region)
	if err != nil {
		return Resolved{}, err
	}
	m := resolved.Capability
	if req.DurationSeconds == 0 {
		req.DurationSeconds = 5
	}
	if req.Resolution == "" {
		req.Resolution = "720p"
	}
	if req.AspectRatio == "" {
		req.AspectRatio = "16:9"
		spec := m.Operations[resolved.Operation]
		if len(spec.AspectRatios) == 1 {
			req.AspectRatio = spec.AspectRatios[0]
		}
	}
	if req.FPS == 0 {
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
