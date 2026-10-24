package pii

import (
	"bytes"
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// pii-detection reads the arguments of a request's tool calls, and with a
// remediation of mask, redact or hash it rewrites them: each value it
// matches is rewritten where it is, and arguments that are JSON stay JSON,
// with their keys in order and the values it did not match as they were.

func toolCallInput(arguments string) *guardrails.CheckInput {
	user, _ := json.Marshal("Send the record.")
	return &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{
			{Role: "user", Content: user},
			{Role: "assistant", ToolCalls: []models.ToolCall{{ID: "call_1", Type: "function",
				Function: models.FunctionCall{Name: "send", Arguments: arguments}}}},
		}},
		Metadata: map[string]string{},
	}
}

func argumentsOf(in *guardrails.CheckInput) string {
	return in.Request.Messages[1].ToolCalls[0].Function.Arguments
}

// keysInOrder decodes JSON arguments with its keys in order, for a test to
// check that a rewrite kept them.
func keysInOrder(t *testing.T, arguments string) []string {
	t.Helper()
	dec := json.NewDecoder(strings.NewReader(arguments))
	if _, err := dec.Token(); err != nil {
		t.Fatalf("arguments %s are not a JSON object: %v", arguments, err)
	}
	var keys []string
	for dec.More() {
		tok, err := dec.Token()
		if err != nil {
			t.Fatal(err)
		}
		keys = append(keys, tok.(string))
		var skip json.RawMessage
		if err := dec.Decode(&skip); err != nil {
			t.Fatal(err)
		}
	}
	return keys
}

func TestPII_BlockModeReadsToolCallArgumentsAndLeavesThem(t *testing.T) {
	const args = `{"to":"alice@example.com"}`
	in := toolCallInput(args)
	r := New(nil).Check(context.Background(), in)
	if r.Pass {
		t.Fatal("pii-detection passed an email in a tool call's arguments")
	}
	assertEntityFound(t, r, "email")
	if got := argumentsOf(in); got != args {
		t.Errorf("block mode rewrote the arguments to %s", got)
	}
}

func TestPII_RewritesToolCallArguments(t *testing.T) {
	const args = `{"to":"alice@example.com","note":"SSN 123-45-6789 on file","count":5,"urgent":true,"cc":null,` +
		`"more":{"list":["bob@example.com",7,"plain"]}}`
	for _, tc := range []struct {
		remediation string
		email, ssn  string
	}{
		{"mask", "***", "***"},
		{"redact", "[REDACTED:email]", "[REDACTED:ssn]"},
		{"hash", "[SHA:", "[SHA:"},
	} {
		t.Run(tc.remediation, func(t *testing.T) {
			in := toolCallInput(args)
			r := New(map[string]interface{}{"remediation": tc.remediation}).Check(context.Background(), in)
			if r.Pass {
				t.Fatal("pii-detection passed PII in a tool call's arguments")
			}
			got := argumentsOf(in)
			if !json.Valid([]byte(got)) {
				t.Fatalf("the rewritten arguments are not JSON: %s", got)
			}
			for _, raw := range []string{"alice@example.com", "bob@example.com", "123-45-6789"} {
				if strings.Contains(got, raw) {
					t.Errorf("the rewritten arguments still hold %s: %s", raw, got)
				}
			}
			var v struct {
				To     string `json:"to"`
				Note   string `json:"note"`
				Count  int    `json:"count"`
				Urgent bool   `json:"urgent"`
				CC     any    `json:"cc"`
				More   struct {
					List []any `json:"list"`
				} `json:"more"`
			}
			if err := json.Unmarshal([]byte(got), &v); err != nil {
				t.Fatal(err)
			}
			if !strings.HasPrefix(v.To, tc.email) || !strings.HasPrefix(v.Note, "SSN "+tc.ssn) || !strings.HasSuffix(v.Note, " on file") {
				t.Errorf("rewritten to %q and note %q, want each match rewritten in place (%s)", v.To, v.Note, tc.remediation)
			}
			if v.Count != 5 || !v.Urgent || v.CC != nil || len(v.More.List) != 3 || v.More.List[1] != float64(7) || v.More.List[2] != "plain" {
				t.Errorf("values the check did not match changed: %s", got)
			}
			if s, _ := v.More.List[0].(string); !strings.HasPrefix(s, tc.email) {
				t.Errorf("an email in a nested list was rewritten to %v, want %s", v.More.List[0], tc.email)
			}
			if keys := keysInOrder(t, got); strings.Join(keys, ",") != "to,note,count,urgent,cc,more" {
				t.Errorf("keys %v, want them in the order they were sent", keys)
			}
		})
	}
}

// A value written with JSON escapes is read as the provider decodes it, and
// rewritten.
func TestPII_RewritesAnEscapedArgument(t *testing.T) {
	in := toolCallInput(`{"to":"alice@example.com"}`)
	r := New(map[string]interface{}{"remediation": "mask"}).Check(context.Background(), in)
	if r.Pass {
		t.Fatal(`pii-detection passed an email written as alice@example.com`)
	}
	if got := argumentsOf(in); got != `{"to":"***"}` {
		t.Errorf("arguments %s, want {\"to\":\"***\"}", got)
	}
}

// A number the check matches (a card number sent as a JSON number) is
// rewritten as a string: the arguments stay JSON and the number is not sent.
func TestPII_RewritesANumberArgumentAsAString(t *testing.T) {
	in := toolCallInput(`{"card":4111111111111111,"qty":2}`)
	r := New(map[string]interface{}{"remediation": "redact"}).Check(context.Background(), in)
	if r.Pass {
		t.Fatal("pii-detection passed a card number sent as a JSON number")
	}
	if got := argumentsOf(in); got != `{"card":"[REDACTED:credit_card]","qty":2}` {
		t.Errorf(`arguments %s, want {"card":"[REDACTED:credit_card]","qty":2}`, got)
	}
}

// A member is read with its key, so a pattern that needs a keyword
// (passport) matches "passport: <number>"; the rewrite goes into the value,
// and the key stays.
func TestPII_ReadsAnArgumentWithItsKey(t *testing.T) {
	in := toolCallInput(`{"passport":"123456789"}`)
	r := New(map[string]interface{}{"remediation": "mask"}).Check(context.Background(), in)
	if r.Pass {
		t.Fatal("pii-detection passed a passport number given under the key passport")
	}
	if got := argumentsOf(in); got != `{"passport":"***"}` {
		t.Errorf(`arguments %s, want {"passport":"***"}`, got)
	}
}

// A key the check matches (an object keyed by email address) is rewritten.
func TestPII_RewritesAMatchingKey(t *testing.T) {
	in := toolCallInput(`{"recipients":{"alice@example.com":"to","bob@example.com":{"role":"cc"}}}`)
	r := New(map[string]interface{}{"remediation": "mask"}).Check(context.Background(), in)
	if r.Pass {
		t.Fatal("pii-detection passed emails used as keys")
	}
	got := argumentsOf(in)
	if !json.Valid([]byte(got)) || strings.Contains(got, "@example.com") {
		t.Errorf("arguments %s, want JSON without the emails", got)
	}
}

// Every copy of a repeated key is read and rewritten: a provider's parser
// keeps one of them.
func TestPII_RewritesARepeatedKey(t *testing.T) {
	in := toolCallInput(`{"to":"alice@example.com","to":"bob@example.com"}`)
	New(map[string]interface{}{"remediation": "mask"}).Check(context.Background(), in)
	if got := argumentsOf(in); got != `{"to":"***","to":"***"}` {
		t.Errorf(`arguments %s, want {"to":"***","to":"***"}`, got)
	}
}

// Arguments that are not JSON (a custom tool's free text) are read and
// rewritten as text.
func TestPII_RewritesArgumentsThatAreNotJSON(t *testing.T) {
	in := toolCallInput("email alice@example.com the report")
	r := New(map[string]interface{}{"remediation": "mask"}).Check(context.Background(), in)
	if r.Pass {
		t.Fatal("pii-detection passed an email in arguments that are not JSON")
	}
	if got := argumentsOf(in); got != "email *** the report" {
		t.Errorf("arguments %q, want %q", got, "email *** the report")
	}
}

// Arguments the check does not match are left byte for byte as sent, white
// space included.
func TestPII_KeepsUnmatchedArgumentsAsSent(t *testing.T) {
	const args = "{ \"q\" : \"weather\\u0020in Paris\" ,\n \"days\": 3 }"
	in := toolCallInput(args)
	New(map[string]interface{}{"remediation": "mask"}).Check(context.Background(), in)
	if got := argumentsOf(in); got != args {
		t.Errorf("arguments %q, want them as sent %q", got, args)
	}
}

// Two matches that overlap (a URL's credentials and the email-shaped text in
// it) are rewritten as one.
func TestPII_OverlappingMatchesAreRewrittenAsOne(t *testing.T) {
	for _, place := range []string{"content", "arguments"} {
		t.Run(place, func(t *testing.T) {
			var in *guardrails.CheckInput
			if place == "content" {
				in = makeInput("see https://user:pass@host.example.com now")
			} else {
				in = toolCallInput(`{"url":"https://user:pass@host.example.com"}`)
			}
			r := New(map[string]interface{}{"remediation": "mask"}).Check(context.Background(), in)
			if r.Pass {
				t.Fatal("pii-detection passed a URL with credentials")
			}
			var got string
			if place == "content" {
				_ = json.Unmarshal(in.Request.Messages[0].Content, &got)
			} else {
				got = argumentsOf(in)
			}
			if strings.Contains(got, "user:pass") || strings.Contains(got, "pass@") {
				t.Errorf("rewritten to %s, which still holds the credentials", got)
			}
			if place == "arguments" && !json.Valid([]byte(got)) {
				t.Errorf("the rewritten arguments are not JSON: %s", got)
			}
		})
	}
}

// The tool call's name is not rewritten: it names the tool the provider
// calls.
func TestPII_LeavesTheToolNameAlone(t *testing.T) {
	in := toolCallInput(`{"to":"alice@example.com"}`)
	New(map[string]interface{}{"remediation": "mask"}).Check(context.Background(), in)
	if name := in.Request.Messages[1].ToolCalls[0].Function.Name; name != "send" {
		t.Errorf("the tool's name became %q", name)
	}
	if !bytes.Contains([]byte(argumentsOf(in)), []byte(`"***"`)) {
		t.Errorf("arguments %s were not masked", argumentsOf(in))
	}
}
