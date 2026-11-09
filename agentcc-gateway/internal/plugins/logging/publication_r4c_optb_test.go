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

// Self-contained on deployed ea4e8639: no capability/r4 helper or projection
// symbol is used. That tree must fail publication in BOTH sinks. At 90b80504
// these are GREEN controls; the P2-log RED is in the providers package.
func TestOptBR4CBothPublicationsOutboundCanary(t *testing.T) {
	const canary = "r4c-platform-outbound-canary"
	const opaque = "r4c-opaque-upstream-diagnostic"
	wantMessages := map[int]string{
		401: "authentication_error: contact support with the correlation id",
		403: "permission_error: contact support with the correlation id",
		429: "rate_limit_error: retry later; contact support with the correlation id",
		400: "invalid_request_error: check your request; contact support with the correlation id",
		503: "upstream_unavailable: retry later; contact support with the correlation id",
	}
	for _, status := range []int{401, 403, 429, 400, 503} {
		for _, mode := range []models.ContentMode{models.ContentModeFull, models.ContentModeMetadata} {
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
				adapter, err := openai.New("r4c-platform", config.ProviderConfig{BaseURL: upstream.URL, APIKey: canary})
				if err != nil {
					t.Fatal(err)
				}
				defer adapter.Close()
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				_, internalError := adapter.ChatCompletion(ctx, &models.ChatCompletionRequest{Model: "fixture", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"fixture"`)}}})
				if internalError == nil {
					t.Fatal("real adapter did not return its upstream refusal")
				}
				// Keep a raw-key witness at the sink layer so R1 cannot mask a sink regression.
				internalError = optbRawSinkError(t, ctx, rawErrors)
				if internalError == nil || !strings.Contains(internalError.Error(), canary) {
					t.Fatal("fixture did not reflect the actual outbound platform key into the internal error")
				}
				select {
				case sent := <-outbound:
					if sent != canary {
						t.Fatal("outbound platform credential witness differs")
					}
				default:
					t.Fatal("outbound platform credential witness absent")
				}
				rc := models.AcquireRequestContext()
				defer rc.Release()
				rc.RequestID, rc.Model, rc.Provider = "req_optb_r4c_both", "fixture", "r4c-platform"
				rc.Privacy.ContentMode = mode
				rc.AddError(internalError)
				// This misleading billing label must not authorize error publication.
				rc.AddAttempt(models.Attempt{CredentialType: models.CredentialBYOK, StatusCode: status, Outcome: models.OutcomeError})
				cfg := config.RequestLoggingConfig{Enabled: true, Workers: 1, BufferSize: 4}
				for _, sink := range []string{"trace", "webhook"} {
					t.Run(sink, func(t *testing.T) {
						record := buildRecord(rc, cfg)
						beforeRecord := buildRecord(rc, cfg)
						beforeRecord.LatencyMs = record.LatencyMs
						beforeError, err := json.Marshal(internalError)
						if err != nil {
							t.Fatal(err)
						}
						beforeHandling := rc.SnapshotV2()
						beforeText := internalError.Error()
						var emitted map[string]any
						var rawPayload []byte
						if sink == "trace" {
							var output bytes.Buffer
							previous := slog.Default()
							slog.SetDefault(slog.New(slog.NewJSONHandler(&output, nil)))
							defer slog.SetDefault(previous)
							emitter := NewTraceEmitter(cfg)
							emitter.Emit(record) // The caller retains this exact by-value input.
							emitter.Close()
							for _, line := range bytes.Split(bytes.TrimSpace(output.Bytes()), []byte("\n")) {
								var attrs map[string]any
								if json.Unmarshal(line, &attrs) != nil {
									t.Fatal("emitted trace is not structured JSON")
								}
								if attrs["msg"] == "request.trace" && attrs["request_id"] == rc.RequestID {
									if emitted != nil {
										t.Fatal("duplicate request.trace")
									}
									emitted, rawPayload = attrs, append([]byte(nil), line...)
								}
							}
						} else {
							batches := make(chan []byte, 1)
							webhook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
								body, _ := io.ReadAll(r.Body)
								batches <- body
								w.WriteHeader(http.StatusNoContent)
							}))
							defer webhook.Close()
							flusher := NewLogFlusher(webhook.URL, "", time.Hour, 4)
							flusher.EnqueueRequest(record, rc, LogEntryOptions{IncludeBodies: mode == models.ContentModeFull, ContentMode: mode})
							flushCtx, flushCancel := context.WithTimeout(context.Background(), 5*time.Second)
							defer flushCancel()
							flusher.Close(flushCtx)
							select {
							case rawPayload = <-batches:
							default:
								t.Fatal("webhook never received an export payload")
							}
							var batch struct {
								Logs []map[string]any `json:"logs"`
							}
							if json.Unmarshal(rawPayload, &batch) != nil || len(batch.Logs) != 1 || batch.Logs[0]["request_id"] != rc.RequestID {
								t.Fatal("webhook payload did not contain exactly the expected request")
							}
							emitted = batch.Logs[0]
						}
						if emitted == nil {
							t.Fatal("publication sink did not emit the request")
						}
						t.Run("internal_unchanged", func(t *testing.T) {
							afterError, err := json.Marshal(internalError)
							if err != nil || !bytes.Equal(beforeError, afterError) || internalError.Error() != beforeText || rc.Errors[len(rc.Errors)-1] != internalError {
								t.Error("E-2: publication changed the original internal error or its identity")
							}
							if !reflect.DeepEqual(record, beforeRecord) {
								t.Error("E-2: publication mutated the caller's input TraceRecord")
							}
							later := buildRecord(rc, cfg)
							later.LatencyMs = beforeRecord.LatencyMs
							if !reflect.DeepEqual(later, beforeRecord) || !reflect.DeepEqual(rc.SnapshotV2(), beforeHandling) {
								t.Error("E-2: publication changed the in-process request/attempt handling")
							}
						})
						t.Run("publication", func(t *testing.T) {
							present := bytes.Contains(rawPayload, []byte(canary))
							t.Logf("E-1 sink=%s outbound_witness=true canary_present=%t", sink, present)
							if present || bytes.Contains(rawPayload, []byte(opaque)) {
								t.Error("E-1: outbound platform canary or opaque upstream text reached the publication sink")
							}
							if emitted["error_message"] != wantMessages[status]+" (correlation id: req_optb_r4c_both)" {
								t.Error("publication did not contain the exact categorical diagnostic")
							}
							if _, ok := emitted["error_code"]; ok {
								t.Error("publication retained a provider-controlled error code")
							}
						})
					})
				}
			})
		}
	}
}
