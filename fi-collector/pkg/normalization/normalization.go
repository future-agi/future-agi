// Package normalization applies only explicitly qualified producer semantics.
// The embedded registry is generated from the Python ingestion contract. Neither
// model names nor operation names alone identify a producer or a schema version.
package normalization

import (
	"encoding/json"
	"slices"
	"strings"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

type Profile struct {
	ID                string            `json:"id"`
	ScopeName         string            `json:"scope_name"`
	ScopeVersions     []string          `json:"scope_versions"`
	Operations        map[string]string `json:"operations"`
	SessionFallback   string            `json:"session_fallback"`
	SummaryOperations []string          `json:"summary_operations"`
}

type contract struct {
	Profiles          []Profile `json:"profiles"`
	UsagePrefixes     []string  `json:"usage_prefixes"`
	UsageKeys         []string  `json:"usage_keys"`
	EventKeys         []string  `json:"event_attribute_keys"`
	LinkKeys          []string  `json:"link_attribute_keys"`
	AdmissionKeys     []string  `json:"reserved_admission_keys"`
	AdmissionError    string    `json:"admission_error"`
	UnpricedUsageKeys []string  `json:"unpriced_usage_keys"`
}

var registry = func() contract {
	var r contract
	if err := json.Unmarshal([]byte(profileJSON), &r); err != nil {
		panic(err)
	}
	return r
}()

var kindKeys = []string{"fi.span.kind", "gen_ai.span.kind", "llm.request.type", "openinference.span.kind"}

func ProfileFor(scope pcommon.InstrumentationScope) *Profile {
	for i := range registry.Profiles {
		p := &registry.Profiles[i]
		if scope.Name() == p.ScopeName && slices.Contains(p.ScopeVersions, scope.Version()) {
			return p
		}
	}
	return nil
}

func String(attrs pcommon.Map, key string) string {
	if v, ok := attrs.Get(key); ok && v.Type() == pcommon.ValueTypeStr {
		return v.Str()
	}
	return ""
}

func NonAdditive(attrs pcommon.Map) bool { return String(attrs, "fi.usage.contribution") == "none" }

// Normalize preserves native trace/span/parent IDs and original operation and
// conversation attributes. It is idempotent, including across adapter passes.
func Normalize(attrs pcommon.Map, profile *Profile) {
	op := String(attrs, "gen_ai.operation.name")
	if profile != nil {
		attrs.PutStr("fi.normalization.profile", profile.ID)
		explicitKind := false
		for _, key := range kindKeys {
			if String(attrs, key) != "" {
				explicitKind = true
				break
			}
		}
		if kind := profile.Operations[op]; !explicitKind && kind != "" {
			attrs.PutStr("fi.span.kind", strings.ToUpper(kind))
			attrs.PutStr("fi.source.operation", op)
		}
		if session := String(attrs, profile.SessionFallback); String(attrs, "session.id") == "" && len(session) > 0 && len(session) <= 4096 {
			attrs.PutStr("session.id", session)
		}
		contribution, hasContribution := attrs.Get("fi.usage.contribution")
		if slices.Contains(profile.SummaryOperations, op) && (!hasContribution || (contribution.Type() == pcommon.ValueTypeStr && contribution.Str() == "none")) {
			attrs.PutStr("fi.usage.contribution", "none")
			attrs.PutStr("fi.usage.scope", "turn")
		}
	}
	if !NonAdditive(attrs) {
		return
	}
	// Do not insert while ranging a pdata map: append can invalidate its backing.
	var keys []string
	attrs.Range(func(key string, _ pcommon.Value) bool {
		match := slices.Contains(registry.UsageKeys, key)
		for _, prefix := range registry.UsagePrefixes {
			match = match || strings.HasPrefix(key, prefix)
		}
		if match {
			keys = append(keys, key)
		}
		return true
	})
	for _, key := range keys {
		value, _ := attrs.Get(key)
		raw := pcommon.NewValueEmpty()
		value.CopyTo(raw)
		attrs.Remove(key)
		raw.CopyTo(attrs.PutEmpty("fi.source.summary." + key))
	}
	attrs.PutStr("fi.cost.kind", "unknown")
}

// RequiresAdmission detects reserved v1 immutable-work/usage-plan semantics.
// Until durable admission is implemented, accepting these into last-write-wins
// storage would silently violate the sender's replay and accounting contract.
func RequiresAdmission(td ptrace.Traces) bool {
	rss := td.ResourceSpans()
	for i := 0; i < rss.Len(); i++ {
		scopes := rss.At(i).ScopeSpans()
		for j := 0; j < scopes.Len(); j++ {
			spans := scopes.At(j).Spans()
			for k := 0; k < spans.Len(); k++ {
				if v, ok := spans.At(k).Attributes().Get("fi.usage.contribution"); ok && (v.Type() != pcommon.ValueTypeStr || v.Str() != "none") {
					return true
				}
				for _, key := range registry.AdmissionKeys {
					if _, ok := spans.At(k).Attributes().Get(key); ok {
						return true
					}
				}
			}
		}
	}
	return false
}

func AdmissionError() string { return registry.AdmissionError }

// Prepare is shared by the authenticated ingestion budget pass and converter.
// Conversion also invokes it for callers using the converter without a server.
func Prepare(td ptrace.Traces) {
	rss := td.ResourceSpans()
	for i := 0; i < rss.Len(); i++ {
		scopes := rss.At(i).ScopeSpans()
		for j := 0; j < scopes.Len(); j++ {
			ss := scopes.At(j)
			profile := ProfileFor(ss.Scope())
			for k := 0; k < ss.Spans().Len(); k++ {
				span := ss.Spans().At(k)
				Normalize(span.Attributes(), profile)
				StoreDetails(span)
			}
		}
	}
}
