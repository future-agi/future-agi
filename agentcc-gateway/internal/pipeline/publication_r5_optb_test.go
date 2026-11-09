package pipeline_test

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
)

func r5LogSpellings() map[string]func(string) string {
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

// INT-R1-04: observe the actual Engine.Process warning, not a logging-plugin
// record. The adapter receives the planted credential over loopback HTTP.
func TestOptBR5PipelinePublication(t *testing.T) {
	const key = `W2 opaque "sent\key" /+?=<> 0123456789 not-live`
	for _, owner := range []string{"platform", "unbound", "byok"} {
		for name, spell := range r5LogSpellings() {
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
					p, err := openai.New("fixture", config.ProviderConfig{APIFormat: "openai", BaseURL: up.URL, APIKey: key})
					if err != nil {
						t.Fatal(err)
					}
					defer p.Close()
					rc := models.AcquireRequestContext()
					defer rc.Release()
					rc.RequestID, rc.Provider, rc.Model = "r5-log", "fixture", "fixture"
					// Deliberately misleading final state must not exempt an unbound error.
					rc.CredentialSource = "org"
					rc.Metadata["billing_mode"] = "byok"
					rc.Request = &models.ChatCompletionRequest{Model: "fixture"}
					var original *models.APIError
					var before models.APIError
					var captured bytes.Buffer
					old := slog.Default()
					slog.SetDefault(slog.New(slog.NewJSONHandler(&captured, nil)))
					defer slog.SetDefault(old)
					got := pipeline.NewEngine().Process(context.Background(), rc, func(ctx context.Context, rc *models.RequestContext) error {
						_, failure := p.ChatCompletion(ctx, rc.Request)
						if !errors.As(failure, &original) {
							t.Fatalf("expected APIError, got %T", failure)
						}
						if owner == "platform" {
							original.BindPublicationCredentialOwner(models.CredentialManaged)
						}
						if owner == "byok" {
							original.BindPublicationCredentialOwner(models.CredentialBYOK)
						}
						before = *original
						return original
					})
					if calls != 1 || got != original || len(rc.Errors) != 1 || rc.Errors[0] != original || !reflect.DeepEqual(*original, before) {
						t.Fatal("publication changed internal error identity or value, or no actual adapter call")
					}
					found := false
					for _, line := range strings.Split(captured.String(), "\n") {
						var row map[string]any
						if json.Unmarshal([]byte(line), &row) != nil || row["msg"] != "provider call failed" {
							continue
						}
						found = true
						text, ok := row["error"].(string)
						if !ok || text == "" {
							t.Fatal("warning has no error text")
						}
						if strings.Contains(text, spell(key)) || strings.Contains(text, key) {
							t.Error("actual pipeline warning retained sent credential")
						}
						if owner == "byok" && !strings.Contains(text, "benign diagnostic") {
							t.Error("BYOK diagnostic was lost")
						}
						if owner != "byok" && strings.Contains(text, "benign diagnostic") {
							t.Error("platform or unbound error escaped closed projection")
						}
					}
					if !found {
						t.Fatal("no actual provider-call-failed warning")
					}
				})
			}
		}
	}
}
