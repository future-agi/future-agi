package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
)

// guardrailText decodes the content the input guardrails receive.
func guardrailText(t *testing.T, content json.RawMessage) string {
	t.Helper()
	var text string
	if err := json.Unmarshal(content, &text); err != nil {
		t.Fatalf("content %.80q is not a JSON string: %v", content, err)
	}
	return text
}

// JSON allows whitespace before the object, and ParseDecisionRequest and
// json.Valid accept it, so the scan must too: a padded body gives exactly the
// text of the unpadded one.
func TestDecisionGuardrailLeadingWhitespace(t *testing.T) {
	bodies := []string{
		decisionSysBody,
		decisionOAIBody,
		`{"state":"abc","model":"typesafe/jev-latest","questions":{"q":{"type":"noul","instructions":"x"}}}`,
		`{"state":"x} my SSN is 123-45-6789","model":"typesafe/jev-latest","questions":{"q":{"type":"noul","instructions":"x"}}}`,
	}
	for _, body := range bodies {
		want, apiErr := decisionGuardrailContent([]byte(body), 0)
		if apiErr != nil {
			t.Fatal(apiErr)
		}
		for _, pad := range []string{" ", "\n", "\t", "\r\n", " \n\t "} {
			got, apiErr := decisionGuardrailContent([]byte(pad+body), 0)
			if apiErr != nil {
				t.Fatalf("pad %q: %v", pad, apiErr)
			}
			if string(got) != string(want) {
				t.Errorf("pad %q on %.40s: text %q, want %q", pad, body, guardrailText(t, got), guardrailText(t, want))
			}
		}
	}
}

// The review's scenario: one leading space must not let state past the input
// guardrails while the upstream gets the whole body.
func TestDecisionGuardrailBlocksStateAfterLeadingSpace(t *testing.T) {
	g, c := decisionWithGuardrail(t, "block", "123-45-6789")
	body := ` {"state":"x} my SSN is 123-45-6789","model":"typesafe/jev-latest","questions":{"q":{"type":"noul","instructions":"x"}}}`
	expectError(t, g.post(t, keyByok, body), http.StatusForbidden, "content_blocked")
	if len(c.inputs) != 1 || !strings.Contains(c.inputs[0], "my SSN is 123-45-6789") {
		t.Fatalf("guardrail saw %q, want the state", c.inputs)
	}
	expectNoCalls(t, g)
}

// The content is a JSON string written without HTML escaping, so '<', '>'
// and '&' stay one byte each: the content is the text plus its quotes and
// JSON escapes, never six bytes per character.
func TestDecisionGuardrailContentIsNotHTMLEscaped(t *testing.T) {
	state := strings.Repeat("<&>", 10_000)
	body := []byte(`{"model":"typesafe/jev-latest","state":"` + state + `","questions":{}}`)
	content, apiErr := decisionGuardrailContent(body, int64(len(body)))
	if apiErr != nil {
		t.Fatal(apiErr)
	}
	if len(content) != len(state)+2 || bytes.Contains(content, []byte(`<`)) {
		t.Fatalf("content is %d bytes for %d bytes of text, starts %.20q", len(content), len(state), content)
	}
	if guardrailText(t, content) != state {
		t.Fatal("content does not decode to the state")
	}
	// Escapes the guardrails need are still written, and decode back.
	tricky := `q\"uo\\te\n\t\r\b\f\u0001\u001f é ` + " "
	body = []byte(`{"model":"m","state":"` + tricky + `","questions":{}}`)
	content, apiErr = decisionGuardrailContent(body, 0)
	if apiErr != nil {
		t.Fatal(apiErr)
	}
	var want string
	if err := json.Unmarshal([]byte(`"`+tricky+`"`), &want); err != nil {
		t.Fatal(err)
	}
	if got := guardrailText(t, content); got != want {
		t.Fatalf("text = %q, want %q", got, want)
	}
}

// The cap counts what the guardrails receive: the encoded content, quotes and
// the two-byte "\n" between parts included.
func TestDecisionGuardrailCapCountsTheEncodedContent(t *testing.T) {
	// 2,000 numbers: a 4,045-byte body whose content is 2 + 2,000 + 2*1,999 bytes.
	body := []byte(`{"model":"m","state":[` + strings.TrimSuffix(strings.Repeat("1,", 2000), ",") + `],"questions":{}}`)
	content, apiErr := decisionGuardrailContent(body, 0)
	if apiErr != nil {
		t.Fatal(apiErr)
	}
	if want := 2 + 2000 + 2*1999; len(content) != want {
		t.Fatalf("content is %d bytes, want %d", len(content), want)
	}
	if _, apiErr := decisionGuardrailContent(body, int64(len(content))); apiErr != nil {
		t.Fatalf("content exactly at the cap: %v", apiErr)
	}
	_, apiErr = decisionGuardrailContent(body, int64(len(content)-1))
	if apiErr == nil || apiErr.Status != http.StatusBadRequest || apiErr.Code != "decision_text_too_large" {
		t.Fatalf("one byte over the cap: %+v", apiErr)
	}
}

// The handler caps the content at the decision body limit, the most a
// decision body may be, not at the larger gateway-wide body size: there the
// cap could only fire for invalid UTF-8.
func TestDecisionGuardrailCapIsTheDecisionBodyLimit(t *testing.T) {
	g, c := decisionWithGuardrail(t, "block", "never-present")
	g.srv.handlers.maxBodySize = 4096
	if limit := g.srv.handlers.decisionBodyLimit(); limit != 4096 {
		t.Fatalf("decisionBodyLimit = %d, want 4096", limit)
	}
	// 3,000 '<' in a 3,080-byte body: 3,002 bytes of content, within the cap.
	lt := strings.Repeat("<", 3000)
	rr := g.post(t, keyByok, `{"model":"typesafe/jev-latest","state":"`+lt+`","questions":{"one":{"type":"noul"}}}`)
	if rr.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rr.Code, rr.Body.String())
	}
	if len(c.inputs) != 1 || c.inputs[0] != lt {
		t.Fatalf("guardrail saw %d inputs", len(c.inputs))
	}
	// 1,900 numbers in a 3,8xx-byte body: about 5,700 bytes of content, over the cap.
	nums := strings.TrimSuffix(strings.Repeat("1,", 1900), ",")
	detail := expectError(t, g.post(t, keyByok, `{"model":"typesafe/jev-latest","state":[`+nums+`],"questions":{"one":{"type":"noul"}}}`),
		http.StatusBadRequest, "decision_text_too_large")
	if !strings.Contains(detail.Message, fmt.Sprint(4096)) {
		t.Fatalf("message %q does not name the 4096-byte cap", detail.Message)
	}
	if len(c.inputs) != 1 {
		t.Fatalf("guardrail ran on a refused body: %d inputs", len(c.inputs))
	}
}
