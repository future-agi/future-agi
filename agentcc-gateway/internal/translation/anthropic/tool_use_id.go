package anthropic

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"sync"
)

// toolUseIDMax is the Anthropic Messages API's own bound on tool_use.id: at most 128 characters
// of [a-zA-Z0-9_-]. A client that speaks this API rejects or silently mishandles anything else.
const toolUseIDMax = 128

// toolUseID makes a canonical tool-call id safe to hand to an Anthropic client.
//
// Providers use this field as a place to keep things. The Gemini path packs the thoughtSignature
// into it, because the OpenAI tool-call struct has no extension slot, which produces ids of four
// hundred characters containing '/', '+' and '='. A parent-level tool call survives that; a
// sub-agent's tool result does not, and the session stalls with no error at all because the
// routing simply never matches. Measured against a shim that emits clean ids, the same sub-agent
// completes and reports back.
//
// Uniqueness is preserved rather than assumed: an id that has to be shortened keeps its readable
// head and carries a hash of the whole original, so two calls that differed only in their tail
// still differ here.
func toolUseID(raw string) string {
	if raw == "" {
		return raw
	}

	var safe strings.Builder
	safe.Grow(len(raw))
	for _, r := range raw {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9', r == '_', r == '-':
			safe.WriteRune(r)
		default:
			safe.WriteByte('_')
		}
	}
	cleaned := safe.String()
	if len(cleaned) <= toolUseIDMax && cleaned == raw {
		return raw
	}
	sum := sha256.Sum256([]byte(raw))
	digest := hex.EncodeToString(sum[:])[:16]
	head := cleaned
	if limit := toolUseIDMax - len(digest) - 1; len(head) > limit {
		head = head[:limit]
	}
	shortened := head + "_" + digest
	// Remembered only when it actually changed, because an id that passed through untouched
	// needs no way back.
	rememberToolUseID(shortened, raw)
	return shortened
}

// Providers keep things in the tool-call id that an Anthropic client is not allowed to carry.
// The Gemini path keeps the thoughtSignature there, and that signature has to return verbatim on
// the next request or Gemini refuses the turn with "missing thought_signature". So the shortened
// id the client is given is remembered against the original, and recovered when the client echoes
// it back as tool_use_id.
//
// Bounded, because a gateway that remembers every tool call ever made is a leak. A conversation
// echoes its ids back on the very next request, so the window this has to cover is one turn, and
// the bound is generous against that.
const toolUseIDMemory = 4096

var originalToolUseIDs = struct {
	sync.Mutex
	byShortened map[string]string
	order       []string
}{byShortened: make(map[string]string, toolUseIDMemory)}

func rememberToolUseID(shortened, raw string) {
	if shortened == raw {
		return
	}
	originalToolUseIDs.Lock()
	defer originalToolUseIDs.Unlock()
	if _, seen := originalToolUseIDs.byShortened[shortened]; seen {
		return
	}
	if len(originalToolUseIDs.order) >= toolUseIDMemory {
		oldest := originalToolUseIDs.order[0]
		originalToolUseIDs.order = originalToolUseIDs.order[1:]
		delete(originalToolUseIDs.byShortened, oldest)
	}
	originalToolUseIDs.byShortened[shortened] = raw
	originalToolUseIDs.order = append(originalToolUseIDs.order, shortened)
}

// originalToolUseID returns what the provider originally called this tool call, or the id itself
// when nothing was shortened. Safe on every id: one that never passed through toolUseID is
// unknown here and is handed back unchanged.
func originalToolUseID(shortened string) string {
	originalToolUseIDs.Lock()
	defer originalToolUseIDs.Unlock()
	if raw, seen := originalToolUseIDs.byShortened[shortened]; seen {
		return raw
	}
	return shortened
}
