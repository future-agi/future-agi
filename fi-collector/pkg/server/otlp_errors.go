package server

import (
	"net/http"

	statuspb "google.golang.org/genproto/googleapis/rpc/status"
	"google.golang.org/grpc/codes"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

// writeOTLPError answers a failed OTLP/HTTP request.
//
// The OTLP/HTTP specification requires the body of every 4xx and 5xx response to
// be a google.rpc.Status message, and OTLP exporters read it to report why an
// export failed. The Status is encoded like the request: protobuf for
// application/x-protobuf and for any request whose media type is not known yet
// (including an unsupported one), JSON for application/json. The HTTP status line
// stays authoritative: clients ignore Status.code.
//
// Headers set on w before the call (Retry-After, Accept) are kept.
func writeOTLPError(w http.ResponseWriter, contentType string, httpCode int, message string) {
	st := &statuspb.Status{Code: int32(grpcCodeForHTTP(httpCode)), Message: message}
	var (
		body []byte
		err  error
	)
	mediaType := "application/x-protobuf"
	if contentType == "application/json" {
		mediaType = "application/json"
		body, err = protojson.Marshal(st)
	} else {
		body, err = proto.Marshal(st)
	}
	if err != nil {
		// A Status of one int and one string cannot fail to encode; fall back to
		// the plain status text rather than an empty body if it ever does.
		http.Error(w, message, httpCode)
		return
	}
	w.Header().Set("Content-Type", mediaType)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(httpCode)
	_, _ = w.Write(body)
}

// grpcCodeForHTTP is the Status.code that corresponds to an HTTP failure. The
// OTLP specification does not use it for HTTP, but a client that logs it should
// see the same retryability as the HTTP status carries.
func grpcCodeForHTTP(httpCode int) codes.Code {
	switch httpCode {
	case http.StatusTooManyRequests:
		return codes.ResourceExhausted
	case http.StatusServiceUnavailable, http.StatusBadGateway, http.StatusGatewayTimeout:
		return codes.Unavailable
	case http.StatusBadRequest, http.StatusMethodNotAllowed, http.StatusRequestEntityTooLarge, http.StatusUnsupportedMediaType:
		return codes.InvalidArgument
	default:
		return codes.Internal
	}
}
