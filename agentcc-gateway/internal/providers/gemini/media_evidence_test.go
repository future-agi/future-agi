package gemini

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"image"
	"image/png"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func imageFixture(t *testing.T) string {
	t.Helper()
	var b bytes.Buffer
	if err := png.Encode(&b, image.NewRGBA(image.Rect(0, 0, 2, 3))); err != nil {
		t.Fatal(err)
	}
	return base64.StdEncoding.EncodeToString(b.Bytes())
}

func TestDimensionalGeminiAllCandidatesBeforeTranslation(t *testing.T) {
	part := geminiPart{InlineData: &geminiInlineData{MimeType: "image/png", Data: imageFixture(t)}}
	resp := &geminiResponse{ModelVersion: "synthetic-version", Candidates: []geminiCandidate{
		{FinishReason: "STOP", Content: geminiContent{Parts: []geminiPart{part, part}}},
		{FinishReason: "STOP", Content: geminiContent{Parts: []geminiPart{part}}},
	}}
	got := translateResponse(resp, "synthetic-request")
	if got.Usage == nil || got.Usage.Media == nil {
		t.Fatal("lost evidence")
	}
	m := got.Usage.Media
	if m.OutputImages == nil || *m.OutputImages != 3 || m.ModelVersion != "synthetic-version" {
		t.Fatalf("media=%+v", m)
	}
	if got.Usage.HasPromptTokens() || got.Usage.HasCompletionTokens() {
		t.Fatal("media invented token zeroes")
	}
	wire, _ := json.Marshal(got.Usage)
	if bytes.Contains(wire, []byte("prompt_tokens")) || bytes.Contains(wire, []byte("completion_tokens")) {
		t.Fatalf("missing counts became wire zeroes: %s", wire)
	}
	if bytes.Contains(wire, []byte("synthetic-version")) || bytes.Contains(wire, []byte(part.InlineData.Data)) {
		t.Fatal("internal media evidence leaked to usage wire")
	}
}

func TestDimensionalGeminiRepeatedStreamStaysUnknown(t *testing.T) {
	data, _ := json.Marshal(&geminiResponse{ModelVersion: "synthetic-version", Candidates: []geminiCandidate{{Content: geminiContent{Parts: []geminiPart{{InlineData: &geminiInlineData{MimeType: "image/png", Data: imageFixture(t)}}}}}}})
	s := newStreamState("synthetic-request")
	first, err := s.parseStreamData(string(data))
	if err != nil {
		t.Fatal(err)
	}
	second, err := s.parseStreamData(string(data))
	if err != nil {
		t.Fatal(err)
	}
	u := models.MergeUsage(first.Usage, second.Usage)
	if u.Media.OutputImages != nil || u.Media.ObservedImageParts != 1 || !u.Media.Stream {
		t.Fatalf("stream became a fabricated generation count: %+v", u.Media)
	}
	var empty models.Usage
	_ = json.Unmarshal([]byte(`{}`), &empty)
	u = models.MergeUsage(u, &empty)
	if u.Media == nil || u.Media.ObservedImageParts != 1 {
		t.Fatal("trailing empty usage erased media")
	}
	other := *u.Media
	other.ModelVersion = "other-version"
	if m := models.MergeMediaEvidence(u.Media, &other); !m.IdentityConflict {
		t.Fatal("identity disagreement dropped")
	}
}

func TestDimensionalLyriaAudioIsNotASongCounter(t *testing.T) {
	resp := &geminiResponse{ModelVersion: "synthetic-lyria", Candidates: []geminiCandidate{{FinishReason: "STOP", Content: geminiContent{Parts: []geminiPart{{InlineData: &geminiInlineData{MimeType: "audio/wav", Data: "c3ludGhldGlj"}}}}}}}
	got := translateResponse(resp, "synthetic/lyria")
	if got.Usage == nil || got.Usage.Media.ObservedAudioParts != 1 || got.Usage.HasReportedCost() || got.Usage.HasCompletionTokens() {
		t.Fatalf("audio invented priced evidence: %+v", got.Usage)
	}
}

func TestDimensionalGeminiInvalidImageIsUnknown(t *testing.T) {
	for _, data := range []string{"", "not base64", "YWJj"} {
		resp := &geminiResponse{Candidates: []geminiCandidate{{FinishReason: "STOP", Content: geminiContent{Parts: []geminiPart{{InlineData: &geminiInlineData{MimeType: "image/png", Data: data}}}}}}}
		m := collectMedia(resp, false)
		if !m.InvalidImage || m.OutputImages != nil {
			t.Fatalf("invalid image became zero: %+v", m)
		}
	}
}
