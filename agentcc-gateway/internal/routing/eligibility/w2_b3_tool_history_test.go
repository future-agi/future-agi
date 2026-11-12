package eligibility

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// J31-01: a conversation returned by OpenRouter must be accepted for the next
// turn on both BYOK and opted-in managed routes. Native Gemini still requires
// the signature representation its own adapter can restore.
func TestW2B3OpenRouterToolHistory(t *testing.T) {
	const call = `{"id":"call_weather","type":"function","function":{"name":"weather","arguments":"{}"}}`
	const signedCall = `{"id":"call_weather","type":"function","function":{"name":"weather","arguments":"{}"},"extra_content":{"google":{"thought_signature":"synthetic-signature"}}}`
	const encrypted = `[{"type":"reasoning.encrypted","data":"synthetic-signature","format":"google-gemini-v1","index":0}]`
	cases := []struct {
		name       string
		calls      string
		details    string
		aggregator bool
		wantOK     bool
	}{
		{"openrouter extra content", signedCall, `null`, true, true},
		{"openrouter encrypted reasoning", call, encrypted, true, true},
		{"openrouter both representations", signedCall, encrypted, true, true},
		{"openrouter parallel calls with message reasoning", call + `,{"id":"call_time","type":"function","function":{"name":"time","arguments":"{}"}}`, encrypted, true, true},
		{"openrouter unsigned", call, `null`, true, false},
		{"openrouter summary is not signature", call, `[{"type":"reasoning.text","text":"Check weather","format":"google-gemini-v1"}]`, true, false},
		{"openrouter empty encrypted data", call, `[{"type":"reasoning.encrypted","data":"","format":"google-gemini-v1"}]`, true, false},
		{"openrouter other provider reasoning", call, `[{"type":"reasoning.encrypted","data":"opaque","format":"openai-responses-v1"}]`, true, false},
		{"openrouter empty extra content", `{"id":"call_weather","extra_content":{"google":{"thought_signature":""}}}`, `null`, true, false},
		{"openrouter other extra content", `{"id":"call_weather","extra_content":{"other":{"thought_signature":"opaque"}}}`, `null`, true, false},
		{"openrouter native marker alone is not translated", `{"id":"call_weather::sig::c2ln","type":"function"}`, `null`, true, false},
		{"native marker", `{"id":"call_weather::sig::c2ln","type":"function"}`, `null`, false, true},
		{"native cannot restore extra content", signedCall, `null`, false, false},
		{"native cannot restore reasoning details", call, encrypted, false, false},
	}
	for _, tc := range cases {
		for _, managed := range []bool{false, true} {
			mode := "byok"
			if managed {
				mode = "managed"
			}
			t.Run(tc.name+"/"+mode, func(t *testing.T) {
				var req models.ChatCompletionRequest
				body := `{"model":"google/gemini-3.5-flash","messages":[{"role":"user","content":"Weather?"},{"role":"assistant","tool_calls":[` + tc.calls + `],"reasoning_details":` + tc.details + `},{"role":"tool","tool_call_id":"call_weather","content":"Sunny"}]}`
				if err := json.Unmarshal([]byte(body), &req); err != nil {
					t.Fatal(err)
				}
				in := byokIn(nil)
				if managed {
					in = managedIn(nil, nil)
					// Native controls have no catalog endpoint in this fixture;
					// opt into collection so rule 5 does not mask rule 10.
					in.Org.ManagedModels.AllowDataCollection = !tc.aggregator
				}
				in.Model, in.Request, in.Aggregator = req.Model, &req, tc.aggregator
				in.ProviderID = "custom-provider-id"
				got := Check(&in)
				if got.OK != tc.wantOK {
					t.Fatalf("continuation allowed = %t, want %t; code=%s reason=%s", got.OK, tc.wantOK, got.Code, got.Reason)
				}
				if !tc.wantOK && got.Code != CodeToolHistoryIncompatible {
					t.Fatalf("refused for %s, want %s", got.Code, CodeToolHistoryIncompatible)
				}
			})
		}
	}
}

func TestW2B3OpenRouterSignatureDoesNotCoverOtherMessages(t *testing.T) {
	var req models.ChatCompletionRequest
	if err := json.Unmarshal([]byte(`{"messages":[
		{"role":"assistant","tool_calls":[{"id":"call_old"}]},
		{"role":"tool","tool_call_id":"call_old","content":"Done"},
		{"role":"assistant","tool_calls":[{"id":"call_new"}],"reasoning_details":[{"type":"reasoning.encrypted","data":"synthetic-signature","format":"google-gemini-v1"}]}
	]}`), &req); err != nil {
		t.Fatal(err)
	}
	in := byokIn(nil)
	in.Model, in.Request, in.Aggregator = "google/gemini-3.5-flash", &req, true
	if got := Check(&in); got.OK || got.Code != CodeToolHistoryIncompatible {
		t.Fatalf("unsigned earlier assistant was accepted: %+v", got)
	}
}
