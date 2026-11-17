package toolview

import (
	"bytes"
	"context"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCUAC26R2RequirementClassifiesBeforeCompatibility(t *testing.T) {
	for _, tc := range []struct {
		name string
		make func([]byte) *NativeRequest
		body string
	}{
		{"messages-versioned", Anthropic, `{"tools":[{"type":"computer_20251124","name":"computer"}]}`},
		{"messages-unknown-version", Anthropic, `{"tools":[{"type":"computer_20990101","name":"computer"}]}`},
		{"responses-preview", Responses, `{"tools":[{"type":"computer_use_preview"}]}`},
		{"responses-unknown-version", Responses, `{"tools":[{"type":"computer_use_future"}]}`},
		{"responses-escaped", Responses, `{"tools":[{"type":"computer_use_preview"}]}`},
		{"gemini-camel", func(b []byte) *NativeRequest { return Gemini(b, "synthetic-model", false) }, `{"tools":[{"computerUse":{"environment":"ENVIRONMENT_BROWSER"}}]}`},
		{"gemini-snake-singleton", func(b []byte) *NativeRequest { return Gemini(b, "synthetic-model", true) }, `{"tools":{"computer_use":{"environment":"ENVIRONMENT_BROWSER"}}}`},
		{"gemini-mixed", func(b []byte) *NativeRequest { return Gemini(b, "synthetic-model", false) }, `{"tools":[{"functionDeclarations":[{"name":"lookup"}],"computerUse":{}}]}`},
		{"gemini-null-declaration", func(b []byte) *NativeRequest { return Gemini(b, "synthetic-model", false) }, `{"tools":[{"computerUse":null}]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := tc.make([]byte(tc.body))
			before := r.Body()
			required, err := r.ComputerUseRequirement(context.Background())
			if err == nil || !required.Required {
				t.Fatal("CUA declaration entered unrestricted compatibility")
			}
			if !bytes.Equal(before, r.Body()) || strings.Contains(err.Error(), "computer_20990101") {
				t.Fatal("classification rewrote or exposed the native declaration")
			}
		})
	}
}

func TestCUAC26R2RequirementAmbiguityRefuses(t *testing.T) {
	for _, body := range []string{
		`{"tools":false}`,
		`{"tools":[null]}`,
		`{"tools":[{"type":null}]}`,
		`{"tools":[{"type":"computer_use_preview","type":"function","name":"lookup"}]}`,
		`{"tools":[{"type":"computer_use_preview"}],"tools":[]}`,
		`{"tools":[],"tools":[{"type":"computer_use_preview"}]}`,
	} {
		if _, err := Responses([]byte(body)).ComputerUseRequirement(context.Background()); err == nil {
			t.Fatal("ambiguous declaration entered unrestricted compatibility")
		}
	}
}

func TestCUAC26R2RequirementOrdinaryBytesUnchanged(t *testing.T) {
	for _, tc := range []struct {
		name string
		make func([]byte) *NativeRequest
		body string
	}{
		{"messages-function", Anthropic, `{ "messages":[], "tools":[{"name":"computer_use","input_schema":{"properties":{"computerUse":{"type":"string"}}}}] }`},
		{"messages-builtin", Anthropic, `{"tools":[{"type":"web_search_20250305","name":"web_search"}]}`},
		{"responses-function", Responses, `{"tools":[{"type":"function","name":"computer_use_preview","parameters":{"description":"computer"}}]}`},
		{"responses-builtin", Responses, `{"tools":[{"type":"web_search_preview"}],"input":"computer_use_preview"}`},
		{"responses-future-ordinary", Responses, `{"tools":[{"type":"synthetic_noncomputer_builtin"}]}`},
		{"gemini-function", func(b []byte) *NativeRequest { return Gemini(b, "synthetic-model", true) }, `{"tools":{"function_declarations":{"name":"computerUse"},"googleSearch":{}},"opaque":{"computerUse":{}}}`},
		{"no-tools", Responses, ` {"input":"computer_use_preview", "metadata":{"type":"computer"}, "opaque":1e+03} `},
		{"null-tools", Anthropic, `{"tools":null}`},
		{"empty-tools", Responses, `{"tools":[]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := tc.make([]byte(tc.body))
			required, err := r.ComputerUseRequirement(context.Background())
			if err != nil || required.Required || !bytes.Equal(r.Body(), []byte(tc.body)) {
				t.Fatal("ordinary native compatibility changed")
			}
		})
	}
}

// Test-only schema ownership: no public factory or production schema is added.
// It accepts only one exact synthetic request and the internally supplied tuple.
type cuaC26R2Schema struct {
	body     []byte
	required models.ComputerUseRequirement
	calls    int
}

func (s *cuaC26R2Schema) ValidateRequest(_ context.Context, body []byte, required models.ComputerUseRequirement) error {
	s.calls++
	if !bytes.Equal(body, s.body) || required != s.required {
		return errNative
	}
	return nil
}
func (*cuaC26R2Schema) ValidateResponse(context.Context, []byte, models.ComputerUseRequirement) error {
	return errNative
}
func (*cuaC26R2Schema) NewStream(context.Context, models.ComputerUseRequirement) (ComputerUseStream, error) {
	return nil, errNative
}

func TestCUAC26R2RequirementAcceptedSchemaControl(t *testing.T) {
	for _, stream := range []bool{false, true} {
		body := []byte(`{"tools":[{"type":"computer_use_preview"}]}`)
		r := Responses(body)
		r.required = models.ComputerUseRequirement{Required: true, Protocol: models.NativeResponses, Action: "generate", Stream: stream, ToolProtocol: "synthetic-c26-r2", VendorVersion: "synthetic-v1"}
		schema := &cuaC26R2Schema{body: body, required: r.required}
		r.schema = schema
		required, err := r.ComputerUseRequirement(context.Background())
		if err != nil || required != r.required || schema.calls != 1 || !bytes.Equal(r.Body(), body) {
			t.Fatal("existing internally qualified schema path changed")
		}
	}
}
