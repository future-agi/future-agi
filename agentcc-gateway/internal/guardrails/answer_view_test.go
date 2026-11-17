package guardrails

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func answerOf(t *testing.T, raw string) *models.ChatCompletionResponse {
	t.Helper()
	var resp models.ChatCompletionResponse
	if err := json.Unmarshal([]byte(raw), &resp); err != nil {
		t.Fatal(err)
	}
	return &resp
}

// An answer with no refusal or transcript is read as it is; one with them is
// read with each as a choice's content of its own, after its own choices,
// and the answer itself is not changed by reading it.
func TestAnswerView_ReadsRefusalAndTranscriptAsText(t *testing.T) {
	plain := answerOf(t, `{"choices":[{"index":0,"message":{"role":"assistant","content":"hi"}}]}`)
	if v := newAnswerView(plain); v.resp != plain {
		t.Fatal("a plain answer was copied")
	}
	resp := answerOf(t, `{"choices":[{"index":0,"message":{"role":"assistant","content":null,"refusal":"no way"}},{"index":1,"message":{"role":"assistant","audio":{"id":"a","data":"QQ==","transcript":"said this"}}}]}`)
	v := newAnswerView(resp)
	if len(v.resp.Choices) != 4 || len(resp.Choices) != 2 {
		t.Fatalf("view has %d choices (answer %d), want 4 and 2", len(v.resp.Choices), len(resp.Choices))
	}
	for i, want := range []string{"no way", "said this"} {
		got, _ := ContentText(v.resp.Choices[2+i].Message.Content)
		if got != want {
			t.Errorf("added choice %d reads %q, want %q", i, got, want)
		}
	}
	if err := v.writeBack(); err != nil || *resp.Choices[0].Message.Refusal != "no way" {
		t.Fatalf("an unchanged view wrote back %v, refusal %q", err, *resp.Choices[0].Message.Refusal)
	}
}

// A check's mask of a refusal goes into the refusal, and of the answer's own
// content into its content; a mask of a transcript cannot be applied to the
// audio, and is an error.
func TestAnswerView_WritesBackARefusalRewrite(t *testing.T) {
	resp := answerOf(t, `{"choices":[{"index":0,"message":{"role":"assistant","content":"ssn 1","refusal":"ssn 2"}}]}`)
	v := newAnswerView(resp)
	v.resp.Choices[0].Message.Content, _ = json.Marshal("ssn ***")
	v.resp.Choices[1].Message.Content, _ = json.Marshal("ssn ****")
	if err := v.writeBack(); err != nil {
		t.Fatal(err)
	}
	content, _ := ContentText(resp.Choices[0].Message.Content)
	if content != "ssn ***" || *resp.Choices[0].Message.Refusal != "ssn ****" {
		t.Fatalf("answer after write-back: content %q refusal %q", content, *resp.Choices[0].Message.Refusal)
	}

	audio := answerOf(t, `{"choices":[{"index":0,"message":{"role":"assistant","audio":{"id":"a","data":"QQ==","transcript":"ssn 1"}}}]}`)
	av := newAnswerView(audio)
	av.resp.Choices[1].Message.Content, _ = json.Marshal("ssn ***")
	if err := av.writeBack(); err != errTranscriptRewrite {
		t.Fatalf("a transcript rewrite wrote back with %v, want errTranscriptRewrite", err)
	}
}

// Only audio with no transcript is unreadable.
func TestUnreadableAnswer(t *testing.T) {
	for raw, want := range map[string]bool{
		`{"choices":[{"index":0,"message":{"role":"assistant","refusal":"no"}}]}`:                                    false,
		`{"choices":[{"index":0,"message":{"role":"assistant","audio":{"id":"a","data":"QQ==","transcript":"x"}}}]}`: false,
		`{"choices":[{"index":0,"message":{"role":"assistant","audio":{"id":"a","data":"QQ=="}}}]}`:                  true,
		`{"choices":[{"index":0,"message":{"role":"assistant","content":"hi"}}]}`:                                    false,
	} {
		if got := unreadableAnswer(answerOf(t, raw)); got != want {
			t.Errorf("%s: unreadable %v, want %v", raw, got, want)
		}
	}
}
