package server

import (
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/batch"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/streaming"
)

// Synthetic-only provider: no socket, real credential or external service.
// Its configured credential is deliberately outside the deployed key regexes.
type optBEchoProvider struct {
	providers.Provider
	credential string
	spelling   func(string) string
	field      string
}

func (p optBEchoProvider) ChatCompletion(context.Context, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	e := &models.APIError{Status: 400, ProviderStatus: 400, UpstreamStatus: 400, Provider: "fixture", Message: "rejected", Code: "invalid_request", Type: "invalid_request_error"}
	text := "echo " + p.spelling(p.credential)
	switch p.field {
	case "message":
		e.Message = text
	case "code":
		e.Code = text
	case "type":
		e.Type = text
	}
	return nil, e
}

func optBG1Spellings() map[string]func(string) string {
	forms := map[string]func(string) string{
		"exact":     func(s string) string { return s },
		"json":      func(s string) string { b, _ := json.Marshal(s); return string(b[1 : len(b)-1]) },
		"percent":   url.QueryEscape,
		"hex-lower": func(s string) string { return hex.EncodeToString([]byte(s)) },
		"hex-upper": func(s string) string { return strings.ToUpper(hex.EncodeToString([]byte(s))) },
	}
	// Encode the complete enclosing bytes, not an already-encoded key with a
	// prefix added afterwards. Prefix lengths shift the key's base64 bit phase.
	for _, enc := range []struct {
		name string
		e    *base64.Encoding
	}{{"std", base64.StdEncoding}, {"url", base64.URLEncoding}} {
		for offset := 0; offset < 3; offset++ {
			e, n := enc.e, offset
			forms[fmt.Sprintf("base64-%s-%d", enc.name, n)] = func(s string) string { return e.EncodeToString([]byte(strings.Repeat("~", n) + s + "!")) }
		}
	}
	return forms
}

func optBClientError(t *testing.T, rc *models.RequestContext, err error) *models.APIError {
	t.Helper()
	e, ok := providerFailure(rc, err).(*models.APIError)
	if !ok {
		t.Fatal("provider stub did not yield APIError")
	}
	return e
}

func TestOptBPlatformErrorSpellingsAtClientBoundaries(t *testing.T) {
	const key = "fixture opaque \"credential\" /+?=<> 0123456789 not-live"
	if privacy.RedactCredentials(key, func(string) string { return "MASK" }) != key {
		t.Fatal("fixture must be outside credential regexes")
	}
	for name, spell := range optBG1Spellings() {
		for _, field := range []string{"message", "code", "type"} {
			t.Run(name+"/"+field, func(t *testing.T) {
				p := optBEchoProvider{credential: key, spelling: spell, field: field}
				_, err := p.ChatCompletion(context.Background(), &models.ChatCompletionRequest{})
				rc := &models.RequestContext{RequestID: "req_optb", Provider: "fixture", CredentialSource: "platform", Metadata: map[string]string{}}
				e := optBClientError(t, rc, err)
				httpOut := httptest.NewRecorder()
				models.WriteError(httpOut, e)
				sseOut := httptest.NewRecorder()
				if err := streaming.NewSSEWriter(sseOut).WriteError(e); err != nil {
					t.Fatal(err)
				}
				run := batch.RunAsChatRequest(func() http.Handler {
					return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						_, providerErr := p.ChatCompletion(r.Context(), &models.ChatCompletionRequest{})
						models.WriteError(w, optBClientError(t, rc, providerErr))
					})
				})
				_, _, batchErr := run(context.Background(), &models.ChatCompletionRequest{})
				if batchErr == nil {
					t.Fatal("expected batch item error")
				}
				result := batch.Result{Status: "error", Error: batchErr.Error()}
				nativeBody, _ := json.Marshal(map[string]any{"error": map[string]string{"message": err.(*models.APIError).Message, "code": err.(*models.APIError).Code, "type": err.(*models.APIError).Type}})
				native := nativeProviderFailure(rc, 400, nativeBody)
				nativeOut := httptest.NewRecorder()
				writeGenAIErrorFromError(nativeOut, native)
				for sink, body := range map[string]string{"http": httpOut.Body.String(), "sse": sseOut.Body.String(), "batch.result.error": result.Error, "native": nativeOut.Body.String()} {
					// Decode JSON escaping before checking represented provider text too.
					represented := body
					var object any
					if json.Unmarshal([]byte(body), &object) == nil {
						represented = fmt.Sprint(object)
					}
					if sink == "sse" {
						if json.Unmarshal([]byte(strings.TrimSpace(strings.TrimPrefix(body, "data: "))), &object) == nil {
							represented = fmt.Sprint(object)
						}
					}
					if strings.Contains(represented, spell(key)) || strings.Contains(represented, key) {
						t.Errorf("%s retained full synthetic credential", sink)
					}
				}
			})
		}
	}
}

func TestOptBBYOKAndFragmentPublicationControls(t *testing.T) {
	for _, text := range []string{"ordinary upstream diagnostic", "prefix...6789", "sk-" + strings.Repeat("a", 40)} {
		t.Run(text[:6], func(t *testing.T) {
			rc := &models.RequestContext{Provider: "fixture", CredentialSource: "org", Metadata: map[string]string{"billing_mode": "byok"}}
			original := &models.APIError{Status: 400, ProviderStatus: 400, Provider: "fixture", Message: text, Code: "invalid_request", Type: "invalid_request_error"}
			got := optBClientError(t, rc, original)
			redacted := privacy.RedactCredentials(models.RedactCredentials(text), func(n string) string { return "[REDACTED:" + n + "]" })
			want := `Provider "fixture" returned HTTP 400: ` + redacted
			if got.Message != want || got.Code != original.Code || got.Type != original.Type {
				t.Fatalf("BYOK publication changed beyond redaction: %q", got.Message)
			}
			if original.Message != text {
				t.Fatal("publication mutated retry/failover error")
			}
		})
	}
}

func TestOptBPlatformBillingLabelIsNotCredentialOwnership(t *testing.T) {
	rc := &models.RequestContext{Provider: "fixture", CredentialSource: "platform", Metadata: map[string]string{"billing_mode": "byok"}}
	original := &models.APIError{Status: 400, ProviderStatus: 400, Provider: "fixture", Message: "synthetic opaque platform diagnostic", Code: "opaque_code", Type: "opaque_type"}
	e := optBClientError(t, rc, original)
	if strings.Contains(e.Message, original.Message) || e.Code == original.Code || e.Type == original.Type {
		t.Fatal("billing label exempted platform text")
	}
}
