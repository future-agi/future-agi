package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// Known excluded models used to take the auth plugin's older answer on
// Responses and Completions, unlike chat and the foreign SDK dialects.
func TestDXAlignmentKnownExcludedModel(t *testing.T) {
	const message = "API key does not have access to model: gpt-4o-mini. This key is restricted to its allowed models (gpt-4o); call one of them, or use a key without this restriction."
	encoded, _ := json.Marshal(message)
	openai := `{"error":{"message":` + string(encoded) + `,"type":"permission_error","param":null,"code":"model_not_permitted","metadata":{"reason":"key_model_denied"}}}`
	for _, tc := range []struct{ path, body, want string }{
		{"/v1/responses", `{"model":"gpt-4o-mini","input":"hi"}`, openai},
		{"/v1/completions", `{"model":"gpt-4o-mini","prompt":"hi"}`, openai},
		{"/v1/chat/completions", chatBody("gpt-4o-mini"), openai},
		{"/v1/messages", managedMessagesBody("gpt-4o-mini", false, ""), `{"type":"error","error":{"type":"permission_error","message":` + string(encoded) + `}}`},
		{"/v1beta/models/gpt-4o-mini:generateContent", `{"contents":[{"parts":[{"text":"hi"}]}]}`, `{"error":{"code":403,"message":` + string(encoded) + `,"status":"PERMISSION_DENIED"}}`},
	} {
		t.Run(tc.path, func(t *testing.T) {
			g := newAsstGateway(t, asstKeyOpts{models: []string{"gpt-4o"}}, false)
			expectDXAnswer(t, g.do("POST", tc.path, tc.body, ""), 403, tc.want)
			if calls := g.up.calls.Load(); calls != 0 {
				t.Fatalf("refused request made %d upstream calls", calls)
			}
		})
	}
}

func TestDXAlignmentRawProviderHTTPError(t *testing.T) {
	for _, status := range []int{400, 401, 403, 404, 429, 500, 503} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%d/stream=%v", status, stream), func(t *testing.T) {
				srv := dxNativeGatewayReply(t, "openai", status, "synthetic plain-text refusal")
				body := fmt.Sprintf(`{"model":"gpt-probe","messages":[{"role":"user","content":"hi"}],"stream":%v}`, stream)
				w := dxRequest(srv, http.MethodPost, "/v1/chat/completions", body, map[string]string{"Authorization": "Bearer sk-agentcc-dx-native"})
				code, meta := dxR4Evidence(t, w, false)
				wantCode := fmt.Sprintf("provider_%d", status)
				if status == 401 || status == 403 {
					wantCode = "provider_auth_failed"
				}
				// The fallback keeps its established canonical HTTP mapping (502).
				if w.Code != 502 || code != wantCode || meta["provider"] != "openai" || meta["provider_status"] != float64(status) {
					t.Fatalf("HTTP %d: %s", w.Code, w.Body.String())
				}
				message := fmt.Sprintf("provider error (HTTP %d): synthetic plain-text refusal", status)
				wantMessage := fmt.Sprintf("Provider %q returned HTTP %d: %s", "openai", status, message)
				if status == 404 {
					wantMessage = `Provider "openai" returned HTTP 404 (check that provider's base_url and api_format on the Gateway Providers page): ` + message
				}
				if status == 401 || status == 403 {
					wantMessage = providerAuthMessage("openai", credentialOrg, status, message)
				}
				if dxR4Error(t, w)["message"] != wantMessage || meta["provider_message"] != message {
					t.Fatalf("wrong raw-provider message: %s", w.Body.String())
				}
			})
		}
	}
}

func TestDXAlignmentCountTokensJSON(t *testing.T) {
	g := newAsstGateway(t, asstKeyOpts{}, false)
	for _, tc := range []struct{ body, reason string }{
		{`{"model":42}`, `"model" must be a string, not a number.`},
		{`{"model":false}`, `"model" must be a string, not true or false.`},
		{`{"model":[]}`, `"model" must be a string, not an array.`},
		{`{"model":{}}`, `"model" must be a string, not an object.`},
		{`42`, `the body must be a JSON object, not a number.`},
	} {
		t.Run(tc.body, func(t *testing.T) {
			message, _ := json.Marshal("Invalid JSON in request body: " + tc.reason)
			expectDXAnswer(t, g.do("POST", "/v1/messages/count_tokens", tc.body, ""), 400,
				`{"type":"error","error":{"type":"invalid_request_error","message":`+string(message)+`}}`)
		})
	}
	for _, body := range []string{`{}`, `{"model":""}`, `{"model":null}`} {
		expectDXAnswer(t, g.do("POST", "/v1/messages/count_tokens", body, ""), 400,
			`{"type":"error","error":{"type":"invalid_request_error","message":"model is required"}}`)
	}
	if g.up.calls.Load() != 0 {
		t.Fatal("invalid token-count request reached upstream")
	}
}

func TestDXAlignmentNativeGoogleRPCStatus(t *testing.T) {
	for _, tc := range []struct {
		httpStatus int
		rpc, want  string
	}{
		{400, "FAILED_PRECONDITION", "FAILED_PRECONDITION"},
		{409, "ABORTED", "ABORTED"},
		{501, "UNIMPLEMENTED", "UNIMPLEMENTED"},
		{503, "UNAVAILABLE", "UNAVAILABLE"},
		{400, "NOT_A_RPC_STATUS", "INVALID_ARGUMENT"},
		{401, "UNAUTHENTICATED", "INTERNAL"}, // provider auth is not gateway auth
		{403, "PERMISSION_DENIED", "INTERNAL"},
	} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%v", tc.rpc, stream), func(t *testing.T) {
				body := fmt.Sprintf(`{"error":{"code":%d,"status":%q,"message":"synthetic upstream refusal"}}`, tc.httpStatus, tc.rpc)
				srv := dxNativeGatewayReply(t, "gemini", tc.httpStatus, body)
				action := "generateContent"
				if stream {
					action = "streamGenerateContent?alt=sse"
				}
				w := dxRequest(srv, "POST", "/v1beta/models/gemini-probe:"+action,
					`{"contents":[{"parts":[{"text":"hi"}]}]}`, map[string]string{"Authorization": "Bearer sk-agentcc-dx-native"})
				code, meta := dxR4Evidence(t, w, true)
				status, wantCode := tc.httpStatus, fmt.Sprintf("provider_%d", tc.httpStatus)
				if stream && status >= 500 {
					status = 502
				}
				if tc.httpStatus == 401 || tc.httpStatus == 403 {
					status, wantCode = 502, "provider_auth_failed"
				}
				e := dxR4Error(t, w)
				if w.Code != status || e["code"] != float64(status) || e["status"] != tc.want || code != wantCode || meta["provider_type"] != tc.rpc || meta["provider_status"] != fmt.Sprint(tc.httpStatus) {
					t.Fatalf("HTTP %d: %s", w.Code, w.Body.String())
				}
			})
		}
	}
}

func TestDXAlignmentProviderAttributionTwoPasses(t *testing.T) {
	rc := &models.RequestContext{Provider: "operator", CredentialSource: "org"}
	for _, status := range []int{0, 401, 403, 404, 500} {
		for _, metadata := range []any{nil, map[string]any{"detail": "retained"}, map[string]string{"detail": "retained"}, struct {
			Detail string `json:"detail"`
		}{Detail: "retained"}} {
			t.Run(fmt.Sprintf("%d/%T", status, metadata), func(t *testing.T) {
				e := models.ErrUpstreamProvider(status, "synthetic refusal")
				e.ProviderStatus, e.Metadata, e.RetryAfterSec = status, metadata, 3
				before, _ := json.Marshal(e)
				once := providerFailure(rc, e).(*models.APIError)
				snapshot, _ := json.Marshal(once)
				twice := providerFailure(rc, once)
				if twice != once || attributeProviderFailure(once, "different", credentialManaged) != once {
					t.Fatal("attribution must return an already attributed error unchanged")
				}
				wrapped := routing.SkipHop(once, "deadline")
				if providerFailure(rc, wrapped) != wrapped {
					t.Fatal("repeated attribution discarded the original wrapper")
				}
				after, _ := json.Marshal(twice)
				if string(snapshot) != string(after) {
					t.Fatalf("second pass changed error: %s -> %s", snapshot, after)
				}
				meta, _ := once.Metadata.(map[string]any)
				if meta["provider"] != "operator" || meta["provider_status"] != status || once.RetryAfterSec != 3 || metadata != nil && meta["detail"] != "retained" {
					t.Fatalf("lost metadata: %+v", once)
				}
				meta["detail"] = "changed copy"
				original, _ := json.Marshal(e)
				if !reflect.DeepEqual(before, original) {
					t.Fatalf("input was mutated: %s -> %s", before, original)
				}
			})
		}
	}
}
