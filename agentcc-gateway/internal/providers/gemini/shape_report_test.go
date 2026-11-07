package gemini

import (
	"context"
	"encoding/json"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/providers/common"
)

// The contract fixtures (contract_test.go) exercise most unknown-shape
// reports end to end. These cover the paths no documented response reaches.

func newRecordingReporter() (*common.ShapeReporter, *common.ShapeRecorder) {
	ctx, rec := common.WithShapeRecorder(context.Background())
	return common.NewShapeReporter(ctx, adapterName, "test-gemini"), rec
}

func shapeValues(rec *common.ShapeRecorder) []string {
	var out []string
	for _, s := range rec.Shapes() {
		out = append(out, string(s.Kind)+":"+s.Value)
	}
	return out
}

func TestParseStreamData_ReportsMalformedChunk(t *testing.T) {
	shape, rec := newRecordingReporter()
	s := newStreamState("gemini-3.8-flash")
	s.shape = shape

	if _, err := s.parseStreamData(`{"candidates": [`); err == nil {
		t.Fatal("parseStreamData accepted malformed JSON")
	}
	if got, want := shapeValues(rec), []string{"malformed_payload:stream_chunk"}; !reflect.DeepEqual(got, want) {
		t.Errorf("reports = %q, want %q", got, want)
	}
}

func TestParseStreamData_NamesCandidatelessChunks(t *testing.T) {
	for _, tc := range []struct{ data, want string }{
		{`{"promptFeedback":{"blockReason":"SAFETY"},"usageMetadata":{"promptTokenCount":3},"responseId":"r"}`, "unknown_event:promptFeedback"},
		{`{"usageMetadata":{"promptTokenCount":3},"modelVersion":"m"}`, "unknown_event:modelVersion,usageMetadata"},
	} {
		shape, rec := newRecordingReporter()
		s := newStreamState("gemini-3.8-flash")
		s.shape = shape
		chunk, err := s.parseStreamData(tc.data)
		if err != nil {
			t.Fatalf("parseStreamData(%s): %v", tc.data, err)
		}
		if len(chunk.Choices) != 0 {
			t.Errorf("parseStreamData(%s) produced choices; the report assumes the caller drops this chunk", tc.data)
		}
		if got := shapeValues(rec); !reflect.DeepEqual(got, []string{tc.want}) {
			t.Errorf("parseStreamData(%s) reports = %q, want [%q]", tc.data, got, tc.want)
		}
	}
}

func TestTranslateResponse_ReportsKnownButUntranslatedParts(t *testing.T) {
	shape, rec := newRecordingReporter()
	resp := &geminiResponse{Candidates: []geminiCandidate{{
		Content: geminiContent{Parts: []geminiPart{
			{Text: "here it is"},
			{FileData: &geminiFileData{MimeType: "image/png", FileURI: "gs://bucket/out.png"}},
		}},
		FinishReason: "STOP",
	}}}

	out := translateResponse(resp, "gemini-3.8-flash", shape)

	if got, want := shapeValues(rec), []string{"unknown_block:fileData"}; !reflect.DeepEqual(got, want) {
		t.Errorf("reports = %q, want %q", got, want)
	}
	if got := extractTextContent(out.Choices[0].Message.Content); got != "here it is" {
		t.Errorf("content = %q; reporting must not change the translation", got)
	}
}

func TestReportBlankParts(t *testing.T) {
	for _, tc := range []struct {
		name, body string
		want       []string
	}{
		{"empty text part is not a new kind", `{"candidates":[{"content":{"parts":[{"text":"hi"},{"text":""}]}}]}`, nil},
		{"empty part is not a new kind", `{"candidates":[{"content":{"parts":[{}]}}]}`, nil},
		{"signature-only text part is known", `{"candidates":[{"content":{"parts":[{"text":"","thoughtSignature":"c2ln"}]}}]}`, nil},
		{"unknown kind alone", `{"candidates":[{"content":{"parts":[{"text":"a"},{"toolCall":{"toolType":"GOOGLE_SEARCH_WEB"}}]}}]}`,
			[]string{"unknown_block:toolCall"}},
		{"unknown keys are named together", `{"candidates":[{"content":{"parts":[{"text":"","partMetadata":{},"executableCode":{"code":"1"}}]}}]}`,
			[]string{"unknown_block:executableCode,partMetadata"}},
		{"unknown key beside a known one is left to the field audit", `{"candidates":[{"content":{"parts":[{"text":"a","executableCode":{"code":"1"}}]}}]}`, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var resp geminiResponse
			if err := json.Unmarshal([]byte(tc.body), &resp); err != nil {
				t.Fatal(err)
			}
			shape, rec := newRecordingReporter()
			reportBlankParts(shape, &resp, []byte(tc.body))
			if got := shapeValues(rec); !reflect.DeepEqual(got, tc.want) {
				t.Errorf("reports = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestFinishReason_ReportsOnlyTheFallback(t *testing.T) {
	for _, tc := range []struct {
		reason       string
		hasToolCalls bool
		want         string
		reported     []string
	}{
		{"STOP", false, "stop", nil},
		{"MAX_TOKENS", false, "length", nil},
		{"SAFETY", false, "content_filter", nil},
		{"CONTINUATION", false, "stop", []string{"unmapped_finish_reason:CONTINUATION"}},
		{"PROHIBITED_CONTENT", false, "stop", []string{"unmapped_finish_reason:PROHIBITED_CONTENT"}},
		// A tool-calling turn is tool_calls whatever the reason; the reason is
		// not consulted, so nothing fell back.
		{"MALFORMED_FUNCTION_CALL", true, "tool_calls", nil},
	} {
		shape, rec := newRecordingReporter()
		if got := finishReason(shape, tc.reason, tc.hasToolCalls); got != tc.want {
			t.Errorf("finishReason(%q, %v) = %q, want %q", tc.reason, tc.hasToolCalls, got, tc.want)
		}
		if got := shapeValues(rec); !reflect.DeepEqual(got, tc.reported) {
			t.Errorf("finishReason(%q, %v) reports = %q, want %q", tc.reason, tc.hasToolCalls, got, tc.reported)
		}
		if got := mapGeminiFinishReasonWithToolCalls(tc.reason, tc.hasToolCalls); got != tc.want {
			t.Errorf("mapGeminiFinishReasonWithToolCalls(%q, %v) = %q, want %q", tc.reason, tc.hasToolCalls, got, tc.want)
		}
	}
}
