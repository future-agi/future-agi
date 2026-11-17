package main

import (
	"bytes"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"

	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/encoding/protojson"
)

// OTLP/JSON is the proto3 JSON mapping with differences a generic protojson
// round trip gets wrong (OTLP specification, "JSON Protobuf Encoding"): trace
// and span ids are hex, not base64; enum values are integers; and receivers
// ignore unknown fields. decodeOTLPJSON and encodeOTLPJSON convert the ids at
// their known places in a TracesData document, so one span delivered as JSON
// or as protobuf decodes to the same message and yields the same receipt.

// idKeys are the id fields of a span and of a span link, by JSON name and by
// proto field name (protojson accepts both).
var idKeys = []string{"traceId", "trace_id", "spanId", "span_id", "parentSpanId", "parent_span_id"}

// objects returns the objects in the array at the first present key.
func objects(obj map[string]any, keys ...string) ([]map[string]any, error) {
	for _, k := range keys {
		raw, ok := obj[k]
		if !ok || raw == nil {
			continue
		}
		list, ok := raw.([]any)
		if !ok {
			return nil, fmt.Errorf("%s is not an array", k)
		}
		out := make([]map[string]any, 0, len(list))
		for _, item := range list {
			o, ok := item.(map[string]any)
			if !ok {
				return nil, fmt.Errorf("%s holds a non-object", k)
			}
			out = append(out, o)
		}
		return out, nil
	}
	return nil, nil
}

// convertIDs rewrites every span and span-link id in a TracesData document.
func convertIDs(root map[string]any, convert func(string) (string, error)) error {
	rewrite := func(o map[string]any) error {
		for _, k := range idKeys {
			raw, ok := o[k]
			if !ok || raw == nil {
				continue
			}
			s, ok := raw.(string)
			if !ok {
				return fmt.Errorf("%s is not a string", k)
			}
			v, err := convert(s)
			if err != nil {
				return fmt.Errorf("%s: %w", k, err)
			}
			o[k] = v
		}
		return nil
	}
	resources, err := objects(root, "resourceSpans", "resource_spans")
	if err != nil {
		return err
	}
	for _, rs := range resources {
		scopes, err := objects(rs, "scopeSpans", "scope_spans")
		if err != nil {
			return err
		}
		for _, ss := range scopes {
			spans, err := objects(ss, "spans")
			if err != nil {
				return err
			}
			for _, span := range spans {
				if err := rewrite(span); err != nil {
					return err
				}
				links, err := objects(span, "links")
				if err != nil {
					return err
				}
				for _, link := range links {
					if err := rewrite(link); err != nil {
						return err
					}
				}
			}
		}
	}
	return nil
}

func decodeDocument(body []byte) (map[string]any, error) {
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.UseNumber() // 64-bit values keep their exact text
	var root map[string]any
	if err := dec.Decode(&root); err != nil {
		return nil, err
	}
	if root == nil {
		return nil, errors.New("not a JSON object")
	}
	if _, err := dec.Token(); !errors.Is(err, io.EOF) {
		return nil, errors.New("trailing data after the JSON object")
	}
	return root, nil
}

// decodeOTLPJSON decodes an OTLP/JSON traces request: hex ids, integer or
// named enums, unknown fields ignored.
func decodeOTLPJSON(body []byte) (*tracepb.TracesData, error) {
	root, err := decodeDocument(body)
	if err != nil {
		return nil, err
	}
	err = convertIDs(root, func(s string) (string, error) {
		b, err := hex.DecodeString(s)
		if err != nil {
			return "", errors.New("not hex")
		}
		return base64.StdEncoding.EncodeToString(b), nil
	})
	if err != nil {
		return nil, err
	}
	normalized, err := json.Marshal(root)
	if err != nil {
		return nil, err
	}
	batch := &tracepb.TracesData{}
	return batch, protojson.UnmarshalOptions{DiscardUnknown: true}.Unmarshal(normalized, batch)
}

// encodeOTLPJSON writes batch as OTLP/JSON: hex ids and integer enums.
func encodeOTLPJSON(batch *tracepb.TracesData) ([]byte, error) {
	raw, err := protojson.MarshalOptions{UseEnumNumbers: true}.Marshal(batch)
	if err != nil {
		return nil, err
	}
	root, err := decodeDocument(raw)
	if err != nil {
		return nil, err
	}
	err = convertIDs(root, func(s string) (string, error) {
		b, err := base64.StdEncoding.DecodeString(s)
		return hex.EncodeToString(b), err
	})
	if err != nil {
		return nil, err
	}
	return json.Marshal(root)
}

// validateIDs refuses ids of the wrong length on either transport: a trace id
// is 16 bytes and a span id 8, or empty.
func validateIDs(batch *tracepb.TracesData) error {
	check := func(name string, id []byte, size int) error {
		if len(id) != 0 && len(id) != size {
			return fmt.Errorf("%s is %d bytes, want %d", name, len(id), size)
		}
		return nil
	}
	for _, rs := range batch.GetResourceSpans() {
		for _, ss := range rs.GetScopeSpans() {
			for _, s := range ss.GetSpans() {
				if err := errors.Join(check("traceId", s.GetTraceId(), 16), check("spanId", s.GetSpanId(), 8),
					check("parentSpanId", s.GetParentSpanId(), 8)); err != nil {
					return err
				}
				for _, l := range s.GetLinks() {
					if err := errors.Join(check("link traceId", l.GetTraceId(), 16), check("link spanId", l.GetSpanId(), 8)); err != nil {
						return err
					}
				}
			}
		}
	}
	return nil
}
