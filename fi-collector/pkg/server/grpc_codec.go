package server

import (
	"google.golang.org/grpc/encoding"
	"google.golang.org/grpc/encoding/proto"
	"google.golang.org/grpc/mem"
	"google.golang.org/protobuf/protoadapt"
	"google.golang.org/protobuf/reflect/protoreflect"
)

// traceExportRequestName is the message checkProtoNesting walks.
const traceExportRequestName protoreflect.FullName = "opentelemetry.proto.collector.trace.v1.ExportTraceServiceRequest"

// nestingCheckedCodec is the gRPC server's codec. The Export handler
// (grpc_export.go) receives and sends raw bytes (rawExport), which it copies
// as they are. Any other message goes to grpc-go's proto codec, with the
// nesting limit (checkProtoNesting) on an ExportTraceServiceRequest. No
// served method decodes through it today: Export reads its message itself
// and refuses deep nesting with INVALID_ARGUMENT in its scan.
type nestingCheckedCodec struct {
	encoding.CodecV2
}

func newNestingCheckedCodec() nestingCheckedCodec {
	return nestingCheckedCodec{encoding.GetCodecV2(proto.Name)}
}

func (c nestingCheckedCodec) Unmarshal(data mem.BufferSlice, v any) error {
	if raw, ok := v.(*rawExport); ok {
		// A copy: grpc-go frees the message's buffers when this returns.
		raw.b = data.Materialize()
		return nil
	}
	if !isTraceExportRequest(v) {
		return c.CodecV2.Unmarshal(data, v)
	}
	// Join the message's buffers once and hand the same buffer to the proto
	// codec, which then decodes it without another copy.
	buf := data.MaterializeToBuffer(mem.DefaultBufferPool())
	defer buf.Free()
	if err := checkProtoNesting(buf.ReadOnlyData()); err != nil {
		return err
	}
	return c.CodecV2.Unmarshal(mem.BufferSlice{buf}, v)
}

func (c nestingCheckedCodec) Marshal(v any) (mem.BufferSlice, error) {
	if raw, ok := v.(*rawExport); ok {
		return mem.BufferSlice{mem.SliceBuffer(raw.b)}, nil
	}
	return c.CodecV2.Marshal(v)
}

// isTraceExportRequest reports whether v is an ExportTraceServiceRequest.
// pdata's generated type is internal, so it is matched by its protobuf name.
func isTraceExportRequest(v any) bool {
	var m protoreflect.ProtoMessage
	switch v := v.(type) {
	case protoadapt.MessageV1:
		m = protoadapt.MessageV2Of(v)
	case protoadapt.MessageV2:
		m = v
	default:
		return false
	}
	return m.ProtoReflect().Descriptor().FullName() == traceExportRequestName
}
