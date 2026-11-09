package logging

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
)

// This exercises ProcessResponse -> TraceEmitter -> emitToLog, without a
// flusher. The actual outbound key is reflected by the synthetic upstream into
// both error.message and error.code; regex-shaped stand-ins are not sufficient.
func TestOptBR4TraceOutboundCanary(t *testing.T) {
	const canary = "r4-outbound-platform-canary"
	const opaque = "r4-opaque-upstream-value"
	wantMessages := map[int]string{
		401: "authentication_error: contact support with the correlation id",
		403: "permission_error: contact support with the correlation id",
		429: "rate_limit_error: retry later; contact support with the correlation id",
		400: "invalid_request_error: check your request; contact support with the correlation id",
		503: "upstream_unavailable: retry later; contact support with the correlation id",
	}
	for _, status := range []int{401, 403, 429, 400, 503} {
		for _, mode := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata, models.ContentModeNone} {
			t.Run(fmt.Sprintf("%d/%s", status, mode), func(t *testing.T) {
				outbound := make(chan string, 1)
				rawErrors := make(chan error, 1)
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					_, _ = io.Copy(io.Discard, r.Body)
					key := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
					outbound <- key
					rawErrors <- &models.APIError{Status: status, ProviderStatus: status, Message: opaque + " " + key, Code: key, Type: "api_error"}
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(status)
					_ = json.NewEncoder(w).Encode(map[string]any{"error": map[string]any{
						"message": opaque + " " + key, "code": key, "type": "api_error",
					}})
				}))
				defer upstream.Close()
				adapter, err := openai.New("r4-platform", config.ProviderConfig{BaseURL: upstream.URL, APIKey: canary})
				if err != nil {
					t.Fatal(err)
				}
				defer adapter.Close()
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				_, upstreamErr := adapter.ChatCompletion(ctx, &models.ChatCompletionRequest{Model: "fixture", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"fixture"`)}}})
				if upstreamErr == nil {
					t.Fatal("real adapter did not return its upstream refusal")
				}
				// Inject below the adapter: trace projection must stand independently of R1.
				upstreamErr = optbRawSinkError(t, ctx, rawErrors)
				if upstreamErr == nil || !strings.Contains(upstreamErr.Error(), canary) {
					t.Fatal("fixture did not reflect an outbound key into the internal error")
				}
				select {
				case key := <-outbound:
					if key != canary {
						t.Fatal("outbound platform credential witness absent")
					}
				default:
					t.Fatal("no outbound request")
				}
				rc := models.AcquireRequestContext()
				defer rc.Release()
				rc.RequestID, rc.Model, rc.Provider = "req_optb_r4_trace", "fixture", "r4-platform"
				rc.Privacy.ContentMode = mode
				rc.AddError(upstreamErr)
				// Billing labels cannot prove ownership of this independent error.
				rc.AddAttempt(models.Attempt{CredentialType: models.CredentialBYOK, StatusCode: status, Outcome: models.OutcomeError})
				cfg := config.RequestLoggingConfig{Enabled: true, Workers: 1, BufferSize: 4}
				internalRecord := buildRecord(rc, cfg)
				before, err := json.Marshal(upstreamErr)
				if err != nil {
					t.Fatal(err)
				}
				var output bytes.Buffer
				previous := slog.Default()
				slog.SetDefault(slog.New(slog.NewJSONHandler(&output, nil)))
				defer slog.SetDefault(previous)
				plugin := New(cfg, nil)
				plugin.ProcessResponse(ctx, rc)
				plugin.Close() // Drain the real asynchronous emitter before inspecting attributes.
				var emitted map[string]any
				for _, line := range bytes.Split(bytes.TrimSpace(output.Bytes()), []byte("\n")) {
					var attrs map[string]any
					if json.Unmarshal(line, &attrs) != nil {
						t.Fatal("emitted trace was not structured JSON")
					}
					if attrs["msg"] == "request.trace" && attrs["request_id"] == rc.RequestID {
						if emitted != nil {
							t.Fatal("duplicate trace emission")
						}
						emitted = attrs
					}
				}
				if emitted == nil {
					t.Fatal("no request.trace was emitted")
				}
				if mode == models.ContentModeNone {
					if _, ok := emitted["error_message"]; ok {
						t.Error("none mode published error text")
					}
				} else if emitted["error_message"] != wantMessages[status]+" (correlation id: req_optb_r4_trace)" {
					t.Error("request.trace did not publish the exact categorical message")
				}
				if _, ok := emitted["error_code"]; ok {
					t.Error("request.trace published an upstream error code")
				}
				serialized, _ := json.Marshal(emitted)
				if bytes.Contains(serialized, []byte(canary)) || bytes.Contains(serialized, []byte(opaque)) {
					t.Error("outbound platform canary or opaque upstream text reached trace attributes")
				}
				after, err := json.Marshal(upstreamErr)
				if err != nil || !bytes.Equal(before, after) || rc.Errors[len(rc.Errors)-1] != upstreamErr {
					t.Error("trace projection changed the internal error used by retries/failover")
				}
				laterRecord := buildRecord(rc, cfg)
				laterRecord.LatencyMs = internalRecord.LatencyMs // Elapsed time is not an error mutation.
				if !reflect.DeepEqual(laterRecord, internalRecord) {
					t.Error("trace projection mutated the internal request snapshot")
				}
			})
		}
	}
}
