// Source-bound credential regressions. All credentials are synthetic.
package bedrock

import (
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"hash/crc32"
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
						if r.Header.Get("X-Amz-Security-Token") != key {
							t.Error("wrong sent session token")
						}
						if !strings.Contains(r.Header.Get("Authorization"), "Credential=r5-access/") {
							t.Error("missing signing witness")
						}
						witnessed = true
						if mode != "stream_terminal" {
							w.WriteHeader(400)
							json.NewEncoder(w).Encode(map[string]any{"message": message, "__type": echo})
							return
						}
						w.Header().Set("Content-Type", "text/event-stream")
						b, _ := json.Marshal(map[string]any{"message": message})
						w.Write(r5Event(b))
					}))
					defer upstream.Close()
					cfg := config.ProviderConfig{BaseURL: upstream.URL, APIKey: key, MaxConcurrent: 2, DefaultTimeout: time.Second, Headers: map[string]string{}}
					cfg.AWSAccessKeyID = "r5-access"
					cfg.AWSSecretAccessKey = "r5-signing-secret"
					cfg.AWSSessionToken = key
					cfg.AWSRegion = "us-east-1"
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
						want := "diagnostic-before [REDACTED] diagnostic-after " + other
						if mode == "stream_terminal" {
							b, _ := json.Marshal(map[string]string{"message": want})
							want = "bedrock exception: " + string(b)
						}
						if errors.As(err, &api) && api.Message != want {
							t.Errorf("BYOK text must differ only by sent key, got %q", api.Message)
						}
					}
				})
			}
		}
	}
}

func r5Event(payload []byte) []byte {
	headers := []byte{}
	for _, kv := range [][2]string{{":message-type", "exception"}, {":exception-type", "modelStreamErrorException"}} {
		headers = append(headers, byte(len(kv[0])))
		headers = append(headers, kv[0]...)
		headers = append(headers, 7, byte(len(kv[1])>>8), byte(len(kv[1])))
		headers = append(headers, kv[1]...)
	}
	out := make([]byte, 12)
	binary.BigEndian.PutUint32(out, uint32(16+len(headers)+len(payload)))
	binary.BigEndian.PutUint32(out[4:], uint32(len(headers)))
	binary.BigEndian.PutUint32(out[8:], crc32.ChecksumIEEE(out[:8]))
	out = append(out, headers...)
	out = append(out, payload...)
	var crc [4]byte
	binary.BigEndian.PutUint32(crc[:], crc32.ChecksumIEEE(out))
	return append(out, crc[:]...)
}
