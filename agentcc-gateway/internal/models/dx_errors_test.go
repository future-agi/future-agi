package models

import (
	"encoding/json"
	"strings"
	"testing"
)

// dxRequestShape decodes through an embedded alias, as ChatCompletionRequest
// does, so a field's path carries the alias's name.
type dxRequestShape struct {
	Model    string `json:"model"`
	Messages []struct {
		Role string `json:"role"`
	} `json:"messages"`
	Options struct {
		Temperature float64 `json:"temperature"`
	} `json:"options"`
}

func (r *dxRequestShape) UnmarshalJSON(data []byte) error {
	type Alias dxRequestShape
	aux := struct{ *Alias }{Alias: (*Alias)(r)}
	return json.Unmarshal(data, &aux)
}

func TestDescribeJSONErrorNamesNoGoTypes(t *testing.T) {
	cases := map[string]string{
		`42`:                                "the body must be a JSON object, not a number.",
		`"hi"`:                              "the body must be a JSON object, not a string.",
		`{"messages":"hi"}`:                 `"messages" must be an array, not a string.`,
		`{"model":7}`:                       `"model" must be a string, not a number.`,
		`{"options":{"temperature":"hot"}}`: `"options.temperature" must be a number, not a string.`,
		`{"messages":[{"role":true}]}`:      `"messages.role" must be a string, not true or false.`,
		`{"model": "x", "messages": [`:      "unexpected end of JSON input",
		`{'model': 'x'}`:                    `invalid character '\'' looking for beginning of object key string`,
	}
	for body, want := range cases {
		var req dxRequestShape
		err := json.Unmarshal([]byte(body), &req)
		if err == nil {
			t.Fatalf("%s decoded", body)
		}
		got := DescribeJSONError(err)
		if got != want {
			t.Errorf("%s: got %q, want %q", body, got, want)
		}
		for _, leak := range []string{"Go ", "Alias", "struct", "models."} {
			if strings.Contains(got, leak) {
				t.Errorf("%s: %q names %q", body, got, leak)
			}
		}
	}
}

func TestCreditRefusalMessages(t *testing.T) {
	cases := []struct{ got, want string }{
		{CreditsExhaustedMessage(true, false), "Your organization has no Future AGI credit. Add credit on the Settings Billing page, or use your own provider keys on the Gateway Providers page."},
		{CreditsExhaustedMessage(false, false), "Your organization has used all of its Future AGI credit. Add credit on the Settings Billing page, or use your own provider keys on the Gateway Providers page."},
		{CreditsExhaustedMessage(true, true), "Your organization has no Future AGI credit. Credit purchase is not available on this self-hosted install, so credit is granted by its operator: ask them to grant Future AGI credit, or use your own provider keys on the Gateway Providers page."},
		{CreditsInFlightMessage(false), "Your organization's Future AGI credit is held by requests in flight. Retry shortly, or add credit on the Settings Billing page."},
		{CreditsInFlightMessage(true), "Your organization's Future AGI credit is held by requests in flight. Retry shortly."},
		{CreditsInsufficientMessage("request", 0.001, 0.0028, true, false), "Your organization's Future AGI credit ($0.001000) does not cover this request's worst-case cost ($0.002800). Add credit on the Settings Billing page, lower max_tokens, or use your own provider keys on the Gateway Providers page."},
		{CreditsInsufficientMessage("call", 0.001, 0.0028, false, false), "Your organization's Future AGI credit ($0.001000) does not cover this call's worst-case cost ($0.002800). Add credit on the Settings Billing page, or use your own provider keys on the Gateway Providers page."},
	}
	for _, c := range cases {
		if c.got != c.want {
			t.Errorf("got  %q\nwant %q", c.got, c.want)
		}
		if strings.Contains(strings.ToLower(c.got), "managed") {
			t.Errorf("%q says managed", c.got)
		}
	}
}
