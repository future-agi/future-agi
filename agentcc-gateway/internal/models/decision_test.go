package models

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

// decisionEnvelopeCase is one envelope case from the shared fixture.
type decisionEnvelopeCase struct {
	Name          string          `json:"name"`
	Route         string          `json:"route"`
	Body          json.RawMessage `json:"body"`
	BodyRaw       *string         `json:"body_raw"`
	Format        string          `json:"format"`
	QuestionIDs   []string        `json:"question_ids"`
	QuestionTypes []string        `json:"question_types"`
	Status        int             `json:"status"`
	Code          string          `json:"code"`
	NamesQuestion string          `json:"names_question"`
	MustNotEcho   []string        `json:"must_not_echo"`
}

func (c decisionEnvelopeCase) body() []byte {
	if c.BodyRaw != nil {
		return []byte(*c.BodyRaw)
	}
	return c.Body
}

func loadDecisionEnvelopeCases(t *testing.T) []decisionEnvelopeCase {
	t.Helper()
	// The cases are shared with the Jev client's tests of the same contract.
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "decision-cases.json"))
	if err != nil {
		t.Fatalf("reading the shared decision cases: %v", err)
	}
	var fixture struct {
		EnvelopeCases []decisionEnvelopeCase `json:"envelope_cases"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatalf("parsing the shared decision cases: %v", err)
	}
	if len(fixture.EnvelopeCases) == 0 {
		t.Fatal("the shared decision cases have no envelope cases")
	}
	return fixture.EnvelopeCases
}

func TestParseDecisionRequestSharedCases(t *testing.T) {
	for _, tc := range loadDecisionEnvelopeCases(t) {
		if tc.Route != "" {
			continue // a route-level refusal; the handler test covers it
		}
		t.Run(tc.Name, func(t *testing.T) {
			req, apiErr := ParseDecisionRequest(tc.body())
			if tc.Status != 0 {
				if apiErr == nil {
					t.Fatalf("parsed %+v, want %d %s", req, tc.Status, tc.Code)
				}
				if apiErr.Status != tc.Status || apiErr.Code != tc.Code {
					t.Fatalf("error = %d %s (%s), want %d %s", apiErr.Status, apiErr.Code, apiErr.Message, tc.Status, tc.Code)
				}
				if tc.NamesQuestion != "" && !strings.Contains(apiErr.Message, `"`+tc.NamesQuestion+`"`) {
					t.Errorf("message %q does not name question %q", apiErr.Message, tc.NamesQuestion)
				}
				for _, secret := range tc.MustNotEcho {
					if strings.Contains(apiErr.Message, secret) {
						t.Errorf("message %q echoes request content", apiErr.Message)
					}
				}
				return
			}
			if apiErr != nil {
				t.Fatalf("unexpected error %s: %s", apiErr.Code, apiErr.Message)
			}
			if req.Format != tc.Format {
				t.Errorf("format = %q, want %q", req.Format, tc.Format)
			}
			if !reflect.DeepEqual(req.QuestionIDs, tc.QuestionIDs) || !reflect.DeepEqual(req.QuestionTypes, tc.QuestionTypes) {
				t.Errorf("questions = %v %v, want %v %v", req.QuestionIDs, req.QuestionTypes, tc.QuestionIDs, tc.QuestionTypes)
			}
			if !bytes.Equal(req.Body, tc.body()) {
				t.Error("parsing changed the body")
			}
			var probe struct {
				Model string `json:"model"`
			}
			json.Unmarshal(tc.body(), &probe)
			if req.Model != probe.Model || req.UpstreamModel != probe.Model {
				t.Errorf("model = %q upstream %q, want %q", req.Model, req.UpstreamModel, probe.Model)
			}
		})
	}
}

func TestParseDecisionRequestCountsState(t *testing.T) {
	req, apiErr := ParseDecisionRequest([]byte(`{"model":"m","state": {"a": "bcd"} ,"questions":{"q":{"type":"noul"}}}`))
	if apiErr != nil {
		t.Fatal(apiErr)
	}
	if req.StateBytes != len(`{"a": "bcd"}`) {
		t.Errorf("state_bytes = %d", req.StateBytes)
	}
	req, apiErr = ParseDecisionRequest([]byte(`{"model":"m","input":"hello","questions":[{"type":"predicate"}]}`))
	if apiErr != nil {
		t.Fatal(apiErr)
	}
	if req.StateBytes != len(`"hello"`) || req.QuestionIDs[0] != "#0" {
		t.Errorf("state_bytes = %d ids = %v", req.StateBytes, req.QuestionIDs)
	}
}

func TestDecisionErrorsTruncateLongQuestionIDs(t *testing.T) {
	id := strings.Repeat("x", 500)
	_, apiErr := ParseDecisionRequest([]byte(`{"model":"m","state":1,"questions":{"` + id + `":{}}}`))
	if apiErr == nil || apiErr.Code != "invalid_question" {
		t.Fatalf("error = %v", apiErr)
	}
	if strings.Contains(apiErr.Message, id) || len(apiErr.Message) > 200 {
		t.Errorf("message carries the whole ID: %d bytes", len(apiErr.Message))
	}
}

// Only the model's value changes. Every other byte — whitespace, key order,
// number spelling, unknown fields — is forwarded as the caller sent it.
func TestSetUpstreamModelRewritesOnlyTheModel(t *testing.T) {
	for _, tt := range []struct{ name, body, want string }{
		{"compact",
			`{"model":"typesafe/jev-latest","state":{"n":1.50},"questions":{"q":{"type":"noul"}}}`,
			`{"model":"jev-latest","state":{"n":1.50},"questions":{"q":{"type":"noul"}}}`},
		{"spaced",
			"{\n  \"state\" : [1, 2],\n  \"model\" :   \"typesafe/jev-latest\" ,\n  \"questions\": {\"q\": {\"type\": \"noul\", \"x\": \"\\u00e9\"}}\n}\n",
			"{\n  \"state\" : [1, 2],\n  \"model\" :   \"jev-latest\" ,\n  \"questions\": {\"q\": {\"type\": \"noul\", \"x\": \"\\u00e9\"}}\n}\n"},
		{"model_last",
			`{"questions":{"q":{"type":"noul"}},"state":"s","model":"typesafe/jev-latest"}`,
			`{"questions":{"q":{"type":"noul"}},"state":"s","model":"jev-latest"}`},
		{"escaped_model",
			`{"model":"typesafe\/jev-latest","state":"s","questions":{"q":{"type":"noul"}}}`,
			`{"model":"jev-latest","state":"s","questions":{"q":{"type":"noul"}}}`},
	} {
		t.Run(tt.name, func(t *testing.T) {
			req, apiErr := ParseDecisionRequest([]byte(tt.body))
			if apiErr != nil {
				t.Fatal(apiErr)
			}
			req.SetUpstreamModel("jev-latest")
			if string(req.Body) != tt.want {
				t.Fatalf("body =\n%s\nwant\n%s", req.Body, tt.want)
			}
			if req.Model != "typesafe/jev-latest" || req.UpstreamModel != "jev-latest" {
				t.Errorf("model = %q upstream = %q", req.Model, req.UpstreamModel)
			}
			// A second rewrite lands on the new span.
			req.SetUpstreamModel("jev-1.13.0")
			if !strings.Contains(string(req.Body), `"jev-1.13.0"`) || strings.Contains(string(req.Body), `"jev-latest"`) {
				t.Errorf("second rewrite: %s", req.Body)
			}
		})
	}
}

func TestSetUpstreamModelSameModelKeepsBody(t *testing.T) {
	body := []byte(`{"model" : "jev-latest","state":1,"questions":{"q":{"type":"noul"}}}`)
	req, apiErr := ParseDecisionRequest(body)
	if apiErr != nil {
		t.Fatal(apiErr)
	}
	req.SetUpstreamModel("jev-latest")
	if !bytes.Equal(req.Body, body) {
		t.Fatalf("body changed: %s", req.Body)
	}
}

func TestParseDecisionResponse(t *testing.T) {
	resp, ok := ParseDecisionResponse([]byte(`{"model":"jev-1.13.0","answers":{"q":{"type":"noul","noul":0.9}},"usage":{"input_tokens":17,"output_tokens":3},"future":true}`))
	if !ok || resp.Model != "jev-1.13.0" || resp.Usage != (DecisionUsage{17, 3}) || string(resp.Answers) != `{"q":{"type":"noul","noul":0.9}}` {
		t.Fatalf("resp = %+v ok = %v", resp, ok)
	}
	resp, ok = ParseDecisionResponse([]byte(`{"model":"gpt-6-luna","usage":{"prompt_tokens":5,"completion_tokens":-2}}`))
	if !ok || resp.Usage != (DecisionUsage{5, 0}) {
		t.Fatalf("openai usage = %+v", resp.Usage)
	}
	// Usage of an unreadable shape is zero, not a broken answer.
	resp, ok = ParseDecisionResponse([]byte(`{"answers":{},"usage":{"input_tokens":"12"},"model":7}`))
	if !ok || resp.Usage != (DecisionUsage{}) || resp.Model != "" {
		t.Fatalf("resp = %+v ok = %v", resp, ok)
	}
	for _, body := range []string{``, `[]`, `"x"`, `not json`, `{"a":1} {}`, `{"a":`} {
		if _, ok := ParseDecisionResponse([]byte(body)); ok {
			t.Errorf("%q parsed as an answer", body)
		}
	}
}

func TestSafeUpstreamMessage(t *testing.T) {
	const key = "synthetic-decision-key-CANARY"
	system, _ := ParseDecisionRequest([]byte(`{"model":"jev-latest","state":{"ticket":"private-state","count":987654321,"flagged":true},"extra":"private-extra","questions":{"resolved":{"type":"noul","instructions":["private-instructions"],"criteria":{"private-label":"private-description"}}}}`))
	openai, _ := ParseDecisionRequest([]byte(`{"model":"openai/gpt-6-luna","input":"private-input","questions":[{"type":"predicate","name":"resolved","instructions":"private-instructions","choices":[{"value":"private-choice"}]}]}`))
	message := func(text string) []byte {
		b, _ := json.Marshal(map[string]any{"error": map[string]string{"message": text}})
		return b
	}
	for _, tt := range []struct {
		name string
		req  *DecisionRequest
		body []byte
		want string
	}{
		{"safe", system, message("Unsupported question type for resolved"), "Unsupported question type for resolved"},
		{"names_model", system, message("Unknown model jev-latest"), "Unknown model jev-latest"},
		{"key", system, message("Invalid key " + key), "generic"},
		{"key_prefix", system, message("Invalid key " + key[:10] + "..."), "generic"},
		{"key_suffix", system, message("Invalid key ..." + key[len(key)-9:]), "generic"},
		{"state_value", system, message("too long: private-state"), "generic"},
		{"state_key", system, message("unknown field ticket"), "generic"},
		{"state_number", system, message("987654321 out of range"), "generic"},
		{"state_bool", system, message("expected true"), "generic"},
		{"unknown_top_level", system, message("private-extra"), "generic"},
		{"instructions", system, message("bad private-instructions"), "generic"},
		{"criteria_label", system, message("duplicate private-label"), "generic"},
		{"criteria_value", system, message("private-description too long"), "generic"},
		{"json_escaped", system, message(`saw "private-state"`), "generic"},
		{"openai_input", openai, message("private-input"), "generic"},
		{"openai_choice", openai, message("bad choice private-choice"), "generic"},
		{"openai_names_question", openai, message("question resolved is invalid"), "question resolved is invalid"},
		{"no_envelope", system, []byte("private-state"), "generic"},
		// Strict on purpose: even a one-letter field name is content, so any
		// message containing it falls back.
		{"short_leaf", mustParse(`{"model":"m","state":{"n":1},"questions":{"q":{"type":"noul"}}}`), message("Unsupported"), "generic"},
		{"empty_message", system, message(" "), "generic"},
		{"too_long", system, message(strings.Repeat("a", 501)), "generic"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			if got := tt.req.SafeUpstreamMessage(tt.body, key, "generic"); got != tt.want {
				t.Fatalf("message = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestDecisionUpstreamErrorRetryAfter(t *testing.T) {
	date := time.Date(2026, 10, 15, 12, 0, 0, 0, time.UTC).Format(http.TimeFormat)
	for _, tt := range []struct{ in, want string }{
		{"7", "7"}, {" 30 ", "30"}, {date, date}, {"-1", ""}, {"soon", ""}, {"", ""}, {strings.Repeat("9", 80), ""},
	} {
		h := http.Header{}
		h.Set("Retry-After", tt.in)
		if got := DecisionUpstreamError("V", 429, h, "").RetryAfter; got != tt.want {
			t.Errorf("Retry-After %q passed on as %q, want %q", tt.in, got, tt.want)
		}
	}
	// Only throttling answers carry one.
	h := http.Header{"Retry-After": {"7"}}
	if got := DecisionUpstreamError("V", 500, h, "").RetryAfter; got != "" {
		t.Errorf("500 carried Retry-After %q", got)
	}
}

func TestDecisionUpstreamErrorUsesMessageOnlyFor400And422(t *testing.T) {
	for status, wantMessage := range map[int]bool{400: true, 422: true, 401: false, 403: false, 402: false, 404: false, 429: false, 500: false} {
		e := DecisionUpstreamError("Vendor", status, http.Header{}, "vendor text")
		if got := strings.Contains(e.Message, "vendor text"); got != wantMessage {
			t.Errorf("%d: message %q", status, e.Message)
		}
		if e.UpstreamStatus != status {
			t.Errorf("%d: upstream status %d", status, e.UpstreamStatus)
		}
	}
}

func TestDecisionTransportError(t *testing.T) {
	expired, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancel()
	if e := DecisionTransportError(expired, errors.New("dial 10.0.0.5:443"), "V"); e.Status != 504 || e.Code != "gateway_timeout" {
		t.Errorf("deadline: %+v", e)
	}
	e := DecisionTransportError(context.Background(), errors.New("dial 10.0.0.5:443: refused"), "V")
	if e.Status != 502 || e.Code != "upstream_error" || strings.Contains(e.Message, "10.0.0.5") {
		t.Errorf("refused: %+v", e)
	}
}

func TestWriteErrorSetsRetryAfterAndUpstreamStatus(t *testing.T) {
	w := httptest.NewRecorder()
	WriteError(w, &APIError{Status: 429, Type: ErrTypeRateLimit, Code: "rate_limit_exceeded", Message: "m", RetryAfter: "7", UpstreamStatus: 429})
	if w.Header().Get("Retry-After") != "7" || w.Header().Get("x-agentcc-upstream-status") != "429" {
		t.Fatalf("headers = %v", w.Header())
	}
	w = httptest.NewRecorder()
	WriteError(w, ErrBadRequest("x", "y"))
	if w.Header().Get("Retry-After") != "" || w.Header().Get("x-agentcc-upstream-status") != "" {
		t.Fatalf("a gateway error carried upstream headers: %v", w.Header())
	}
}

func TestReleaseClearsDecisionFields(t *testing.T) {
	rc := AcquireRequestContext()
	rc.DecisionRequest = &DecisionRequest{Model: "m"}
	rc.DecisionResponse = &DecisionResponse{Model: "m"}
	rc.Release()
	if rc.DecisionRequest != nil || rc.DecisionResponse != nil {
		t.Fatal("Release left decision fields set")
	}
}

func mustParse(body string) *DecisionRequest {
	req, apiErr := ParseDecisionRequest([]byte(body))
	if apiErr != nil {
		panic(apiErr)
	}
	return req
}

// Only a 400 or 422 passes the vendor's text on, so only those parse it.
func TestUpstreamErrorMessageParsesOnlyWhatItShows(t *testing.T) {
	req, _ := ParseDecisionRequest([]byte(`{"model":"jev-latest","questions":{"resolved":{"type":"noul","instructions":["Judge"]}}}`))
	body, _ := json.Marshal(map[string]any{"error": map[string]string{"message": "the type field is invalid"}})
	for _, status := range []int{400, 422} {
		if got := req.UpstreamErrorMessage(status, body, "k", "generic"); got != "the type field is invalid" {
			t.Errorf("%d: got %q", status, got)
		}
	}
	for _, status := range []int{401, 429, 500, 502, 503} {
		if got := req.UpstreamErrorMessage(status, body, "k", "generic"); got != "generic" {
			t.Errorf("%d: got %q, want the fallback", status, got)
		}
	}
	// The statuses parsed are exactly those DecisionUpstreamError shows the
	// message for: one more there would show the fallback instead.
	for status := 100; status < 600; status++ {
		shown := DecisionUpstreamError("Vendor", status, nil, "MESSAGE").Message == "MESSAGE"
		parsed := req.UpstreamErrorMessage(status, body, "k", "generic") != "generic"
		if shown != parsed {
			t.Errorf("%d: message shown %v, parsed %v", status, shown, parsed)
		}
	}
}
