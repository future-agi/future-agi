package anthropic

import (
	"regexp"
	"testing"
)

var anthropicToolUseID = regexp.MustCompile(`^[a-zA-Z0-9_-]{1,128}$`)

func TestToolUseIDIsAlwaysSomethingAnAnthropicClientAccepts(t *testing.T) {
	// The middle one is the real shape: the Gemini path packs a base64 thoughtSignature into the
	// id because the OpenAI tool-call struct has no extension slot. A parent-level tool call
	// survives it; a sub-agent's tool result does not, and the session stalls with no error.
	long := "call_0::sig::AY89a1/JuAAAZghixG6rbGRaDDyevOUXtqeCWJ9IyY3PjYbGiEtwX2V2cRUiRUkRjgQn124uGDHVtB/QTIwlHdL8tMp8jSgBawLCPnBi1KdHkO+9zWLvSWqqWcYHanMIvZCI31+WWlVtVxsAPOjsJMx+6gkHTZK7ZV1tC8EFmMVY32ki//QdhvgH+Jc7VzJiHIF69Ckj9PJTmkmOQH8Rznw="
	for _, raw := range []string{
		"call_0",
		"toolu_01A09q90qw90lq917835lq9",
		long,
		"has spaces and+slashes/and=equals",
	} {
		got := toolUseID(raw)
		if !anthropicToolUseID.MatchString(got) {
			t.Errorf("toolUseID(%.40q...) = %q, which no Anthropic client accepts", raw, got)
		}
	}

	// An id that is already legal is handed through untouched, so nothing changes on the paths
	// that were never broken.
	for _, raw := range []string{"call_0", "toolu_01A09q90qw90lq917835lq9", "a-b_C9"} {
		if got := toolUseID(raw); got != raw {
			t.Errorf("toolUseID(%q) = %q, want it unchanged", raw, got)
		}
	}

	// Two calls that differ only in their tail still differ here, because a shortened id carries a
	// hash of the whole original rather than just its readable head.
	a := toolUseID("call_0::sig::" + string(make([]byte, 400)) + "A")
	b := toolUseID("call_0::sig::" + string(make([]byte, 400)) + "B")
	if a == b {
		t.Errorf("two different signatures collapsed to the same id %q", a)
	}
	if toolUseID("") != "" {
		t.Error("an empty id should stay empty rather than becoming a hash")
	}
}

func TestAShortenedIDFindsItsWayBackToWhatTheProviderCalledIt(t *testing.T) {
	// The Gemini path keeps its thoughtSignature in the tool-call id, and Gemini refuses the next
	// turn without it: "Function call is missing a thought_signature in functionCall parts."
	// So the id handed to the client has to be legal AND has to lead back to the original.
	raw := "call_0::sig::AY89a1/JuAAAZghixG6rbGRaDDyevOUXtqeCWJ9IyY3PjYbGiEtwX2V2cRUiRUkRjgQn124uGDHVtB/QTIwlHdL8tMp8jSgBawLCPnBi1KdHkO+9zWLvSWqqWcYHanMIvZCI31+WWlVtVxsAPOjs="
	shortened := toolUseID(raw)
	if shortened == raw {
		t.Fatalf("expected %q to be shortened", raw)
	}
	if got := originalToolUseID(shortened); got != raw {
		t.Errorf("originalToolUseID(%q) = %q, want the original back", shortened, got)
	}

	// An id that was never shortened is handed back untouched rather than treated as missing.
	if got := originalToolUseID("call_0"); got != "call_0" {
		t.Errorf("originalToolUseID(%q) = %q, want it unchanged", "call_0", got)
	}
	// And an id this gateway has never seen is not invented into something else.
	if got := originalToolUseID("toolu_neverseen"); got != "toolu_neverseen" {
		t.Errorf("originalToolUseID of an unknown id = %q, want it unchanged", got)
	}
}
