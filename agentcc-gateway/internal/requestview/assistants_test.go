package requestview

import (
	"context"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// readView reads v and fails the test when it cannot.
func readView(t *testing.T, v *View) []string {
	t.Helper()
	chat, reason := v.ChatRequest(context.Background())
	if chat == nil {
		t.Fatalf("unread: %s", reason)
	}
	var out []string
	for _, m := range chat.Messages {
		s, _ := contentText(m.Content)
		out = append(out, m.Role+": "+s)
	}
	return out
}

// Each Assistants API body is read where the model reads it: instructions
// as system messages, a thread's or run's messages by role (a string, or
// its text parts), tool outputs as tool messages, a vector store search's
// queries, and Gemini embedContent's parts and title.
func TestAssistants_ReadEveryText(t *testing.T) {
	for _, tc := range []struct {
		name string
		v    *View
		want []string
	}{
		{"assistant", Assistant([]byte(`{"model":"gpt-4o","name":"n","description":"d","instructions":"be kind"}`)), []string{"system: be kind"}},
		{"thread", Thread([]byte(`{"messages":[{"role":"user","content":"hi"},{"role":"assistant","content":[{"type":"text","text":"a"},{"type":"image_file","image_file":{"file_id":"f"}},{"type":"text","text":"b"}]}]}`)),
			[]string{"user: hi", "assistant: a\nb"}},
		{"message", ThreadMessage([]byte(`{"role":"user","content":"ssn"}`)), []string{"user: ssn"}},
		{"run", Run([]byte(`{"assistant_id":"a","instructions":"i","additional_instructions":"j","additional_messages":[{"role":"user","content":"k"}]}`)),
			[]string{"system: i", "system: j", "user: k"}},
		{"thread and run", ThreadAndRun([]byte(`{"assistant_id":"a","instructions":"i","thread":{"messages":[{"role":"user","content":"m"}]}}`)),
			[]string{"system: i", "user: m"}},
		{"tool outputs", ToolOutputs([]byte(`{"tool_outputs":[{"tool_call_id":"c1","output":"o1"},{"tool_call_id":"c2","output":"o2"}]}`)),
			[]string{"tool: o1", "tool: o2"}},
		{"vector store search", VectorStoreSearch([]byte(`{"query":["q1","q2"]}`)), []string{"user: q1", "user: q2"}},
		{"embedContent", GeminiEmbedContent([]byte(`{"content":{"parts":{"text":"p"}},"title":"t"}`)).View, []string{"user: p", "user: t"}},
	} {
		got := readView(t, tc.v)
		if strings.Join(got, "|") != strings.Join(tc.want, "|") {
			t.Errorf("%s: read %q, want %q", tc.name, got, tc.want)
		}
	}
}

// A body that does not have its API's shape cannot be read, so a check that
// would block or rewrite it refuses it.
func TestAssistants_UnknownShapesAreNotRead(t *testing.T) {
	for _, body := range []string{
		`[]`,
		`{"messages":"hi"}`,
		`{"messages":[{"role":"user","content":7}]}`,
		`{"messages":[{"role":"user","content":[{"type":"text","text":{"value":"x"}}]}]}`,
	} {
		if chat, reason := Thread([]byte(body)).ChatRequest(context.Background()); chat != nil || reason == "" {
			t.Errorf("%s: read %+v, want it unread", body, chat)
		}
	}
	if chat, _ := VectorStoreSearch([]byte(`{"query":[1]}`)).ChatRequest(context.Background()); chat != nil {
		t.Errorf("a query of numbers was read: %+v", chat)
	}
	if chat, _ := ToolOutputs([]byte(`{"tool_outputs":[{"tool_call_id":"c","output":{"x":1}}]}`)).ChatRequest(context.Background()); chat != nil {
		t.Errorf("an output that is not a string was read: %+v", chat)
	}
}

// A rewrite goes back where it was read, and the rest of the body is sent
// as the client sent it.
func TestAssistants_WriteTheRewrite(t *testing.T) {
	v := Run([]byte(`{"assistant_id":"a","metadata":{"k":"v"},"additional_messages":[{"role":"user","content":[{"type":"text","text":"ssn 123-45-6789"}]}]}`))
	chat, _ := v.ChatRequest(context.Background())
	rewriteMessage(t, chat, 0, "ssn ***")
	if err := v.ApplyChecks(); err != nil {
		t.Fatal(err)
	}
	want := `{"assistant_id":"a","metadata":{"k":"v"},"additional_messages":[{"role":"user","content":[{"type":"text","text":"ssn ***"}]}]}`
	if string(v.Body()) != want {
		t.Errorf("body %s, want %s", v.Body(), want)
	}
}

// The tools an assistant or a run offers are read by name: a function by
// its name, a tool the provider runs by its type.
func TestAssistants_ReadTheTools(t *testing.T) {
	v := Assistant([]byte(`{"tools":[{"type":"code_interpreter"},{"type":"function","function":{"name":"lookup"}}]}`))
	chat, _ := v.ChatRequest(context.Background())
	var names []string
	for _, tool := range chat.Tools {
		names = append(names, tool.Function.Name)
	}
	if strings.Join(names, ",") != "code_interpreter,lookup" {
		t.Errorf("tools %v", names)
	}
}

// The token-count routes answer with a count, not model text: their views
// are NoChatAnswer, as embedContent's is, so the checks on the answer do
// not read their request and a block there is recorded as logged.
// The Messages and generateContent views have a chat answer.
func TestViews_SayWhichHaveNoChatAnswer(t *testing.T) {
	var noAnswer = []models.PassThroughRequest{
		AnthropicCountTokens([]byte(`{}`)), GeminiCountTokens([]byte(`{}`), "m"), GeminiEmbedContent([]byte(`{}`)),
	}
	for _, v := range noAnswer {
		if _, ok := v.(models.NoChatAnswer); !ok {
			t.Errorf("%T is not NoChatAnswer", v)
		}
	}
	for _, v := range []models.PassThroughRequest{Anthropic([]byte(`{}`)), Gemini([]byte(`{}`), "m", false), Responses([]byte(`{}`))} {
		if _, ok := v.(models.NoChatAnswer); ok {
			t.Errorf("%T is NoChatAnswer, but its API answers with model text", v)
		}
	}
}
