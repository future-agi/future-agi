package contracttest

import (
	"testing"
)

func TestParseSSE(t *testing.T) {
	body := ": a comment\r\n" +
		"event: message_start\r\n" +
		"data: {\"type\":\"message_start\"}\r\n" +
		"\r\n" +
		"event: ping\n" +
		"id: 7\n" +
		"retry: 100\n" +
		"data:{\"type\": \"ping\"}\n" +
		"\n" +
		"event: no_data\n" +
		"\n" +
		"data: line one\n" +
		"data: line two\n" +
		"\n" +
		"data: [DONE]" // no trailing blank line

	got := ParseSSE([]byte(body))
	want := []Event{
		{Name: "message_start", Data: []byte(`{"type":"message_start"}`)},
		{Name: "ping", Data: []byte(`{"type": "ping"}`)},
		{Data: []byte("line one\nline two")},
		{Data: []byte("[DONE]")},
	}
	if len(got) != len(want) {
		t.Fatalf("got %d events %q, want %d", len(got), got, len(want))
	}
	for i := range want {
		if got[i].Name != want[i].Name || string(got[i].Data) != string(want[i].Data) {
			t.Errorf("event %d = {%q %q}, want {%q %q}", i, got[i].Name, got[i].Data, want[i].Name, want[i].Data)
		}
	}
}

func TestEventType(t *testing.T) {
	tests := []struct {
		ev   Event
		want string
	}{
		{Event{Name: "content_block_delta", Data: []byte(`{"type":"other"}`)}, "content_block_delta"},
		{Event{Data: []byte(`{"type":"response.created"}`)}, "response.created"},
		{Event{Data: []byte(`{"object":"chat.completion.chunk"}`)}, ""},
		{Event{Data: []byte(`[DONE]`)}, ""},
	}
	for _, tc := range tests {
		if got := tc.ev.Type(); got != tc.want {
			t.Errorf("Type() of %q = %q, want %q", tc.ev.Data, got, tc.want)
		}
	}
}

func TestAssertKnownEventTypes(t *testing.T) {
	events := ParseSSE([]byte("event: a\ndata: {}\n\nevent: b\ndata: {}\n\nevent: c\ndata: {}\n\n"))

	ft := runFake(func(tb *fakeTB) { AssertKnownEventTypes(tb, events, "a", "b", "c") })
	if len(ft.errors) != 0 {
		t.Errorf("unexpected errors: %q", ft.errors)
	}
	ft = runFake(func(tb *fakeTB) { AssertKnownEventTypes(tb, events, "a") })
	if len(ft.errors) != 1 {
		t.Errorf("want one error naming b and c, got %q", ft.errors)
	}
}
