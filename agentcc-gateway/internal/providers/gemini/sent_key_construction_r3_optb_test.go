// R1 construction canary: the stub encodes only the credential it receives.
// All credentials are synthetic; all HTTP traffic stays on loopback.
package gemini

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

func TestOptBR3AdapterSentKeyConstruction(t *testing.T) {
	const key = `I2.opaque/"\+Credential-abcdefghijklmnopqrstuvwxyz~?<>`
	for _, variant := range []string{"gemini", "vertex"} {
		for _, spelling := range r3G1Spellings() {
			for _, mode := range []string{"http", "stream_http", "stream_terminal"} {
				t.Run(variant+"/"+mode+"/"+spelling.name, func(t *testing.T) {
					witness := make(chan string, 1)
					upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						sent := r.Header.Get("X-Goog-Api-Key")
						if variant == "vertex" {
							sent = strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
						}
						witness <- sent
						if sent != key {
							t.Error("stub did not receive the configured credential")
						}
						echo := spelling.encode(sent)
						message := "diagnostic-before " + echo + " diagnostic-after unrelated-owner-value"
						body := map[string]any{"error": map[string]any{"message": message, "status": echo, "code": 400}}
						w.Header().Set("Content-Type", "application/json")
						if mode != "stream_terminal" {
							w.WriteHeader(http.StatusBadRequest)
							_ = json.NewEncoder(w).Encode(body)
							return
						}
						w.Header().Set("Content-Type", "text/event-stream")
						b, _ := json.Marshal(body)
						_, _ = fmt.Fprintf(w, "data: %s\n\n", b)
					}))
					defer upstream.Close()
					cfg := config.ProviderConfig{BaseURL: upstream.URL, APIKey: key, DefaultTimeout: 2 * time.Second, MaxConcurrent: 1}

					p, err := New("i2-fixture", cfg)
					if err != nil {
						t.Fatal(err)
					}
					defer p.Close()
					// Local transport, production Vertex bearer branch; no ADC or token network.
					p.vertexAI = variant == "vertex"
					ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
					defer cancel()
					req := &models.ChatCompletionRequest{Model: "unit-model", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}}}
					if mode == "http" {
						_, err = p.ChatCompletion(ctx, req)
					} else {
						req.Stream = true
						chunks, errs := p.StreamChatCompletion(ctx, req)
						for chunks != nil || errs != nil {
							select {
							case _, ok := <-chunks:
								if !ok {
									chunks = nil
								}
							case next, ok := <-errs:
								if !ok {
									errs = nil
								} else if next != nil {
									err = next
								}
							case <-ctx.Done():
								t.Fatal("adapter stream did not terminate")
							}
						}
					}
					select {
					case sent := <-witness:
						if sent != key {
							t.Fatal("sent-key witness differs")
						}
					default:
						t.Fatal("adapter never sent a credential to the stub")
					}
					// No RequestContext, owner binding, logger or publication helper runs here.
					r3AssertConstructedError(t, err, key, spelling.encode(key))
				})
			}
		}
	}
}

type r3Spelling struct {
	name   string
	encode func(string) string
}

func r3G1Spellings() []r3Spelling {
	jsonText := func(s string) string { b, _ := json.Marshal(s); return string(b[1 : len(b)-1]) }
	fullPercent := func(s string) string {
		var b strings.Builder
		for _, v := range []byte(s) {
			fmt.Fprintf(&b, "%%%02X", v)
		}
		return b.String()
	}
	mixedPercent := func(s string) string {
		b := []byte(fullPercent(s))
		for i := 0; i < len(b); i += 6 {
			b[i+1] = byte(strings.ToLower(string(b[i+1]))[0])
			b[i+2] = byte(strings.ToLower(string(b[i+2]))[0])
		}
		return string(b)
	}
	unicodeJSON := func(s string) string {
		var b strings.Builder
		for _, v := range []byte(s) {
			fmt.Fprintf(&b, `\u%04x`, v)
		}
		return b.String()
	}
	out := []r3Spelling{
		{"exact", func(s string) string { return s }},
		{"json", jsonText},
		{"json_slash", func(s string) string { return strings.ReplaceAll(jsonText(s), "/", `\/`) }},
		{"json_unicode", unicodeJSON},
		{"json_wrapped", func(s string) string { return jsonText(jsonText(s)) }},
		{"json_unicode_wrapped", func(s string) string { return jsonText(unicodeJSON(s)) }},
		{"percent_query", url.QueryEscape},
		{"percent_path", url.PathEscape},
		{"percent_full_upper", fullPercent},
		{"percent_full_lower", func(s string) string { return strings.ToLower(fullPercent(s)) }},
		{"percent_full_mixed", mixedPercent},
		{"hex_lower", func(s string) string { return hex.EncodeToString([]byte(s)) }},
		{"hex_upper", func(s string) string { return strings.ToUpper(hex.EncodeToString([]byte(s))) }},
	}
	for _, encoding := range []struct {
		name  string
		value *base64.Encoding
	}{
		{"base64_std", base64.StdEncoding}, {"base64_url", base64.URLEncoding},
		{"base64_rawstd", base64.RawStdEncoding}, {"base64_rawurl", base64.RawURLEncoding},
	} {
		out = append(out, r3Spelling{encoding.name, func(s string) string { return encoding.value.EncodeToString([]byte(s)) }})
		for offset := 0; offset < 3; offset++ {
			out = append(out, r3Spelling{fmt.Sprintf("%s_embedded_%d", encoding.name, offset), func(s string) string {
				// Encode the complete surrounding value, not a pasted credential fragment.
				return encoding.value.EncodeToString([]byte(strings.Repeat("~", offset) + s + "!suffix"))
			}})
		}
	}
	return out
}

func r3AssertConstructedError(t *testing.T, err error, key, echo string) {
	t.Helper()
	if err == nil {
		t.Fatal("provider refusal disappeared")
	}
	var api *models.APIError
	if !errors.As(err, &api) {
		t.Fatalf("provider refusal is %T, want APIError", err)
	}
	for _, text := range []string{err.Error(), api.Message, api.Code, api.Type} {
		if strings.Contains(text, key) || strings.Contains(text, echo) {
			t.Error("R1: sent credential survived the adapter's error constructor")
		}
	}
	if !strings.Contains(api.Message, "diagnostic-before ") || !strings.Contains(api.Message, " diagnostic-after unrelated-owner-value") {
		t.Error("source scrub removed noncredential diagnostics")
	}
	if !strings.Contains(api.Message, "[REDACTED]") {
		t.Error("source scrub did not mark the removed credential")
	}
	// Check the credential-bearing sextets too, not only the complete encoded wrapper.
	for _, enc := range []*base64.Encoding{base64.StdEncoding, base64.URLEncoding} {
		for offset := 0; offset < 3; offset++ {
			whole := enc.EncodeToString([]byte(strings.Repeat("~", offset) + key + "!suffix"))
			needle := whole[(offset*8+5)/6 : ((offset+len(key))*8)/6]
			for _, text := range []string{err.Error(), api.Message, api.Code, api.Type} {
				if strings.Contains(text, needle) {
					t.Error("R1: credential-bearing base64 fragment survived construction")
				}
			}
		}
	}
}
