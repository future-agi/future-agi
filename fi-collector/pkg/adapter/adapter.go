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
	"strconv"
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
	CostUserSet      bool
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
	hk.Model = firstString(attrsString, modelNameKeys)
	hk.Provider = firstString(attrsString, providerKeys)
	// gen_ai_system is its own CH column: keep the raw deprecated key verbatim.
	hk.GenAISystem = attrsString["gen_ai.system"]
	if v, ok := attrsString["gen_ai.operation.name"]; ok {
		hk.GenAIOperation = v
	}
	if v, ok := firstNumber(attrsString, attrsNumber, inputTokenKeys); ok {
		hk.PromptTokens = int32(v)
	}
	if v, ok := firstNumber(attrsString, attrsNumber, outputTokenKeys); ok {
		hk.CompletionTokens = int32(v)
	}
	if v, ok := firstNumber(attrsString, attrsNumber, totalTokenKeys); ok {
		hk.TotalTokens = int32(v)
	} else if hk.PromptTokens+hk.CompletionTokens > 0 {
		// Derive total when only the parts are present (some SDKs don't emit total).
		hk.TotalTokens = hk.PromptTokens + hk.CompletionTokens
	}
	// User-provided cost — Django calculate_cost precedence (otel.py:1666):
	// cost.total wins; else input+output when either side present. An explicit
	// user 0 is respected (CostUserSet gates the token-pricing fallback later).
	//
	// Three precise deviations from Django here, all a consequence of
	// firstNumber returning ok=false on an unparseable string instead of
	// Django's int()/float() coercion (which raised and was caught to 0):
	//
	//  1. Unparseable cost string on BOTH sides, or on the only side present
	//     (e.g. cost.total alone is garbage): we fall through to token-based
	//     pricing below. Django: cost=0, and CostUserSet-equivalent stays
	//     true (still user-set, just zero) — i.e. Django never falls back to
	//     token pricing here, we do.
	//  2. One side unparseable, the other side parseable (e.g. cost.input is
	//     garbage but cost.output parses): we keep the parseable side's value
	//     as the user cost (in + out, with the bad side contributing 0).
	//     Django: the whole sum resolves to 0 (its int()/float() coercion
	//     wraps the combined expression, not each side independently).
	//  3. TotalTokens: when no total-tokens alias is present, we synthesize
	//     TotalTokens = PromptTokens + CompletionTokens (see below). Django
	//     never derived this — Django-backfilled rows have TotalTokens 0/NULL
	//     in cases where a live (fi-collector-written) row for the same
	//     shape has the derived sum.
	if v, ok := firstNumber(attrsString, attrsNumber, costTotalKeys); ok {
		hk.Cost = v
		hk.CostUserSet = true
	} else {
		in, okIn := firstNumber(attrsString, attrsNumber, costInputKeys)
		out, okOut := firstNumber(attrsString, attrsNumber, costOutputKeys)
		if okIn || okOut {
			hk.Cost = in + out
			hk.CostUserSet = true
		}
	}
	return hk
}

// Attribute alias tables — mirror (priority order preserved; providerKeys
// documents its one deliberate reordering) of the Django AttributeRegistry
// (futureagi/tracer/utils/semantic_conventions.py, class AttributeAliases) that
// the retired OTLP converter used. PRIORITY ORDER IS LOAD-BEARING: it must match
// Django so live rows agree with backfilled rows. Do not reorder.
var (
	modelNameKeys = []string{
		"llm.model_name",        // FI / OpenInference
		"gen_ai.request.model",  // OTel GenAI (request)
		"gen_ai.response.model", // OTel GenAI (response — resolved model names)
		"llm.request.model",     // OpenLLMetry
	}
	// Django checked SpanAttributes.PROVIDER_NAME ("gen_ai.provider.name")
	// explicitly BEFORE the registry aliases (otel.py:1556), so it leads here.
	// "llm.provider" was never a Django alias but is kept last so rows written
	// by older fi-collector builds keep resolving the same way.
	providerKeys = []string{
		"gen_ai.provider.name", // OTel GenAI — current canonical
		"llm.system",           // FI
		"gen_ai.system",        // OTel GenAI — deprecated
		"llm.vendor",           // OpenLLMetry
		"llm.provider",         // legacy fi-collector
	}
	inputTokenKeys = []string{
		"llm.token_count.prompt",    // FI / OpenInference
		"gen_ai.usage.input_tokens", // OTel GenAI
		"llm.usage.prompt_tokens",   // OpenLLMetry
	}
	outputTokenKeys = []string{
		"llm.token_count.completion",
		"gen_ai.usage.output_tokens",
		"llm.usage.completion_tokens",
	}
	totalTokenKeys = []string{
		"llm.token_count.total",
		"gen_ai.usage.total_tokens",
		"llm.usage.total_tokens",
	}
	costTotalKeys  = []string{"gen_ai.cost.total", "llm.cost.total"}
	costInputKeys  = []string{"gen_ai.cost.input", "llm.cost.prompt"}
	costOutputKeys = []string{"gen_ai.cost.output", "llm.cost.completion"}
)

// firstString returns the value of the first alias present in attrsString.
func firstString(attrsString map[string]string, keys []string) string {
	for _, k := range keys {
		if v, ok := attrsString[k]; ok && v != "" {
			return v
		}
	}
	return ""
}

// firstNumber walks aliases in priority order. For each key it checks the
// typed-number map first, then falls back to parsing a string-typed value —
// Django's get_attribute read the raw attribute dict and int()/float()-coerced,
// so numeric-as-string payloads must not be dropped here.
func firstNumber(
	attrsString map[string]string,
	attrsNumber map[string]float64,
	keys []string,
) (float64, bool) {
	for _, k := range keys {
		if v, ok := attrsNumber[k]; ok {
			return v, true
		}
		if s, ok := attrsString[k]; ok {
			if v, err := strconv.ParseFloat(strings.TrimSpace(s), 64); err == nil {
				return v, true
			}
		}
	}
	return 0, false
}
