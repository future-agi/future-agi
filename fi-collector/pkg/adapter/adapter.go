// Package adapter splits OpenTelemetry span attributes into the typed
// columns of the CH 25.3 spans table: attrs_string / attrs_number /
// attrs_bool typed Maps, plus an attributes_extra JSON overflow tier.
//
// This is the GO port of pg_to_ch_adapter.py:split_attributes() that powers
// the one-shot historical backfill — same rules, same overflow-key prefix
// list, same bool-vs-int discrimination. We keep both implementations in
// lockstep so backfilled rows and live-ingested rows look identical to
// downstream queries.
//
// The function is intentionally allocator-light: it writes into caller-
// provided maps and a strings.Builder for the JSON overflow. At 1B
// spans/day the GC overhead of per-call allocations would dominate.
package adapter

import (
	"encoding/json"
	"math"
	"strings"

	"go.opentelemetry.io/collector/pdata/pcommon"
)

// Attribute keys whose values stay in the typed-JSON overflow regardless of
// their scalar type. These are LLM message arrays + content payloads — they
// often look like strings (so would otherwise land in attrs_string) but are
// usually nested objects whose shape varies per row, so leaving them in
// overflow keeps the typed Maps' cardinality bounded.
//
// Must match _OVERFLOW_KEY_PREFIXES in pg_to_ch_adapter.py.
var overflowKeyPrefixes = []string{
	"llm.prompt",
	"llm.completion",
	"llm.messages",
	"input.value",
	"output.value",
	"retrieval.documents",
	"embedding.embeddings",
}

// Float64-safe integer range. Larger ints get demoted to overflow rather
// than silently losing precision.
const (
	maxSafeInt = 9007199254740992
	minSafeInt = -9007199254740992
)

// Split routes each attribute into the right destination map. Overflow goes
// into a JSON object that the caller serialises to a string for the
// attributes_extra column.
//
// Caller owns the destination maps + the overflow buffer; this function
// only writes. Safe to reuse maps across spans by clearing them first.
func Split(
	attrs pcommon.Map,
	attrsString map[string]string,
	attrsNumber map[string]float64,
	attrsBool map[string]uint8,
	overflow map[string]any,
) {
	attrs.Range(func(k string, v pcommon.Value) bool {
		if hasOverflowPrefix(k) {
			overflow[k] = otelValueToJSON(v)
			return true
		}

		switch v.Type() {
		case pcommon.ValueTypeStr:
			attrsString[k] = v.Str()
		case pcommon.ValueTypeBool:
			// MUST come before any int path. (In Go bool isn't an int subtype
			// like Python, but the rule still matters for the legibility of
			// the spec mirrored across both implementations.)
			if v.Bool() {
				attrsBool[k] = 1
			} else {
				attrsBool[k] = 0
			}
		case pcommon.ValueTypeInt:
			n := v.Int()
			if n >= minSafeInt && n <= maxSafeInt {
				attrsNumber[k] = float64(n)
			} else {
				overflow[k] = n
			}
		case pcommon.ValueTypeDouble:
			f := v.Double()
			// math.IsFinite isn't in the std library — it's a Plan9 builtin.
			// Reproduce: finite iff neither NaN nor ±Inf.
			if !math.IsNaN(f) && !math.IsInf(f, 0) {
				attrsNumber[k] = f
			} else {
				// NaN / +Inf / -Inf survive into overflow as their JSON-encoded
				// representation rather than corrupting a Float64 Map.
				overflow[k] = otelValueToJSON(v)
			}
		case pcommon.ValueTypeMap, pcommon.ValueTypeSlice, pcommon.ValueTypeBytes:
			overflow[k] = otelValueToJSON(v)
		case pcommon.ValueTypeEmpty:
			overflow[k] = nil
		default:
			// Defensive: future OTel value types fall through to overflow.
			overflow[k] = v.AsString()
		}
		return true
	})
}

// OverflowToJSON serialises the overflow map as JSON text suitable for the
// `attributes_extra String` column wire format. Returns "{}" for empty.
//
// NOTE: CH 25.x typed JSON auto-flattens dotted keys at write time, so
// `{"a.b": 1}` ends up stored as `{"a": {"b": 1}}` and is queried via
// `attributes_extra.a.b.:Int64`. The wire format we emit here is just
// JSON text — CH does the path flattening server-side.
func OverflowToJSON(overflow map[string]any) string {
	if len(overflow) == 0 {
		return "{}"
	}
	b, err := json.Marshal(overflow)
	if err != nil {
		// json.Marshal on a map[string]any only errors on unsupported types
		// (channels, functions). Our values come from OTel pdata so this is
		// effectively unreachable; return empty rather than dropping the row.
		return "{}"
	}
	return string(b)
}

// hasOverflowPrefix walks the prefix list with explicit early-exit. Hot path
// per attribute — keep it allocation-free.
func hasOverflowPrefix(key string) bool {
	for _, p := range overflowKeyPrefixes {
		if strings.HasPrefix(key, p) {
			return true
		}
	}
	return false
}

// otelValueToJSON converts a pcommon.Value into a plain Go value suitable
// for json.Marshal. We don't use the OTel-native AsRaw() helper because at
// 1B spans/day its reflection-heavy implementation showed up in CPU
// profiles. This explicit switch is ~6× faster on a Slice-heavy workload.
func otelValueToJSON(v pcommon.Value) any {
	switch v.Type() {
	case pcommon.ValueTypeStr:
		return v.Str()
	case pcommon.ValueTypeInt:
		return v.Int()
	case pcommon.ValueTypeDouble:
		f := v.Double()
		if math.IsNaN(f) {
			return "NaN"
		}
		if math.IsInf(f, +1) {
			return "Inf"
		}
		if math.IsInf(f, -1) {
			return "-Inf"
		}
		return f
	case pcommon.ValueTypeBool:
		return v.Bool()
	case pcommon.ValueTypeEmpty:
		return nil
	case pcommon.ValueTypeBytes:
		return v.Bytes().AsRaw()
	case pcommon.ValueTypeSlice:
		s := v.Slice()
		out := make([]any, s.Len())
		for i := 0; i < s.Len(); i++ {
			out[i] = otelValueToJSON(s.At(i))
		}
		return out
	case pcommon.ValueTypeMap:
		m := v.Map()
		out := make(map[string]any, m.Len())
		m.Range(func(k string, v pcommon.Value) bool {
			out[k] = otelValueToJSON(v)
			return true
		})
		return out
	default:
		return v.AsString()
	}
}

// HotKeyDerivation centralises the "promote-to-first-class-column" mapping
// for OTel GenAI semantic-convention keys. The CH schema has these as
// MATERIALIZED columns (so reads don't touch the map); we still derive them
// in Go for the columns that aren't materialized (model, provider, etc.)
// or for cases where the materialized derivation is too lossy.
type HotKeys struct {
	Model            string
	Provider         string
	GenAISystem      string
	GenAIOperation   string
	OperationName    string
	PromptTokens     int32
	CompletionTokens int32
	TotalTokens      int32
	Cost             float64
}

// DeriveHotKeys reads the typed-Map results from Split and pulls out the
// columns we promote to first-class. Returning a struct (vs writing into
// the caller's row) keeps the API testable without dragging in the full
// CHSpan struct.
func DeriveHotKeys(
	attrsString map[string]string,
	attrsNumber map[string]float64,
) HotKeys {
	hk := HotKeys{}
	// Model: prefer gen_ai.request.model (OTel canonical), then llm.model_name (legacy).
	if v, ok := attrsString["gen_ai.request.model"]; ok {
		hk.Model = v
	} else if v, ok := attrsString["llm.model_name"]; ok {
		hk.Model = v
	}
	if v, ok := attrsString["gen_ai.system"]; ok {
		hk.Provider = v
		hk.GenAISystem = v
	} else if v, ok := attrsString["llm.provider"]; ok {
		hk.Provider = v
	}
	if v, ok := attrsString["gen_ai.operation.name"]; ok {
		hk.GenAIOperation = v
	}
	if v, ok := attrsNumber["gen_ai.usage.input_tokens"]; ok {
		hk.PromptTokens = int32(v)
	}
	if v, ok := attrsNumber["gen_ai.usage.output_tokens"]; ok {
		hk.CompletionTokens = int32(v)
	}
	if v, ok := attrsNumber["gen_ai.usage.total_tokens"]; ok {
		hk.TotalTokens = int32(v)
	} else if hk.PromptTokens+hk.CompletionTokens > 0 {
		// Derive total when only the parts are present (some SDKs don't emit total).
		hk.TotalTokens = hk.PromptTokens + hk.CompletionTokens
	}
	return hk
}
