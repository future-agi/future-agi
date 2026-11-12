package anthropicfmt

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestToolResultMediaErrorCodeScope(t *testing.T) {
	for _, code := range []string{"messages_tool_result_media_unsupported", "messages_routing_translation_unsupported", "invalid_request"} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%v", code, stream), func(t *testing.T) {
				err := models.ErrBadRequest(code, "synthetic refusal")
				w := httptest.NewRecorder()
				WriteAPIError(w, err)
				if w.Code != http.StatusBadRequest || w.Header().Get("Content-Type") != "application/json" {
					t.Fatalf("wrong HTTP error status=%d content-type=%q", w.Code, w.Header().Get("Content-Type"))
				}
				data := w.Body.Bytes()
				if stream {
					frame := string(StreamErrorEvent(err))
					if !strings.HasPrefix(frame, "event: error\ndata: ") || !strings.HasSuffix(frame, "\n\n") {
						t.Fatalf("invalid SSE frame: %q", frame)
					}
					data = []byte(strings.TrimSuffix(strings.TrimPrefix(frame, "event: error\ndata: "), "\n\n"))
				}
				var got map[string]any
				if e := json.Unmarshal(data, &got); e != nil {
					t.Fatal(e)
				}
				wantError := map[string]any{"type": "invalid_request_error", "message": err.Message}
				if code == "messages_tool_result_media_unsupported" {
					wantError["code"] = code
				}
				if !reflect.DeepEqual(got, map[string]any{"type": "error", "error": wantError}) {
					t.Fatalf("wrong Messages envelope: %s", data)
				}
			})
		}
	}
}

// The media-specific code must not replace the existing provider-attributed
// HTTP envelope or its Retry-After precedence during shared-file composition.
func TestToolResultMediaProviderEnvelopeUnchanged(t *testing.T) {
	metadata := map[string]any{"provider": "synthetic-provider", "upstream_code": "synthetic_busy"}
	for _, seconds := range []int{0, 7} {
		t.Run(fmt.Sprintf("retry-seconds=%d", seconds), func(t *testing.T) {
			err := &models.APIError{
				Status: http.StatusTooManyRequests, Code: "upstream_provider_error",
				Message: "synthetic provider refusal", Metadata: metadata,
				RetryAfterSec: seconds, RetryAfter: "11",
			}
			w := httptest.NewRecorder()
			WriteAPIError(w, err)
			wantRetry := "11"
			if seconds > 0 {
				wantRetry = "7"
			}
			var got map[string]any
			if e := json.Unmarshal(w.Body.Bytes(), &got); e != nil {
				t.Fatal(e)
			}
			want := map[string]any{
				"type": "error",
				"error": map[string]any{
					"type": "rate_limit_error", "message": err.Message,
					"code": err.Code, "metadata": metadata,
				},
			}
			if w.Code != http.StatusTooManyRequests || w.Header().Get("Content-Type") != "application/json" || w.Header().Get("Retry-After") != wantRetry || !reflect.DeepEqual(got, want) {
				t.Fatalf("provider envelope changed: status=%d headers=%v body=%s", w.Code, w.Header(), w.Body)
			}
		})
	}
}
