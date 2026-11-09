package otel

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	otelpkg "github.com/futureagi/agentcc-gateway/internal/otel"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"
)

func r5SpanSpellings() map[string]func(string) string {
	out := map[string]func(string) string{
		"exact":     func(s string) string { return s },
		"json":      func(s string) string { b, _ := json.Marshal(s); return string(b[1 : len(b)-1]) },
		"percent":   url.QueryEscape,
		"hex-lower": func(s string) string { return hex.EncodeToString([]byte(s)) },
		"hex-upper": func(s string) string { return strings.ToUpper(hex.EncodeToString([]byte(s))) },
	}
	for _, enc := range []struct {
		name string
		e    *base64.Encoding
	}{{"std", base64.StdEncoding}, {"url", base64.URLEncoding}} {
		for offset := 0; offset < 3; offset++ {
			e, n := enc.e, offset
			out[fmt.Sprintf("base64-%s-%d", enc.name, n)] = func(s string) string { return e.EncodeToString([]byte(strings.Repeat("~", n) + s + "!")) }
		}
	}
	return out
}

type r5SpanExporters []otelpkg.SpanExporter

func (es r5SpanExporters) Export(spans []*otelpkg.Span) error {
	for _, e := range es {
		if err := e.Export(spans); err != nil {
			return err
		}
	}
	return nil
}
func (es r5SpanExporters) Shutdown() error {
	for _, e := range es {
		if err := e.Shutdown(); err != nil {
			return err
		}
	}
	return nil
}

// INT-R1-05: exporter output, not just the mutable span. The real OTLP encoder
// is exercised to assert Status.Message independently of error.message.
func TestOptBR5OTelPublication(t *testing.T) {
	const key = `W2 opaque "sent\key" /+?=<> 0123456789 not-live`
	for _, owner := range []string{"platform", "unbound", "byok"} {
		for name, spell := range r5SpanSpellings() {
			for _, field := range []string{"message", "code", "type"} {
				t.Run(owner+"/"+name+"/"+field, func(t *testing.T) {
					key := key
					if owner == "byok" {
						key = "tenant " + key
					}
					calls := 0
					up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						calls++
						if r.Header.Get("Authorization") != "Bearer "+key {
							t.Error("wrong outbound credential")
						}
						detail := map[string]string{"message": "benign diagnostic", "code": "fixture", "type": "invalid_request_error"}
						detail[field] += " " + spell(key)
						w.WriteHeader(400)
						_ = json.NewEncoder(w).Encode(map[string]any{"error": detail})
					}))
					defer up.Close()
					adapter, err := openai.New("fixture", config.ProviderConfig{APIFormat: "openai", BaseURL: up.URL, APIKey: key})
					if err != nil {
						t.Fatal(err)
					}
					defer adapter.Close()
					_, failure := adapter.ChatCompletion(context.Background(), &models.ChatCompletionRequest{Model: "fixture"})
					var original *models.APIError
					if !errors.As(failure, &original) || calls != 1 {
						t.Fatal("no actual upstream APIError")
					}
					if owner == "platform" {
						original.BindPublicationCredentialOwner(models.CredentialManaged)
					}
					if owner == "byok" {
						original.BindPublicationCredentialOwner(models.CredentialBYOK)
					}
					before := *original
					received := make(chan *tracepb.TracesData, 1)
					collector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						raw, readErr := io.ReadAll(r.Body)
						var td tracepb.TracesData
						if readErr != nil || proto.Unmarshal(raw, &td) != nil {
							t.Error("invalid OTLP export")
							w.WriteHeader(400)
							return
						}
						received <- &td
						w.WriteHeader(200)
					}))
					defer collector.Close()
					otlp, err := otelpkg.NewOTLPExporter(otelpkg.OTLPOptions{Endpoint: collector.URL, ServiceName: "r5"})
					if err != nil {
						t.Fatal(err)
					}
					defer otlp.Shutdown()
					var stdout bytes.Buffer
					capture := &captureExporter{}
					exporters := r5SpanExporters{capture, otelpkg.NewStdoutExporter(&stdout), otlp}
					p := NewWithExporter(exporters, 1, true)
					p.includeBodies = false
					rc := newTestRC()
					rc.CredentialSource, rc.Metadata["billing_mode"] = "org", "byok"
					rc.Errors = []error{original}
					p.ProcessRequest(context.Background(), rc)
					p.ProcessResponse(context.Background(), rc)
					if err := exporters.Shutdown(); err != nil {
						t.Fatal(err)
					}
					spans := capture.Spans()
					if len(spans) != 1 || spans[0].Status != "ERROR" {
						t.Fatal("missing real error span")
					}
					var printed otelpkg.Span
					if err := json.Unmarshal(bytes.TrimSpace(stdout.Bytes()), &printed); err != nil {
						t.Fatal(err)
					}
					texts := map[string]string{"memory": fmt.Sprint(spans[0].Attributes["error.message"]), "stdout": fmt.Sprint(printed.Attributes["error.message"])}
					select {
					case td := <-received:
						count := 0
						for _, resource := range td.ResourceSpans {
							for _, scope := range resource.ScopeSpans {
								for _, span := range scope.Spans {
									count++
									if span.GetStatus().GetCode() != tracepb.Status_STATUS_CODE_ERROR {
										t.Error("missing OTLP ERROR status")
									}
									texts["Status.Message"] = span.GetStatus().GetMessage()
									for _, attr := range span.Attributes {
										if attr.Key == "error.message" {
											texts["otlp.attribute"] = attr.Value.GetStringValue()
										}
									}
								}
							}
						}
						if count != 1 || len(texts) != 4 {
							t.Fatal("missing actual OTLP span or error attribute")
						}
					default:
						t.Fatal("no collector export after shutdown")
					}
					for sink, text := range texts {
						if text == "" {
							t.Errorf("%s has no diagnostic", sink)
						}
						if strings.Contains(text, spell(key)) || strings.Contains(text, key) {
							t.Errorf("%s retained sent credential", sink)
						}
						if owner == "byok" && !strings.Contains(text, "benign diagnostic") {
							t.Errorf("%s lost BYOK diagnostic", sink)
						}
						if owner != "byok" && strings.Contains(text, "benign diagnostic") {
							t.Errorf("%s trusted unbound ownership", sink)
						}
					}
					if rc.Errors[0] != original || !reflect.DeepEqual(*original, before) {
						t.Error("export mutated internal error")
					}
				})
			}
		}
	}
}
