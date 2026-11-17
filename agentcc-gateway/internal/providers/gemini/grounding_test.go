package gemini

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// groundedBody is a generateContent response grounded with Google Search:
// four search queries, two web sources, the supports that tie the answer
// to them and the Search Suggestion chip (searchEntryPoint), whose
// renderedContent is HTML the caller must display as given.
const groundedBody = `{
  "candidates": [{
    "content": {"role": "model", "parts": [{"text": "Spain won Euro 2024, beating England 2-1 in the final."}]},
    "finishReason": "STOP",
    "groundingMetadata": {
      "webSearchQueries": ["euro 2024 winner", "euro 2024 final score", "who won euro 2024", "euro 2024 final result"],
      "searchEntryPoint": {"renderedContent": "<style>.container{align-items:center;border-radius:999px}</style><div class=\"container\"><a class=\"chip\" href=\"https://www.google.com/search?q=euro+2024+winner&amp;client=app\">euro 2024 winner</a></div>"},
      "groundingChunks": [
        {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/a", "title": "uefa.com"}},
        {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/b", "title": "bbc.com"}}
      ],
      "groundingSupports": [
        {"segment": {"startIndex": 0, "endIndex": 54, "text": "Spain won Euro 2024, beating England 2-1 in the final."}, "groundingChunkIndices": [0, 1], "confidenceScores": [0.98, 0.95]}
      ]
    }
  }],
  "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 15, "totalTokenCount": 27},
  "modelVersion": "gemini-3-pro"
}`

const groundedText = "Spain won Euro 2024, beating England 2-1 in the final."

func decodeGemini(t *testing.T, body string) *geminiResponse {
	t.Helper()
	var r geminiResponse
	if err := json.Unmarshal([]byte(body), &r); err != nil {
		t.Fatal(err)
	}
	return &r
}

// groundingAnnotationOf is the grounding_metadata annotation of the
// translated message, decoded.
func groundingAnnotationOf(t *testing.T, annotations json.RawMessage) json.RawMessage {
	t.Helper()
	var list []struct {
		Type              string          `json:"type"`
		GroundingMetadata json.RawMessage `json:"grounding_metadata"`
	}
	if err := json.Unmarshal(annotations, &list); err != nil {
		t.Fatalf("annotations %s: %v", annotations, err)
	}
	for _, a := range list {
		if a.Type == groundingAnnotationType {
			return a.GroundingMetadata
		}
	}
	t.Fatalf("no %s annotation in %s", groundingAnnotationType, annotations)
	return nil
}

func canonical(t *testing.T, raw []byte) string {
	t.Helper()
	var v any
	if err := json.Unmarshal(raw, &v); err != nil {
		t.Fatal(err)
	}
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// The grounding counts reach the usage: Gemini 3 bills each of the four
// queries, Gemini 2.5 the one grounded prompt.
func TestGroundingCountsIntoServerToolUsage(t *testing.T) {
	out := translateResponse(decodeGemini(t, groundedBody), "gemini-3-pro")
	stu := out.Usage.ServerToolUse
	if stu == nil || stu.WebSearchQueries != 4 || stu.GroundedPrompts != 1 || stu.MapsGroundedPrompts != 0 {
		t.Fatalf("server_tool_use = %+v, want 4 queries and 1 grounded prompt", stu)
	}
	if out.Usage.PromptTokens != 12 || out.Usage.CompletionTokens != 15 {
		t.Fatalf("token usage changed: %+v", out.Usage)
	}

	// Queries are summed over the candidates; the prompt is one.
	two := decodeGemini(t, groundedBody)
	two.Candidates = append(two.Candidates, two.Candidates[0])
	if stu := translateResponse(two, "gemini-3-pro").Usage.ServerToolUse; stu.WebSearchQueries != 8 || stu.GroundedPrompts != 1 {
		t.Fatalf("two candidates = %+v, want 8 queries, 1 prompt", stu)
	}

	// Maps grounding: a maps source, and no search queries.
	maps := decodeGemini(t, `{"candidates":[{"content":{"parts":[{"text":"Try Tartine."}]},"groundingMetadata":{
		"groundingChunks":[{"maps":{"uri":"https://maps.google.com/?cid=1","title":"Tartine","placeId":"places/1"}}],
		"googleMapsWidgetContextToken":"widgetcontent/abc"}}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":3,"totalTokenCount":8}}`)
	if stu := translateResponse(maps, "gemini-2.5-flash").Usage.ServerToolUse; stu == nil || stu.MapsGroundedPrompts != 1 || stu.GroundedPrompts != 0 || stu.WebSearchQueries != 0 {
		t.Fatalf("maps = %+v, want one maps-grounded prompt only", stu)
	}

	// With dynamic retrieval a response the model answered without a source
	// is not a grounded prompt, though it may list the queries it ran.
	unsourced := decodeGemini(t, `{"candidates":[{"content":{"parts":[{"text":"4"}]},"groundingMetadata":{"webSearchQueries":["2+2"]}}],
		"usageMetadata":{"promptTokenCount":3,"candidatesTokenCount":1,"totalTokenCount":4}}`)
	if stu := translateResponse(unsourced, "gemini-2.5-flash").Usage.ServerToolUse; stu == nil || stu.GroundedPrompts != 0 || stu.WebSearchQueries != 1 {
		t.Fatalf("unsourced = %+v, want 1 query and no grounded prompt", stu)
	}
}

func TestNoGroundingNoServerToolUsage(t *testing.T) {
	out := translateResponse(decodeGemini(t, `{"candidates":[{"content":{"parts":[{"text":"hi"}]},"finishReason":"STOP"}],
		"usageMetadata":{"promptTokenCount":1,"candidatesTokenCount":1,"totalTokenCount":2}}`), "gemini-3-pro")
	if out.Usage.ServerToolUse != nil || out.Choices[0].Message.Annotations != nil {
		t.Fatalf("ungrounded response = usage %+v, annotations %s", out.Usage.ServerToolUse, out.Choices[0].Message.Annotations)
	}
}

// The Search Suggestion (searchEntryPoint) must be shown as Google sent it,
// with the Grounded Results unmodified and nothing interspersed (Gemini API
// terms). The translated message carries the candidate's groundingMetadata
// whole, as one annotation, and its text exactly as the model wrote it; the
// native generateContent pass-through returns the upstream's bytes.
func TestSearchEntryPointPreserved(t *testing.T) {
	out := translateResponse(decodeGemini(t, groundedBody), "gemini-3-pro")
	msg := out.Choices[0].Message
	var text string
	if err := json.Unmarshal(msg.Content, &text); err != nil || text != groundedText {
		t.Fatalf("content = %s, want the model's text unchanged", msg.Content)
	}
	got := groundingAnnotationOf(t, msg.Annotations)

	var want struct {
		Candidates []struct {
			GroundingMetadata json.RawMessage `json:"groundingMetadata"`
		} `json:"candidates"`
	}
	if err := json.Unmarshal([]byte(groundedBody), &want); err != nil {
		t.Fatal(err)
	}
	if canonical(t, got) != canonical(t, want.Candidates[0].GroundingMetadata) {
		t.Fatalf("grounding metadata changed:\n got %s\nwant %s", got, want.Candidates[0].GroundingMetadata)
	}
	var sep struct {
		SearchEntryPoint struct {
			RenderedContent string `json:"renderedContent"`
		} `json:"searchEntryPoint"`
	}
	if err := json.Unmarshal(got, &sep); err != nil {
		t.Fatal(err)
	}
	const chip = `<style>.container{align-items:center;border-radius:999px}</style><div class="container"><a class="chip" href="https://www.google.com/search?q=euro+2024+winner&amp;client=app">euro 2024 winner</a></div>`
	if sep.SearchEntryPoint.RenderedContent != chip {
		t.Fatalf("renderedContent = %q, want the chip as sent", sep.SearchEntryPoint.RenderedContent)
	}

	// Through the whole chat response, as the client decodes it.
	wire, err := json.Marshal(out)
	if err != nil {
		t.Fatal(err)
	}
	var client struct {
		Choices []struct {
			Message struct {
				Annotations json.RawMessage `json:"annotations"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.Unmarshal(wire, &client); err != nil {
		t.Fatal(err)
	}
	if canonical(t, groundingAnnotationOf(t, client.Choices[0].Message.Annotations)) != canonical(t, want.Candidates[0].GroundingMetadata) {
		t.Fatal("the chat response the client reads lost the grounding metadata")
	}

	// Native pass-through: the generateContent body comes back byte for byte.
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(groundedBody))
	}))
	defer upstream.Close()
	p, err := New("gemini", config.ProviderConfig{BaseURL: upstream.URL, APIKey: "test-key"})
	if err != nil {
		t.Fatal(err)
	}
	body, status, err := p.GenerateContent(context.Background(), "gemini-3-pro",
		[]byte(`{"contents":[{"parts":[{"text":"who won euro 2024"}]}],"tools":[{"google_search":{}}]}`), nil)
	if err != nil || status != http.StatusOK {
		t.Fatalf("GenerateContent = %d, %v", status, err)
	}
	if !bytes.Equal(body, []byte(groundedBody)) {
		t.Fatalf("native body changed:\n got %s\nwant %s", body, groundedBody)
	}
}
