package requestview

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// rewriteMessage sets message i of the chat request to text, as pii-detection's
// remediation does.
func rewriteMessage(t *testing.T, req *models.ChatCompletionRequest, i int, text string) {
	t.Helper()
	if req == nil || i >= len(req.Messages) {
		t.Fatalf("no message %d to rewrite in %+v", i, req)
	}
	req.Messages[i].Content, _ = json.Marshal(text)
}

// The checks read each text of an embeddings input as a user message, and a
// rewrite goes back into the input the provider gets, keeping its shape: a
// string stays a string, a list keeps its other items.
func TestEmbedding_ReadsAndRewritesTheInput(t *testing.T) {
	for _, tc := range []struct{ input, rewritten, want string }{
		{`"ssn 123-45-6789"`, "ssn ***", `"ssn ***"`},
		{`["hello","ssn 123-45-6789"]`, "ssn ***", `["hello","ssn ***"]`},
	} {
		req := &models.EmbeddingRequest{Model: "e", Input: json.RawMessage(tc.input)}
		v := Embedding(req)
		chat, reason := v.ChatRequest(context.Background())
		if reason != "" || chat == nil {
			t.Fatalf("%s: unread (%q)", tc.input, reason)
		}
		last := len(chat.Messages) - 1
		if chat.Messages[last].Role != "user" {
			t.Errorf("%s: role %q, want user", tc.input, chat.Messages[last].Role)
		}
		rewriteMessage(t, chat, last, tc.rewritten)
		if err := v.ApplyChecks(); err != nil {
			t.Fatalf("%s: %v", tc.input, err)
		}
		if string(req.Input) != tc.want {
			t.Errorf("input %s became %s, want %s", tc.input, req.Input, tc.want)
		}
		// A second call has nothing more to write.
		if err := v.ApplyChecks(); err != nil || string(req.Input) != tc.want {
			t.Errorf("%s: again: %v, input %s", tc.input, err, req.Input)
		}
	}
}

// Token ids, or an input of another type, are not text the checks can
// read: nothing is read, and the reason says why.
func TestEmbedding_TokenIDsAreNotRead(t *testing.T) {
	for _, input := range []string{`[1,2,3]`, `[[1,2],[3]]`, `["a",1]`, `{"text":"a"}`, `7`} {
		v := Embedding(&models.EmbeddingRequest{Model: "e", Input: json.RawMessage(input)})
		chat, reason := v.ChatRequest(context.Background())
		if chat != nil || !strings.Contains(reason, "cannot read this request") {
			t.Errorf("%s: read %+v, reason %q; want nothing read and a reason", input, chat, reason)
		}
		if err := v.ApplyChecks(); err != nil {
			t.Errorf("%s: ApplyChecks on an unread request: %v", input, err)
		}
	}
}

// A transcription's prompt is read, and the request still says it cannot
// all be read: its audio is not text. An OCR request is not read at all.
func TestAudioAndOCR_SayWhatIsNotRead(t *testing.T) {
	v := Transcription(&models.TranscriptionRequest{Model: "w", Prompt: "names: Ada"})
	chat, reason := v.ChatRequest(context.Background())
	if chat == nil || len(chat.Messages) != 1 || !strings.Contains(reason, "audio file") {
		t.Errorf("transcription: read %+v, reason %q; want the prompt read and the audio named", chat, reason)
	}
	v = Translation(&models.TranslationRequest{Model: "w"})
	if chat, reason := v.ChatRequest(context.Background()); chat != nil || !strings.Contains(reason, "audio file") {
		t.Errorf("translation with no prompt: read %+v, reason %q; want nothing read", chat, reason)
	}
	ocr := OCR(&models.OCRRequest{Model: "o", Document: models.OCRDocument{Type: "document_url", DocumentURL: "https://x.invalid/a.pdf"}})
	if chat, reason := ocr.ChatRequest(context.Background()); chat != nil || !strings.Contains(reason, "document (document_url)") {
		t.Errorf("OCR: read %+v, reason %q; want nothing read and the document named", chat, reason)
	}
}

// Rerank reads its query and each document, and a rewrite of a document goes
// into that document.
func TestRerank_ReadsQueryAndDocuments(t *testing.T) {
	req := &models.RerankRequest{Model: "r", Query: "which?", Documents: []string{"a", "ssn 123-45-6789"}}
	v := Rerank(req)
	chat, _ := v.ChatRequest(context.Background())
	if len(chat.Messages) != 3 {
		t.Fatalf("%d messages, want the query and two documents", len(chat.Messages))
	}
	rewriteMessage(t, chat, 2, "ssn ***")
	if err := v.ApplyChecks(); err != nil {
		t.Fatal(err)
	}
	if req.Documents[1] != "ssn ***" || req.Documents[0] != "a" || req.Query != "which?" {
		t.Errorf("request %+v, want only the second document rewritten", req)
	}
}

// A rewrite that cannot be written is an error, and the request is left as
// it was: content that is not text, a message count the check changed.
func TestFields_RefusesWhatItCannotWrite(t *testing.T) {
	req := &models.ImageRequest{Model: "i", Prompt: "ssn 123-45-6789"}
	v := Image(req)
	chat, _ := v.ChatRequest(context.Background())
	chat.Messages[0].Content = json.RawMessage(`{"not":"content"}`)
	if err := v.ApplyChecks(); err == nil || req.Prompt != "ssn 123-45-6789" {
		t.Errorf("content that is not text: %v, prompt %q; want an error and the prompt as sent", err, req.Prompt)
	}
	v = Image(req)
	chat, _ = v.ChatRequest(context.Background())
	chat.Messages = append(chat.Messages, models.Message{Role: "user"})
	if err := v.ApplyChecks(); err == nil {
		t.Error("a check that added a message: no error")
	}
	// A field with no setter cannot be rewritten.
	f := NewFields("m", func() ([]Field, string) { return []Field{{At: "x", Text: "ssn 1"}}, "" })
	chat, _ = f.ChatRequest(context.Background())
	rewriteMessage(t, chat, 0, "ssn ***")
	if err := f.ApplyChecks(); err == nil || !strings.Contains(err.Error(), "cannot be written") {
		t.Errorf("a field without Set: %v, want cannot be written", err)
	}
}

// Search reads its query, a string or each of a list, and the rewrite keeps
// the shape.
func TestSearch_ReadsAndRewritesTheQuery(t *testing.T) {
	req := &models.SearchRequest{SearchProvider: "s", QueryRaw: json.RawMessage(`["weather","ssn 123-45-6789"]`)}
	v := Search(req)
	chat, _ := v.ChatRequest(context.Background())
	rewriteMessage(t, chat, 1, "ssn ***")
	if err := v.ApplyChecks(); err != nil {
		t.Fatal(err)
	}
	if string(req.QueryRaw) != `["weather","ssn ***"]` {
		t.Errorf("query became %s", req.QueryRaw)
	}
}

// A video job's prompt is read and rewritten in place.
func TestVideoPrompt_RewritesThePrompt(t *testing.T) {
	prompt := "ssn 123-45-6789"
	v := VideoPrompt("v", &prompt)
	chat, _ := v.ChatRequest(context.Background())
	rewriteMessage(t, chat, 0, "ssn ***")
	if err := v.ApplyChecks(); err != nil || prompt != "ssn ***" {
		t.Errorf("%v, prompt %q", err, prompt)
	}
}
