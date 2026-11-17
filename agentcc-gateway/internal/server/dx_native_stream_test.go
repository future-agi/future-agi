package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
)

// Exercise the interface contract even when a stream adapter returns an HTTP
// failure with a nil error (the built-in adapters currently return Go errors).
type dxNativeStream struct {
	providers.AnthropicNativeProvider
	providers.GenAINativeProvider
	status int
	closed bool
}

func (s *dxNativeStream) Read(p []byte) (int, error) { return 0, io.EOF }
func (s *dxNativeStream) Close() error               { s.closed = true; return nil }
func (s *dxNativeStream) response() io.ReadCloser {
	return &dxErrorStream{Reader: strings.NewReader(`{"error":{"message":"synthetic refusal"}}`), owner: s}
}
func (s *dxNativeStream) StreamAnthropicMessage(context.Context, []byte, map[string]string) (io.ReadCloser, int, error) {
	return s.response(), s.status, nil
}
func (s *dxNativeStream) StreamGenerateContent(context.Context, string, []byte, map[string]string) (io.ReadCloser, int, error) {
	return s.response(), s.status, nil
}

type dxErrorStream struct {
	*strings.Reader
	owner *dxNativeStream
}

func (r *dxErrorStream) Close() error { r.owner.closed = true; return nil }

func TestDXR2NativeStreamHTTPFailure(t *testing.T) {
	for _, format := range []string{"anthropic", "gemini"} {
		for _, status := range []int{401, 403, 500} {
			t.Run(fmt.Sprintf("%s/%d", format, status), func(t *testing.T) {
				srv := dxNativeGateway(t, format, 200)
				h := srv.handlers
				h.engine = pipeline.NewEngine()
				rc := models.AcquireRequestContext()
				defer rc.Release()
				rc.Provider, rc.Model = "native", "probe"
				rc.Metadata["billing_mode"] = "byok"
				rc.IsStream = true
				stream := &dxNativeStream{status: status}
				w := httptest.NewRecorder()
				if format == "anthropic" {
					h.handleAnthropicStream(context.Background(), w, rc, stream, requestview.Anthropic([]byte(`{"messages":[]}`)), nil)
				} else {
					h.handleGenAIStream(context.Background(), w, rc, stream, "probe", requestview.Gemini([]byte(`{"contents":[]}`), "probe", true), nil)
				}
				wantStatus, wantCode := status, fmt.Sprintf("provider_%d", status)
				if status == 401 || status == 403 {
					wantStatus, wantCode = 502, "provider_auth_failed"
				}
				var envelope struct {
					Error struct {
						Code    json.RawMessage `json:"code"`
						Message string          `json:"message"`
						Details []struct {
							Reason string `json:"reason"`
						} `json:"details"`
					} `json:"error"`
				}
				if err := json.Unmarshal(w.Body.Bytes(), &envelope); err != nil {
					t.Fatal(err)
				}
				code := ""
				if format == "anthropic" {
					_ = json.Unmarshal(envelope.Error.Code, &code)
				} else if len(envelope.Error.Details) == 1 {
					code = envelope.Error.Details[0].Reason
				}
				if w.Code != wantStatus || code != wantCode || !strings.Contains(envelope.Error.Message, `"native"`) || !stream.closed {
					t.Fatalf("status=%d code=%s body=%s closed=%v", w.Code, code, w.Body.String(), stream.closed)
				}
			})
		}
	}
}
