// Source-bound credential regressions. All credentials are synthetic.
package openai

import (
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Encode whole values, including surrounding bytes, independently of the scrubber.
func r5Spellings(key string) map[string]string {
	quoted, _ := json.Marshal(key)
	out := map[string]string{"exact": key, "json": string(quoted[1 : len(quoted)-1]), "percent": url.QueryEscape(key), "percent_lower": strings.ReplaceAll(url.QueryEscape(key), "%2F", "%2f"), "hex": hex.EncodeToString([]byte(key)), "HEX": strings.ToUpper(hex.EncodeToString([]byte(key)))}
	for offset := 0; offset < 3; offset++ {
		for name, enc := range map[string]*base64.Encoding{"std": base64.StdEncoding, "url": base64.URLEncoding, "rawstd": base64.RawStdEncoding, "rawurl": base64.RawURLEncoding} {
			out[fmt.Sprintf("%s_%d", name, offset)] = enc.EncodeToString([]byte(strings.Repeat("!", offset) + key + "suffix"))
		}
	}
	return out
}
func r5Text(err error) string {
	if err == nil {
		return ""
	}
	out := err.Error()
	var api *models.APIError
	if errors.As(err, &api) {
		out += "|" + api.Message + "|" + api.Code + "|" + api.Type
	}
	return out
}
func TestR5ExactCredentialBoundary(t *testing.T) {
	// The quote, backslash and slash make raw/JSON/percent variants distinct.
	for _, owner := range []string{"platform", "byok"} {
		for spelling, echo := range r5Spellings(`opaque.R5/"\+Credential-abcdefghijklmnopqrstuvwxyz`) {
			for _, mode := range []string{"http", "stream_http", "stream_terminal"} {
				t.Run(owner+"/"+mode+"/"+spelling, func(t *testing.T) {
					key := `opaque.R5/"\+Credential-abcdefghijklmnopqrstuvwxyz`
					other := "other-owner-credential-survives"
					message := "diagnostic-before " + echo + " diagnostic-after " + other
					var witnessed bool
					upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						if r.Header.Get("Authorization") != "Bearer "+key {
							t.Error("wrong sent key")
						}
						witnessed = true
						if mode != "stream_terminal" {
							w.WriteHeader(400)
							json.NewEncoder(w).Encode(map[string]any{"error": map[string]any{"message": message, "code": echo, "type": echo}})
							return
						}
						w.Header().Set("Content-Type", "text/event-stream")
						b, _ := json.Marshal(map[string]any{"error": map[string]any{"message": message, "code": echo, "type": echo}})
						fmt.Fprintf(w, "data: %s\n\n", b)
					}))
					defer upstream.Close()
					cfg := config.ProviderConfig{BaseURL: upstream.URL, APIKey: key, MaxConcurrent: 2, DefaultTimeout: time.Second, Headers: map[string]string{}}

					p, err := New(owner, cfg)
					if err != nil {
						t.Fatal(err)
					}
					defer p.Close()

					ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
					defer cancel()
					request := &models.ChatCompletionRequest{Model: "unit-model", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}}}
					if mode == "http" {
						_, err = p.ChatCompletion(ctx, request)
					} else {
						chunks, errs := p.StreamChatCompletion(ctx, request)
						for chunks != nil || errs != nil {
							select {
							case _, ok := <-chunks:
								if !ok {
									chunks = nil
								}
							case e, ok := <-errs:
								if !ok {
									errs = nil
								} else if e != nil {
									err = e
								}
							case <-ctx.Done():
								t.Fatal("stream did not terminate")
							}
						}
					}
					if !witnessed {
						t.Fatal("no outbound credential witness")
					}
					if err == nil {
						t.Fatal("upstream refusal lost")
					}
					text := r5Text(err)
					if strings.Contains(text, echo) || strings.Contains(text, key) {
						t.Fatal("sent credential survived in error fields")
					}
					if !strings.Contains(text, "diagnostic-before") || !strings.Contains(text, "diagnostic-after "+other) {
						t.Fatalf("noncredential provider diagnostics changed: %q", text)
					}
					if owner == "byok" && spelling == "exact" {
						var api *models.APIError
						if errors.As(err, &api) && api.Message != "diagnostic-before [REDACTED] diagnostic-after "+other {
							t.Errorf("BYOK text must differ only by sent key, got %q", api.Message)
						}
					}
				})
			}
		}
	}
}
