package normalization

import (
	"math"
	"unicode/utf8"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

const (
	MaxEvents       = 64
	MaxLinks        = 32
	MaxDetailString = 4096
	MaxDetailBytes  = 16 << 10
)

// Only bounded, allowlisted structural scalar fields are newly persisted.
// Prompt/tool/error text and tracestate are deliberately not copied. This is
// not a generic PII scrubber; producer-side capture policy remains required.
func detailAttributes(attrs pcommon.Map, allowed []string, budget *int) map[string]any {
	out := map[string]any{}
	// Registry order, not wire order, makes truncation deterministic.
	for _, key := range allowed {
		value, ok := attrs.Get(key)
		if !ok {
			continue
		}
		size := len(key) + 32
		var raw any
		switch value.Type() {
		case pcommon.ValueTypeStr:
			if len(value.Str()) > MaxDetailString || !utf8.ValidString(value.Str()) {
				continue
			}
			size += len(value.Str())
			raw = value.Str()
		case pcommon.ValueTypeBool:
			raw = value.Bool()
		case pcommon.ValueTypeInt:
			raw = value.Int()
		case pcommon.ValueTypeDouble:
			if math.IsNaN(value.Double()) || math.IsInf(value.Double(), 0) {
				continue
			}
			raw = value.Double()
		default:
			continue
		}
		if size > *budget {
			continue
		}
		out[key] = raw
		*budget -= size
	}
	return out
}

// StoreDetails uses existing JSON storage for links (fi.otel.links in
// attributes_extra). This stores causality; it does NOT authorize resolving a
// link or offer navigation across projects. No database migration is needed.
func StoreDetails(span ptrace.Span) {
	budget := MaxDetailBytes
	qualified := String(span.Attributes(), "fi.normalization.profile") != ""
	events := span.Events()
	index := 0
	dropped := 0
	events.RemoveIf(func(ev ptrace.SpanEvent) bool {
		index++
		if qualified && (index > MaxEvents || len(ev.Name()) > MaxDetailString || len(ev.Name()) > budget) {
			dropped++
			return true
		}
		if qualified {
			budget -= len(ev.Name())
		}
		before := ev.Attributes().Len()
		attrs := map[string]any{}
		if index <= MaxEvents {
			attrs = detailAttributes(ev.Attributes(), registry.EventKeys, &budget)
		}
		_ = ev.Attributes().FromRaw(attrs)
		ev.SetDroppedAttributesCount(ev.DroppedAttributesCount() + uint32(before-len(attrs)))
		return false
	})
	if dropped > 0 {
		span.SetDroppedEventsCount(span.DroppedEventsCount() + uint32(dropped))
	}
	if span.DroppedEventsCount() > 0 {
		span.Attributes().PutInt("fi.otel.dropped_events_count", int64(span.DroppedEventsCount()))
	}

	links := span.Links()
	rows := make([]any, 0, min(links.Len(), MaxLinks))
	for i := 0; i < links.Len() && i < MaxLinks; i++ {
		link := links.At(i)
		if link.TraceID().IsEmpty() || link.SpanID().IsEmpty() {
			continue
		}
		attrs := detailAttributes(link.Attributes(), registry.LinkKeys, &budget)
		rows = append(rows, map[string]any{
			"trace_id": link.TraceID().String(), "span_id": link.SpanID().String(),
			"attributes":               attrs,
			"dropped_attributes_count": int64(link.DroppedAttributesCount()) + int64(link.Attributes().Len()-len(attrs)),
		})
	}
	if len(rows) > 0 {
		_ = span.Attributes().PutEmptySlice("fi.otel.links").FromRaw(rows)
	} else {
		span.Attributes().Remove("fi.otel.links")
	}
	missing := int64(links.Len()-len(rows)) + int64(span.DroppedLinksCount())
	if missing > 0 {
		span.Attributes().PutInt("fi.otel.dropped_links_count", missing)
	}
}
