package otel

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	otelpkg "github.com/futureagi/agentcc-gateway/internal/otel"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// rcWithMetadata builds a request context carrying caller-supplied dimensions.
func rcWithMetadata(kv map[string]string) *models.RequestContext {
	rc := &models.RequestContext{
		RequestID:      "req-1",
		Model:          "gpt-4o",
		Metadata:       map[string]string{},
		RequestHeaders: http.Header{},
	}
	for k, v := range kv {
		rc.Metadata[k] = v
		rc.CustomMetadataKeys = append(rc.CustomMetadataKeys, k)
	}
	return rc
}

func attrString(t *testing.T, span *otelpkg.Span, key string) string {
	t.Helper()
	v, ok := span.Attributes[key]
	if !ok {
		t.Fatalf("attribute %q missing; have %v", key, attrKeys(span))
	}
	s, ok := v.(string)
	if !ok {
		t.Fatalf("attribute %q = %T, want string", key, v)
	}
	return s
}

func attrKeys(span *otelpkg.Span) []string {
	keys := make([]string, 0, len(span.Attributes))
	for k := range span.Attributes {
		keys = append(keys, k)
	}
	return keys
}

// The JSON object is what the platform parses, so flattening must never
// replace it.
func TestAttachMetadata_EmitsBlobAndFlattenedKeys(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	rc := rcWithMetadata(map[string]string{"customer_id": "cust-4711", "module": "reviews"})

	span := spanFor(t, p, rc)

	var blob map[string]string
	if err := json.Unmarshal([]byte(attrString(t, span, "metadata")), &blob); err != nil {
		t.Fatalf("metadata blob is not a JSON object: %v", err)
	}
	if blob["customer_id"] != "cust-4711" || blob["module"] != "reviews" {
		t.Errorf("blob = %v, want both caller keys", blob)
	}

	if got := attrString(t, span, "agentcc.metadata.customer_id"); got != "cust-4711" {
		t.Errorf("flattened customer_id = %q, want %q", got, "cust-4711")
	}
	if got := attrString(t, span, "agentcc.metadata.module"); got != "reviews" {
		t.Errorf("flattened module = %q, want %q", got, "reviews")
	}
}

// Turning flattening off must leave the blob — it is the channel the platform
// reads, not an extra.
func TestAttachMetadata_DisabledKeepsBlob(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	p.metadataAttributes = false
	rc := rcWithMetadata(map[string]string{"customer_id": "cust-4711"})

	span := spanFor(t, p, rc)

	if _, ok := span.Attributes["metadata"]; !ok {
		t.Error("metadata blob dropped when flattening is disabled")
	}
	if _, ok := span.Attributes["agentcc.metadata.customer_id"]; ok {
		t.Error("flattened key emitted while flattening is disabled")
	}
}

// Go randomises map iteration; without the sort, which keys survive the cap
// would differ between two identical requests.
func TestAttachMetadata_CapIsDeterministicAndCounted(t *testing.T) {
	kv := make(map[string]string, maxMetadataPairs*2)
	for i := 0; i < maxMetadataPairs*2; i++ {
		kv[fmt.Sprintf("k%02d", i)] = "v"
	}

	var first []string
	for run := 0; run < 5; run++ {
		p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
		rc := rcWithMetadata(kv)
		rc.RequestID = fmt.Sprintf("req-%d", run)
		span := spanFor(t, p, rc)

		var blob map[string]string
		if err := json.Unmarshal([]byte(attrString(t, span, "metadata")), &blob); err != nil {
			t.Fatal(err)
		}
		if len(blob) != maxMetadataPairs {
			t.Fatalf("kept %d pairs, want %d", len(blob), maxMetadataPairs)
		}
		if got := span.Attributes[metadataDroppedAttr]; got != maxMetadataPairs {
			t.Errorf("%s = %v, want %d", metadataDroppedAttr, got, maxMetadataPairs)
		}

		keys := make([]string, 0, len(blob))
		for k := range blob {
			keys = append(keys, k)
		}
		if first == nil {
			first = keys
			continue
		}
		if len(first) != len(keys) {
			t.Fatalf("run %d kept a different number of keys", run)
		}
		for _, k := range first {
			if _, ok := blob[k]; !ok {
				t.Fatalf("run %d dropped %q that run 0 kept — truncation is not deterministic", run, k)
			}
		}
	}
	// Sorted truncation keeps the lowest keys.
	for _, k := range first {
		if k >= fmt.Sprintf("k%02d", maxMetadataPairs) {
			t.Errorf("kept %q, want the first %d sorted keys", k, maxMetadataPairs)
		}
	}
}

// Nothing was dropped, so the counter must be absent rather than zero.
func TestAttachMetadata_NoDroppedAttributeWhenNothingDropped(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	span := spanFor(t, p, rcWithMetadata(map[string]string{"a": "1"}))

	if _, ok := span.Attributes[metadataDroppedAttr]; ok {
		t.Errorf("%s present with nothing dropped", metadataDroppedAttr)
	}
}

func TestAttachMetadata_OversizeKeyDroppedValueTruncated(t *testing.T) {
	longKey := strings.Repeat("k", maxMetadataKeyLen+1)
	longValue := strings.Repeat("v", maxMetadataValueLen+50)

	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	span := spanFor(t, p, rcWithMetadata(map[string]string{longKey: "x", "ok": longValue}))

	if _, ok := span.Attributes[metadataAttrPrefix+longKey]; ok {
		t.Error("oversize key was exported; a shortened key would be a different key")
	}
	if got := attrString(t, span, metadataAttrPrefix+"ok"); len(got) != maxMetadataValueLen {
		t.Errorf("value length = %d, want %d", len(got), maxMetadataValueLen)
	}
}

// A value cut mid-rune makes proto.Marshal reject the whole batch.
func TestAttachMetadata_ValueTruncatesOnRuneBoundary(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	// 3 bytes per rune, so the byte cap lands mid-rune.
	span := spanFor(t, p, rcWithMetadata(map[string]string{"k": strings.Repeat("界", maxMetadataValueLen)}))

	got := attrString(t, span, metadataAttrPrefix+"k")
	if len(got)%3 != 0 {
		t.Errorf("value truncated mid-rune: %d bytes", len(got))
	}
}

func TestAttachMetadata_NoAttributesWhenNoCallerKeys(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	span := spanFor(t, p, rcWithMetadata(nil))

	if _, ok := span.Attributes["metadata"]; ok {
		t.Error("metadata attribute emitted for a request with no caller dimensions")
	}
}

func TestNewHeaderMatcher(t *testing.T) {
	tests := []struct {
		name     string
		patterns []string
		header   string
		want     bool
	}{
		{name: "no patterns captures nothing", patterns: nil, header: "x-acme-tenant"},
		{name: "exact match", patterns: []string{"x-acme-tenant"}, header: "x-acme-tenant", want: true},
		{name: "exact match is case-insensitive", patterns: []string{"X-Acme-Tenant"}, header: "x-acme-tenant", want: true},
		{name: "exact miss", patterns: []string{"x-acme-tenant"}, header: "x-acme-region"},
		{name: "prefix wildcard", patterns: []string{"x-acme-*"}, header: "x-acme-region", want: true},
		{name: "prefix wildcard does not match outside namespace", patterns: []string{"x-acme-*"}, header: "x-other-region"},
		{name: "bare star matches everything", patterns: []string{"*"}, header: "x-acme-region", want: true},
		{name: "whitespace trimmed", patterns: []string{"  x-acme-tenant  "}, header: "x-acme-tenant", want: true},
		{name: "empty pattern ignored", patterns: []string{"", "   "}, header: "x-acme-tenant"},

		// The denylist wins over any allowlist, wildcard or exact.
		{name: "wildcard cannot reach authorization", patterns: []string{"*"}, header: "authorization"},
		{name: "x-star cannot reach x-api-key", patterns: []string{"x-*"}, header: "x-api-key"},
		{name: "x-star cannot reach x-goog-api-key", patterns: []string{"x-*"}, header: "x-goog-api-key"},
		{name: "naming a credential exactly still denies it", patterns: []string{"authorization"}, header: "authorization"},
		{name: "cookie denied", patterns: []string{"*"}, header: "cookie"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			m := newHeaderMatcher(tt.patterns)
			if m == nil {
				if tt.want {
					t.Fatal("matcher is nil but a match was expected")
				}
				return
			}
			if got := m.match(tt.header); got != tt.want {
				t.Errorf("match(%q) = %v, want %v", tt.header, got, tt.want)
			}
		})
	}
}

// The load-bearing case: cloneRequestHeaders copies x-api-key into
// Authorization, so a live credential is always present on the context.
func TestAttachRequestHeaders_WildcardNeverExportsCredentials(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	p.captureHeaders = newHeaderMatcher([]string{"*"})

	rc := rcWithMetadata(nil)
	rc.RequestHeaders.Set("Authorization", "Bearer sk-live-secret")
	rc.RequestHeaders.Set("X-Api-Key", "sk-live-secret")
	rc.RequestHeaders.Set("Cookie", "session=abc")
	rc.RequestHeaders.Set("X-Acme-Tenant", "acme-eu")

	span := spanFor(t, p, rc)

	for _, denied := range []string{"authorization", "x-api-key", "cookie"} {
		if _, ok := span.Attributes[headerAttrPrefix+denied]; ok {
			t.Errorf("credential header %q reached the span", denied)
		}
	}
	for k, v := range span.Attributes {
		if s, ok := v.(string); ok && strings.Contains(s, "sk-live-secret") {
			t.Errorf("attribute %q leaked the credential value", k)
		}
	}
	if got := attrString(t, span, headerAttrPrefix+"x-acme-tenant"); got != "acme-eu" {
		t.Errorf("x-acme-tenant = %q, want %q", got, "acme-eu")
	}
}

func TestAttachRequestHeaders_DisabledByDefault(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)

	rc := rcWithMetadata(nil)
	rc.RequestHeaders.Set("X-Acme-Tenant", "acme-eu")

	span := spanFor(t, p, rc)

	for k := range span.Attributes {
		if strings.HasPrefix(k, headerAttrPrefix) {
			t.Errorf("header %q captured with no capture_headers configured", k)
		}
	}
}

// semconv says string array; Split would route that to attributes_extra, which
// is not queryable — so repeated values are joined instead.
func TestAttachRequestHeaders_RepeatedValuesJoinAsOneString(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	p.captureHeaders = newHeaderMatcher([]string{"x-acme-*"})

	rc := rcWithMetadata(nil)
	rc.RequestHeaders.Add("X-Acme-Tag", "a")
	rc.RequestHeaders.Add("X-Acme-Tag", "b")

	span := spanFor(t, p, rc)

	if got := attrString(t, span, headerAttrPrefix+"x-acme-tag"); got != "a,b" {
		t.Errorf("x-acme-tag = %q, want %q", got, "a,b")
	}
}

func TestAttachRequestHeaders_ValuesAreRedacted(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	p.captureHeaders = newHeaderMatcher([]string{"x-acme-*"})
	p.SetRedactor(privacy.New("patterns", []privacy.PatternConfig{
		{Name: "email", Pattern: `[\w.]+@[\w.]+`},
	}))

	rc := rcWithMetadata(nil)
	rc.RequestHeaders.Set("X-Acme-Operator", "ops@milestone.com")

	span := spanFor(t, p, rc)

	if got := attrString(t, span, headerAttrPrefix+"x-acme-operator"); strings.Contains(got, "ops@milestone.com") {
		t.Errorf("header value was exported unredacted: %q", got)
	}
}

// Metadata is dimensional data the caller grouped by; redacting an id that
// merely looks like an email would silently break their grouping.
func TestAttachMetadata_ValuesAreNotRedacted(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	p.SetRedactor(privacy.New("patterns", []privacy.PatternConfig{
		{Name: "email", Pattern: `[\w.]+@[\w.]+`},
	}))

	span := spanFor(t, p, rcWithMetadata(map[string]string{"tenant": "ops@milestone.com"}))

	if got := attrString(t, span, metadataAttrPrefix+"tenant"); got != "ops@milestone.com" {
		t.Errorf("tenant = %q, want the value the caller sent", got)
	}
}

// rcWithExtras builds a request context carrying already-snapshotted body
// fields, as applyCallerExtras would have left them.
func rcWithExtras(kv map[string]any) *models.RequestContext {
	rc := rcWithMetadata(nil)
	rc.CallerExtras = kv
	return rc
}

// The headline case: standard OpenAI parameters the request struct has no field
// for arrive as body extras, and must be filterable.
func TestAttachBodyExtras_ExportsModernOpenAIParams(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	span := spanFor(t, p, rcWithExtras(map[string]any{
		"reasoning_effort":    "high",
		"parallel_tool_calls": true,
		"store":               false,
	}))

	if got := attrString(t, span, bodyAttrPrefix+"reasoning_effort"); got != "high" {
		t.Errorf("reasoning_effort = %q, want %q", got, "high")
	}
	if got := span.Attributes[bodyAttrPrefix+"parallel_tool_calls"]; got != true {
		t.Errorf("parallel_tool_calls = %v, want true", got)
	}
	if got, ok := span.Attributes[bodyAttrPrefix+"store"]; !ok || got != false {
		t.Errorf("store = %v (present %v), want false", got, ok)
	}
}

// Numbers must stay numbers: the platform routes numeric attributes into a
// column a range filter can use, and a stringified one is dead weight there.
func TestAttachBodyExtras_NumbersKeepTheirType(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	span := spanFor(t, p, rcWithExtras(map[string]any{
		"routing_weight": int64(3),
		"threshold":      0.75,
	}))

	if got := span.Attributes[bodyAttrPrefix+"routing_weight"]; got != int64(3) {
		t.Errorf("routing_weight = %#v, want int64(3)", got)
	}
	if got := span.Attributes[bodyAttrPrefix+"threshold"]; got != 0.75 {
		t.Errorf("threshold = %#v, want 0.75", got)
	}
}

func TestAttachBodyExtras_DisabledExportsNothing(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	p.bodyAttributes = false
	span := spanFor(t, p, rcWithExtras(map[string]any{"reasoning_effort": "high"}))

	for k := range span.Attributes {
		if strings.HasPrefix(k, bodyAttrPrefix) || k == bodyDroppedAttr {
			t.Errorf("attribute %q emitted while body attributes are disabled", k)
		}
	}
}

// Fields the handler refused still have to be visible as a count, or a silent
// drop reads as "the caller sent nothing".
func TestAttachBodyExtras_HandlerDropsAreCounted(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	rc := rcWithExtras(map[string]any{"reasoning_effort": "high"})
	rc.CallerExtrasDropped = 2

	span := spanFor(t, p, rc)

	if got := span.Attributes[bodyDroppedAttr]; got != 2 {
		t.Errorf("%s = %v, want 2", bodyDroppedAttr, got)
	}
}

func TestAttachBodyExtras_CapIsDeterministicAndCounted(t *testing.T) {
	kv := make(map[string]any, maxMetadataPairs*2)
	for i := 0; i < maxMetadataPairs*2; i++ {
		kv[fmt.Sprintf("f%02d", i)] = "v"
	}

	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	span := spanFor(t, p, rcWithExtras(kv))

	kept := 0
	for k := range span.Attributes {
		if strings.HasPrefix(k, bodyAttrPrefix) {
			kept++
		}
	}
	if kept != maxMetadataPairs {
		t.Fatalf("kept %d fields, want %d", kept, maxMetadataPairs)
	}
	if got := span.Attributes[bodyDroppedAttr]; got != maxMetadataPairs {
		t.Errorf("%s = %v, want %d", bodyDroppedAttr, got, maxMetadataPairs)
	}
	// Sorted truncation keeps the lowest keys.
	for i := maxMetadataPairs; i < maxMetadataPairs*2; i++ {
		if _, ok := span.Attributes[bodyAttrPrefix+fmt.Sprintf("f%02d", i)]; ok {
			t.Errorf("kept f%02d, want only the first %d sorted keys", i, maxMetadataPairs)
		}
	}
}

// Unlike metadata, body extras are arbitrary payload rather than a dimension
// the caller picked, so the privacy config applies.
func TestAttachBodyExtras_StringValuesAreRedacted(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)
	p.SetRedactor(privacy.New("patterns", []privacy.PatternConfig{
		{Name: "key", Pattern: `sk-[A-Za-z0-9]+`},
	}))

	span := spanFor(t, p, rcWithExtras(map[string]any{"upstream_hint": "use sk-live1234abcd now"}))

	if got := attrString(t, span, bodyAttrPrefix+"upstream_hint"); strings.Contains(got, "sk-live1234abcd") {
		t.Errorf("body value exported unredacted: %q", got)
	}
}

// The provenance guarantee, pinned. The translation layer writes its own state
// into the canonical request's Extra map (tool_name_mapping and friends), and
// the exporter must read the handler's snapshot instead — so gateway internals
// are excluded by where the data came from, not by a list of our key names that
// someone has to remember to update.
func TestAttachBodyExtras_NeverReadsRequestExtra(t *testing.T) {
	p := NewWithExporter(otelpkg.NoopExporter{}, 1.0, true)

	rc := rcWithExtras(nil)
	rc.Request = &models.ChatCompletionRequest{
		Model: "gpt-4o",
		Extra: map[string]json.RawMessage{
			"tool_name_mapping":         json.RawMessage(`{"a":"b"}`),
			"anthropic_thinking_config": json.RawMessage(`{"type":"enabled"}`),
			"gemini_tool_call_id_map":   json.RawMessage(`{"c":"d"}`),
		},
	}

	span := spanFor(t, p, rc)

	for k := range span.Attributes {
		if strings.HasPrefix(k, bodyAttrPrefix) {
			t.Errorf("gateway-internal key %q leaked from Request.Extra onto the span", k)
		}
	}
}
