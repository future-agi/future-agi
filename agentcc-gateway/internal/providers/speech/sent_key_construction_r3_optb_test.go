// Speech's adapter returns a session; server owns the upstream handshake.
// Observe the original constructed error, not the projected downstream reply.
package speech_test

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
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/server"
)

type r3SpeechCapture struct {
	err   error
	calls int
}

func (*r3SpeechCapture) Name() string  { return "i2-original-speech-error" }
func (*r3SpeechCapture) Priority() int { return 1 }
func (*r3SpeechCapture) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *r3SpeechCapture) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.calls++
	if len(rc.Errors) > 0 {
		p.err = rc.Errors[0]
	}
	return pipeline.ResultContinue()
}

func TestOptBR3AdapterSentKeyConstruction(t *testing.T) {
	const key = `I2.opaque/"\+Credential-abcdefghijklmnopqrstuvwxyz~?<>`
	for _, vendor := range []string{"deepgram", "cartesia"} {
		for _, spelling := range r3G1Spellings() {
			t.Run(vendor+"/handshake/"+spelling.name, func(t *testing.T) {
				witness := make(chan string, 1)
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					// Registry discovery/health GETs are not speech dispatches and
					// must not consume the bounded authentication witness channel.
					if !strings.EqualFold(r.Header.Get("Upgrade"), "websocket") {
						w.Header().Set("Content-Type", "application/json")
						_, _ = fmt.Fprint(w, `{"data":[]}`)
						return
					}
					sent := r.Header.Get("X-Api-Key")
					if vendor == "deepgram" {
						sent = strings.TrimPrefix(r.Header.Get("Authorization"), "Token ")
					}
					witness <- sent
					if sent != key {
						t.Error("speech stub did not receive the sent key")
					}
					w.WriteHeader(http.StatusBadRequest)
					_, _ = fmt.Fprint(w, "diagnostic-before "+spelling.encode(sent)+" diagnostic-after unrelated-owner-value")
				}))
				defer upstream.Close()
				cfg := &config.Config{Providers: map[string]config.ProviderConfig{vendor: {APIFormat: vendor, BaseURL: upstream.URL, APIKey: key}}}
				registry, err := providers.NewRegistry(cfg)
				if err != nil {
					t.Fatal(err)
				}
				defer registry.Close()
				capture := &r3SpeechCapture{}
				h := server.NewHandlers(registry, pipeline.NewEngine(capture), 1<<20, time.Second, 5*time.Second,
					nil, nil, nil, nil, nil, nil, nil, nil, config.StreamingGuardrailConfig{}, nil, nil, nil, nil)
				path := "/tts/websocket"
				if vendor == "deepgram" {
					path = "/v1/speak"
				}
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				req := httptest.NewRequest(http.MethodGet, "/"+vendor+path, nil).WithContext(ctx)
				req.Header.Set("Connection", "Upgrade")
				req.Header.Set("Upgrade", "websocket")
				rec := httptest.NewRecorder()
				h.SpeechRelay(vendor, path).ServeHTTP(rec, req)
				select {
				case sent := <-witness:
					if sent != key {
						t.Fatal("sent speech key differs")
					}
				default:
					t.Fatal("speech relay never dispatched to the stub")
				}
				if rec.Code != http.StatusBadRequest || capture.calls != 1 {
					t.Fatal("speech refusal did not reach the original-error observer")
				}
				r3AssertConstructedError(t, capture.err, key, spelling.encode(key))
			})
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
