package server

import (
	"context"
	"errors"
	"fmt"
	"github.com/future-agi/future-agi/fi-collector/pkg/normalization"

	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

// pricePreparedExport checks the actual post-header/post-auth/post-routing
// request before it allocates converted rows. Stamping only adds four scalar
// resource entries (reserved before decoding in price); their strings borrow
// header/auth backing. This pass reserves their repeated wire representation
// and conversion/encoding memory, without serializing a second request body.
// Old-generation attempts never reach conversion and never shrink this lease.
func (s *Server) pricePreparedExport(ctx context.Context, td ptrace.Traces, held *exportWork) *rejection {
	normalization.Prepare(td)
	shape, err := scanPreparedTraces(td)
	if err != nil {
		var se *scanError
		if errors.As(err, &se) {
			return &rejection{reason: se.reason, tooLarge: se.tooLarge, msg: "prepared request: " + se.msg}
		}
		return &rejection{reason: "malformed", msg: err.Error()}
	}
	if shape.rowJSONBytes > s.limits.maxEncoded {
		return &rejection{reason: "stored_size", tooLarge: true, msg: fmt.Sprintf("payload too large: final authenticated row JSON is about %d MiB, over the %d MiB limit; send smaller batches", shape.rowJSONBytes>>20, s.limits.maxEncoded>>20)}
	}
	if held == nil {
		return nil
	} // in-process API only; every wire handler has leases
	p := shape.price()
	handler, rows := max(p.handler, held.work.size()), max(p.rows, held.rows.size())
	if handler+rows > s.work.size {
		return &rejection{reason: "budget", tooLarge: true, msg: "payload too large: final authenticated shape exceeds FI_INGEST_BUDGET_MIB; send smaller batches"}
	}
	// Both grow calls share the original request's leaseBudget. Holding a
	// partial reservation can time out (503), never deadlock indefinitely or
	// turn a permanently oversized shape into a retryable refusal.
	if err := held.work.grow(ctx, handler-held.work.size()); err != nil {
		return rejectWorkBusy
	}
	if err := held.rows.grow(ctx, rows-held.rows.size()); err != nil {
		return rejectWorkBusy
	}
	return nil
}

// scanPreparedTraces uses the same row/curated accounting as the wire scanners
// on already-leased pdata. No maps, strings or serialized payloads are built.
// The original scan remains the high-water price for overwritten fields and
// JSON-decoder temporaries; this pass adds only a larger final shape.
func scanPreparedTraces(td ptrace.Traces) (otlpShape, error) {
	var s otlpShape
	rss := td.ResourceSpans()
	for i := 0; i < rss.Len(); i++ {
		rs := rss.At(i)
		s.resourceSpans++
		var ra rsAccum
		if err := s.pdataAttributes(rs.Resource().Attributes(), kvResource, &ra, nil); err != nil {
			return s, err
		}
		s.pdataOptionalString(rs.SchemaUrl())
		scopes := rs.ScopeSpans()
		for j := 0; j < scopes.Len(); j++ {
			ss := scopes.At(j)
			s.scopeSpans++
			s.pdataOptionalString(ss.SchemaUrl())
			s.pdataOptionalString(ss.Scope().Name())
			s.pdataOptionalString(ss.Scope().Version())
			if err := s.pdataAttributes(ss.Scope().Attributes(), kvOther, nil, nil); err != nil {
				return s, err
			}
			spans := ss.Spans()
			for k := 0; k < spans.Len(); k++ {
				span := spans.At(k)
				s.spans++
				sp := spanAccum{root: span.ParentSpanID().IsEmpty()}
				sp.nameJSON = s.pdataOptionalString(span.Name())
				sp.json += sp.nameJSON
				sp.json += s.pdataOptionalString(span.Status().Message())
				s.pdataOptionalString(span.TraceState().AsRaw())
				if err := s.pdataAttributes(span.Attributes(), kvSpan, &ra, &sp); err != nil {
					return s, err
				}
				events := span.Events()
				for e := 0; e < events.Len(); e++ {
					ev := events.At(e)
					s.events++
					s.pdataOptionalString(ev.Name())
					extra := spanEventJSON(int64(len(ev.Name())))
					sp.json += extra
					s.hotCopyBytes += extra
					if err := s.pdataAttributes(ev.Attributes(), kvEvent, nil, &sp); err != nil {
						return s, err
					}
				}
				links := span.Links()
				for l := 0; l < links.Len(); l++ {
					link := links.At(l)
					s.links++
					s.pdataOptionalString(link.TraceState().AsRaw())
					if err := s.pdataAttributes(link.Attributes(), kvOther, nil, nil); err != nil {
						return s, err
					}
				}
				s.endSpan(&ra, &sp)
			}
		}
		if err := s.endResourceSpans(&ra); err != nil {
			return s, err
		}
	}
	return s, nil
}

// Empty optional fields have no decoded allocation and are omitted by the
// protobuf writer. Do not create phantom top-ups for otherwise unchanged input.
func (s *otlpShape) pdataOptionalString(v string) int64 {
	if v == "" {
		return 0
	}
	n, _ := s.protoString([]byte(v))
	return n + 2
}

func (s *otlpShape) pdataKV(key string, v pcommon.Value) (kvInfo, error) {
	if err := s.addKeyValue(); err != nil {
		return kvInfo{}, err
	}
	plain, html := s.protoString([]byte(key))
	value, err := s.pdataValue(v)
	return kvInfo{keyJSON: plain, keyHTML: html, keyRaw: int64(len(key)), class: classifyKey([]byte(key)), promoted: promotedStringKey([]byte(key)), val: value}, err
}
func (s *otlpShape) pdataAttributes(attrs pcommon.Map, ctx kvContext, rs *rsAccum, sp *spanAccum) error {
	var err error
	attrs.Range(func(key string, v pcommon.Value) bool {
		var kv kvInfo
		kv, err = s.pdataKV(key, v)
		if err != nil {
			return false
		}
		s.addKV(ctx, kv, rs, sp)
		return true
	})
	return err
}
func (s *otlpShape) pdataValue(v pcommon.Value) (valueInfo, error) {
	switch v.Type() {
	case pcommon.ValueTypeStr:
		p, h := s.protoString([]byte(v.Str()))
		return valueInfo{json: p + 2, htmlJSON: h + 2, raw: int64(len(v.Str())), scalar: true, isString: true, lastStringJSON: p + 2}, nil
	case pcommon.ValueTypeEmpty:
		return valueInfo{json: 4, htmlJSON: 4, scalar: true}, nil
	case pcommon.ValueTypeBool, pcommon.ValueTypeInt, pcommon.ValueTypeDouble:
		return valueInfo{json: numberJSON, htmlJSON: numberJSON, scalar: true, lastStringJSON: -1}, nil
	case pcommon.ValueTypeBytes:
		raw := int64(v.Bytes().Len())
		s.bytesValues += raw
		n := 4*((raw+2)/3) + 2
		return valueInfo{json: n, htmlJSON: n, raw: raw, lastStringJSON: -1}, nil
	case pcommon.ValueTypeSlice:
		s.containers++
		out := valueInfo{json: 2, htmlJSON: 2, lastStringJSON: -1}
		values := v.Slice()
		for i := 0; i < values.Len(); i++ {
			if err := s.addArrayValue(); err != nil {
				return out, err
			}
			e, err := s.pdataValue(values.At(i))
			if err != nil {
				return out, err
			}
			out.json += e.json + 1
			out.htmlJSON += e.htmlJSON + 1
			out.raw += e.raw
		}
		return out, nil
	case pcommon.ValueTypeMap:
		s.containers++
		out := valueInfo{json: 2, htmlJSON: 2, lastStringJSON: -1}
		var err error
		v.Map().Range(func(k string, v pcommon.Value) bool {
			var kv kvInfo
			kv, err = s.pdataKV(k, v)
			if err != nil {
				return false
			}
			out.json += kv.json()
			out.htmlJSON += kv.htmlJSON()
			out.raw += kv.keyRaw + kv.val.raw
			return true
		})
		return out, err
	}
	return valueInfo{}, errMalformed("unsupported prepared attribute value")
}
