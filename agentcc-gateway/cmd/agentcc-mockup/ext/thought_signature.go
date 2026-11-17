package ext

import (
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mockup/mock"
)

// requireThoughtSignature handles require_thought_signature. For a
// google/gemini-* model, a request whose messages hold an assistant tool call
// without a thought signature is refused with 400 invalid_request, as Gemini
// refuses it ("Function call is missing a thought_signature"); it is not
// charged. Other models are unaffected. A tool call is signed when
//   - its assistant message's reasoning_details holds a reasoning.encrypted
//     entry with data and the call's id (OpenRouter's format), or
//   - the call carries extra_content.google.thought_signature (Gemini's
//     OpenAI-compatible format).
func requireThoughtSignature(arg string, req *mock.Request, resp *mock.Response) error {
	if arg != "" {
		return invalidDirective("require_thought_signature:"+arg, "takes no argument")
	}
	if !strings.HasPrefix(req.Model, "google/gemini-") {
		return nil
	}
	messages, _ := req.JSON["messages"].([]any)
	for i, m := range messages {
		msg, _ := m.(map[string]any)
		if msg["role"] != "assistant" {
			continue
		}
		signed := encryptedReasoningIDs(msg)
		calls, _ := msg["tool_calls"].([]any)
		for _, c := range calls {
			call, _ := c.(map[string]any)
			id, _ := call["id"].(string)
			if (id != "" && signed[id]) || googleThoughtSignature(call) != "" {
				continue
			}
			resp.Status = http.StatusBadRequest
			resp.ErrorCode = "invalid_request"
			resp.ErrorMessage = fmt.Sprintf("Function call is missing a thought_signature in functionCall parts "+
				"(messages[%d] tool call %q; mock require_thought_signature)", i, id)
			return nil
		}
	}
	return nil
}

// encryptedReasoningIDs are the ids of an assistant message's
// reasoning_details entries of type reasoning.encrypted that carry data.
func encryptedReasoningIDs(msg map[string]any) map[string]bool {
	details, _ := msg["reasoning_details"].([]any)
	ids := map[string]bool{}
	for _, d := range details {
		detail, _ := d.(map[string]any)
		id, _ := detail["id"].(string)
		data, _ := detail["data"].(string)
		if detail["type"] == "reasoning.encrypted" && id != "" && data != "" {
			ids[id] = true
		}
	}
	return ids
}

func googleThoughtSignature(call map[string]any) string {
	extra, _ := call["extra_content"].(map[string]any)
	google, _ := extra["google"].(map[string]any)
	sig, _ := google["thought_signature"].(string)
	return sig
}
