package logging

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

func optbExport(t *testing.T, rec TraceRecord, rc *models.RequestContext) map[string]any {
	t.Helper()
	wire := make(chan []byte, 1)
	sink := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		wire <- b
		w.WriteHeader(http.StatusOK)
	}))
	defer sink.Close()
	f := NewLogFlusher(sink.URL, "", time.Hour, 100)
	if rc == nil {
		f.Enqueue(rec)
	} else {
		f.EnqueueRequest(rec, rc, DefaultLogEntryOptions())
	}
	f.flush()
	select {
	case raw := <-wire:
		var payload struct {
			Logs []map[string]any `json:"logs"`
		}
		if err := json.Unmarshal(raw, &payload); err != nil {
			t.Fatal(err)
		}
		if len(payload.Logs) != 1 {
			t.Fatal("missing log export")
		}
		return payload.Logs[0]
	default:
		t.Fatal("flusher did not deliver payload")
		return nil
	}
}

// The fixture error is made from the outbound witness, before the adapter can
// scrub it. A missing dispatch must fail the test rather than hang the suite.
func optbRawSinkError(t *testing.T, ctx context.Context, raw <-chan error) error {
	t.Helper()
	select {
	case err := <-raw:
		return err
	case <-ctx.Done():
		t.Fatal("raw sink-layer witness was not delivered")
		return nil
	}
}

func optbAssertErrorFields(t *testing.T, entry map[string]any, canary string) {
	t.Helper()
	msg, _ := entry["error_message"].(string)
	if msg == "" || !strings.Contains(msg, "correlation id") || !strings.Contains(msg, "req_optb") {
		t.Error("hidden message lacks correlation and actionable instruction")
	}
	var visit func(any)
	visit = func(value any) {
		switch v := value.(type) {
		case map[string]any:
			for k, x := range v {
				if k == "error_message" || k == "error_code" || k == "error" {
					raw, _ := json.Marshal(x)
					for _, bad := range []string{canary, "opaque-upstream-value", "fixture.invalid", "X-Api-Key"} {
						if strings.Contains(string(raw), bad) {
							t.Error("C4: platform credential/upstream value reached an exported error field")
						}
					}
				}
				visit(x)
			}
		case []any:
			for _, x := range v {
				visit(x)
			}
		}
	}
	visit(entry)
}

func TestOptBC4NativeAdapterCanary(t *testing.T) {
	const key = "optb-outbound-native-fixture"
	for _, format := range []string{"openai", "azure", "anthropic", "gemini", "cohere"} {
		for _, status := range []int{401, 403, 429, 400, 503} {
			t.Run(fmt.Sprintf("%s/%d", format, status), func(t *testing.T) {
				outbound := make(chan bool, 1)
				rawErrors := make(chan error, 1)
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					// Registry discovery/health GETs are not authenticated inference
					// attempts and must not occupy the bounded wire witness channel.
					if r.Method != http.MethodPost {
						w.Header().Set("Content-Type", "application/json")
						_, _ = io.WriteString(w, `{"data":[]}`)
						return
					}
					found := false
					for _, vv := range r.Header {
						for _, v := range vv {
							found = found || strings.Contains(v, key)
						}
					}
					outbound <- found
					message := "opaque-upstream-value " + key + " https://fixture.invalid X-Api-Key"
					rawErrors <- &models.APIError{Status: status, ProviderStatus: status, Message: message, Code: "upstream-fixture-code", Type: "api_error"}
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(status)
					code := any("upstream-fixture-code")
					if format == "gemini" {
						code = status
					}
					_ = json.NewEncoder(w).Encode(map[string]any{"message": message, "error": map[string]any{"message": message, "type": "api_error", "code": code, "status": "INVALID_ARGUMENT"}})
				}))
				defer upstream.Close()
				registry, err := providers.NewRegistry(&config.Config{Providers: map[string]config.ProviderConfig{"fixture": {APIFormat: format, BaseURL: upstream.URL, APIKey: key, Managed: true}}})
				if err != nil {
					t.Fatal(err)
				}
				defer registry.Close()
				p, ok := registry.GetProvider("fixture")
				if !ok {
					t.Fatal("provider absent")
				}
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				_, err = p.ChatCompletion(ctx, &models.ChatCompletionRequest{Model: "fixture", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}}})
				if err == nil {
					t.Fatal("native adapter did not fail")
				}
				select {
				case sent := <-outbound:
					if !sent {
						t.Fatal("canary was not outbound platform key")
					}
				default:
					t.Fatal("no outbound request")
				}
				// Inject the pre-built raw witness below R1: this tests the sink's
				// independent projection even when the adapter scrubbed its own error.
				err = optbRawSinkError(t, ctx, rawErrors)
				if !strings.Contains(err.Error(), key) {
					t.Fatal("fixture did not reflect the outbound canary into its error")
				}
				rc := models.AcquireRequestContext()
				defer rc.Release()
				rc.RequestID = "req_optb_native"
				rc.Model = "fixture"
				rc.Provider = "fixture"
				rc.AddError(err)
				rc.AddAttempt(models.Attempt{AttemptID: models.AttemptID(rc.RequestID, 1), Index: 1, CredentialType: models.CredentialManaged, StatusCode: status, Outcome: models.OutcomeError, ErrorMessage: err.Error()})
				rec := buildRecord(rc, config.RequestLoggingConfig{Enabled: true})
				optbAssertErrorFields(t, optbExport(t, rec, rc), key)
			})
		}
	}
}

func TestOptBC4TransportStreamAndLegacyExports(t *testing.T) {
	const key = "optb-outbound-terminal-fixture"
	for _, kind := range []string{"transport", "stream_terminal", "v1", "untrusted_byok_label"} {
		t.Run(kind, func(t *testing.T) {
			// A local transport confirms the outbound key and supplies the reflected
			// value. Terminal/transport error injection happens after dispatch.
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = io.WriteString(w, r.Header.Get("X-Api-Key")) }))
			defer upstream.Close()
			req, _ := http.NewRequest("POST", upstream.URL, nil)
			req.Header.Set("X-Api-Key", key)
			resp, err := upstream.Client().Do(req)
			if err != nil {
				t.Fatal(err)
			}
			raw, err := io.ReadAll(resp.Body)
			resp.Body.Close()
			if err != nil || string(raw) != key {
				t.Fatal("outbound key witness absent")
			}
			diagnostic := "opaque-upstream-value " + string(raw) + " https://fixture.invalid X-Api-Key"
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.RequestID = "req_optb_terminal"
			rc.Model = "fixture"
			rc.IsStream = kind == "stream_terminal"
			rc.AddError(errors.New(diagnostic))
			owner := models.CredentialManaged
			if kind == "untrusted_byok_label" {
				owner = models.CredentialBYOK
			}
			rc.AddAttempt(models.Attempt{AttemptID: models.AttemptID(rc.RequestID, 1), Index: 1, CredentialType: owner, StatusCode: 0, Outcome: models.OutcomeError, ErrorMessage: diagnostic})
			rec := buildRecord(rc, config.RequestLoggingConfig{Enabled: true})
			rec.ErrorCode = diagnostic
			rec.ResponseBodyJSON = json.RawMessage(`{"error":{"message":"` + diagnostic + `"}}`)
			if kind == "v1" {
				rc = nil
			}
			optbAssertErrorFields(t, optbExport(t, rec, rc), key)
		})
	}
}
