package gemini

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func geminiUsageResponse(t *testing.T, usage string) *models.Usage {
	t.Helper()
	var resp geminiResponse
	body := `{"candidates":[{"content":{"role":"model","parts":[{"text":"ok"}]},"finishReason":"STOP"}],"usageMetadata":` + usage + `}`
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		t.Fatal(err)
	}
	u := translateResponse(&resp, "gemini-3-pro").Usage
	if u == nil {
		t.Fatal("no usage")
	}
	return u
}

func TestGeminiCacheTokensAndThoughtTokens(t *testing.T) {
	u := geminiUsageResponse(t, `{"promptTokenCount":100,"cachedContentTokenCount":60,"candidatesTokenCount":20,"thoughtsTokenCount":30,"totalTokenCount":150}`)
	if u.PromptTokens != 100 || u.CompletionTokens != 50 || u.TotalTokens != 150 {
		t.Errorf("tokens = prompt %d completion %d total %d, want 100/50/150", u.PromptTokens, u.CompletionTokens, u.TotalTokens)
	}
	if u.PromptDetails.CachedTokens != 60 || u.CompletionDetails.ReasoningTokens != 30 {
		t.Errorf("sidecars = %+v / %+v, want cached 60 and reasoning 30", u.PromptDetails, u.CompletionDetails)
	}
	if u.CompletionTokensDetails == nil || string(*u.CompletionTokensDetails) != `{"reasoning_tokens":30}` {
		t.Errorf("completion_tokens_details = %v, want reasoning_tokens 30", u.CompletionTokensDetails)
	}
	if u.PromptTokensDetails == nil || string(*u.PromptTokensDetails) != `{"cached_tokens":60}` {
		t.Errorf("prompt_tokens_details = %v, want cached_tokens 60", u.PromptTokensDetails)
	}
}

// A zero cached count is reported, and a response without thoughts gets
// no completion details.
func TestGeminiUnknownDetailsPreserved(t *testing.T) {
	u := geminiUsageResponse(t, `{"promptTokenCount":10,"cachedContentTokenCount":0,"candidatesTokenCount":5,"totalTokenCount":15}`)
	if u.PromptTokensDetails == nil || string(*u.PromptTokensDetails) != `{"cached_tokens":0}` {
		t.Errorf("prompt_tokens_details = %v, want {\"cached_tokens\":0}", u.PromptTokensDetails)
	}
	if u.CompletionTokensDetails != nil {
		t.Errorf("completion_tokens_details = %s for a response without thoughts", *u.CompletionTokensDetails)
	}

	// Details written through the sidecars keep every subfield they do not
	// know, so an upstream's extra fields pass through.
	raw := json.RawMessage(`{"cached_tokens":1,"audio_tokens":0,"modality_tokens":{"text":9}}`)
	usage := &models.Usage{PromptTokensDetails: &raw}
	cached := 60
	setTokenDetails(usage, &cached, 30)
	var prompt map[string]json.RawMessage
	if err := json.Unmarshal(*usage.PromptTokensDetails, &prompt); err != nil {
		t.Fatal(err)
	}
	if string(prompt["cached_tokens"]) != "60" || string(prompt["modality_tokens"]) != `{"text":9}` || string(prompt["audio_tokens"]) != "0" {
		t.Errorf("prompt_tokens_details = %s, want cached_tokens 60 with the other subfields kept", *usage.PromptTokensDetails)
	}
}
