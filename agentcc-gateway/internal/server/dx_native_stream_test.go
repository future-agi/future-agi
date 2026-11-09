package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http/httptest"
	"reflect"
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
				// A billing label alone cannot prove an org-owned credential.
				wantStatus := status
				if status == 401 || status == 403 {
					wantStatus = 502
				}
				message := w4ClosedMessage(status, rc.RequestID)
				want := map[string]any{"type": "error", "error": map[string]any{"type": "api_error", "message": message}}
				if format == "gemini" {
					want = map[string]any{"error": map[string]any{"code": float64(wantStatus), "message": message, "status": "INTERNAL"}}
				}
				var got map[string]any
				if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
					t.Fatal(err)
				}
				if w.Code != wantStatus || !reflect.DeepEqual(got, want) || !stream.closed || w.Flushed || w.Header().Get("Content-Type") != "application/json" {
					t.Fatalf("status=%d body=%s closed=%v; want %d %#v", w.Code, w.Body.String(), stream.closed, wantStatus, want)
				}
			})
		}
	}
}
