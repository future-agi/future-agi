package server

import (
	"fmt"

	"google.golang.org/protobuf/encoding/protowire"
)

// maxDecodeNesting is the deepest message nesting accepted in an OTLP
// protobuf request, over HTTP or gRPC, counted from the
// ExportTraceServiceRequest (depth 0). An
// attribute value sits 5 levels down, and each nested array adds 2 (AnyValue,
// ArrayValue), so this allows about 5,000 nested arrays. It matches
// protobuf-go's default recursion limit and jsoniter's maxDepth, which already
// refuses JSON bodies nested more than 10,000 objects/arrays deep.
const maxDecodeNesting = 10000

var errNestingTooDeep = fmt.Errorf("nesting deeper than %d levels", maxDecodeNesting)

// checkProtoNesting refuses an ExportTraceServiceRequest body nested deeper
// than maxDecodeNesting, before it is decoded. pdata's protobuf decoder
// recurses once per level with no limit, so about 1.2M nested arrays (2.4M
// levels, an 11 MiB body) would overflow the 1 GB goroutine stack: a fatal
// error that no recover catches. The OTLP/HTTP handler calls it, and so does
// the gRPC server's codec (nestingCheckedCodec).
//
// The walk follows the OTLP trace schema with protowire and allocates nothing.
// Malformed protobuf is an error too: the walk cannot see past it, and the
// decoder refuses almost all of it anyway.
func checkProtoNesting(b []byte) error {
	return walkProtoNesting(b, pbRequest, 0)
}

// pbKind is an OTLP trace message type the walk descends into.
type pbKind uint8

const (
	pbRequest pbKind = iota // ExportTraceServiceRequest
	pbResourceSpans
	pbResource
	pbScopeSpans
	pbScope
	pbSpan
	pbEvent
	pbLink
	pbKeyValue
	pbAnyValue
	pbArrayValue
	pbKeyValueList
)

// pbChild is the message type of field num of a parent message, following the
// OTLP trace protos as pdata v1.20.0 decodes them. ok is false for scalar,
// string and bytes fields, for messages that cannot nest (Status), and for
// fields pdata does not know (it skips those without recursing).
func pbChild(parent pbKind, num protowire.Number) (child pbKind, ok bool) {
	switch parent {
	case pbRequest:
		return pbResourceSpans, num == 1
	case pbResourceSpans:
		switch num {
		case 1:
			return pbResource, true
		case 2, 1000: // 1000: the deprecated InstrumentationLibrarySpans
			return pbScopeSpans, true
		}
	case pbResource:
		return pbKeyValue, num == 1
	case pbScopeSpans:
		switch num {
		case 1:
			return pbScope, true
		case 2:
			return pbSpan, true
		}
	case pbScope:
		return pbKeyValue, num == 3
	case pbSpan:
		switch num {
		case 9:
			return pbKeyValue, true
		case 11:
			return pbEvent, true
		case 13:
			return pbLink, true
		}
	case pbEvent:
		return pbKeyValue, num == 3
	case pbLink:
		return pbKeyValue, num == 4
	case pbKeyValue:
		return pbAnyValue, num == 2
	case pbAnyValue:
		switch num {
		case 5:
			return pbArrayValue, true
		case 6:
			return pbKeyValueList, true
		}
	case pbArrayValue:
		return pbAnyValue, num == 1
	case pbKeyValueList:
		return pbKeyValue, num == 1
	}
	return 0, false
}

func walkProtoNesting(b []byte, kind pbKind, depth int) error {
	for len(b) > 0 {
		num, typ, n := protowire.ConsumeTag(b)
		if n < 0 {
			return protowire.ParseError(n)
		}
		b = b[n:]
		if typ == protowire.BytesType {
			if child, ok := pbChild(kind, num); ok {
				msg, m := protowire.ConsumeBytes(b)
				if m < 0 {
					return protowire.ParseError(m)
				}
				b = b[m:]
				if depth >= maxDecodeNesting {
					return errNestingTooDeep
				}
				if err := walkProtoNesting(msg, child, depth+1); err != nil {
					return err
				}
				continue
			}
		}
		m := protowire.ConsumeFieldValue(num, typ, b)
		if m < 0 {
			return protowire.ParseError(m)
		}
		b = b[m:]
	}
	return nil
}
