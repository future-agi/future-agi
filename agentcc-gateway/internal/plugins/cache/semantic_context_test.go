package cache

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// Issue #3768: semantic cache entries must not be served across different
// instructions (system / developer messages) or different conversation
// history, while paraphrases in the same context still hit.

const longAgentPrompt = `You are Atlas, the internal support assistant for a logistics company. ` +
	`Always answer concisely, cite the relevant policy section, never reveal internal ticket ids, ` +
	`escalate anything involving refunds above five hundred dollars to a human agent, respond in ` +
	`the language the user writes in, and format lists as short bullet points. If you are unsure, ` +
	`say so and suggest contacting the operations desk during business hours.`

func textMsg(role, text string) models.Message {
	b, _ := json.Marshal(text)
	return models.Message{Role: role, Content: b}
}

func imageMsg(text, url string) models.Message {
	b, _ := json.Marshal([]map[string]any{{"type": "text", "text": text}, {"type": "image_url", "image_url": map[string]string{"url": url}}})
	return models.Message{Role: "user", Content: b}
}

func partsMsg(role, text string) models.Message {
	b, _ := json.Marshal([]map[string]string{{"type": "text", "text": text}})
	return models.Message{Role: role, Content: b}
}

// storeThenLookup stores a response for first, then reports whether second is
// served from the semantic cache (exact-match L1 cannot hit: messages differ).
func storeThenLookup(t *testing.T, first, second []models.Message) (bool, string) {
	t.Helper()
	return storeThenLookupNS(t, "", first, "", second)
}

func storeThenLookupNS(t *testing.T, ns1 string, first []models.Message, ns2 string, second []models.Message) (bool, string) {
	t.Helper()
	p, _, _ := newTestSemanticPlugin()
	rc1 := models.AcquireRequestContext()
	rc1.Request = &models.ChatCompletionRequest{Model: "gpt-4o", Messages: first}
	rc1.Model = "gpt-4o"
	if ns1 != "" {
		rc1.Metadata["cache_namespace"] = ns1
	}
	p.ProcessRequest(context.Background(), rc1)
	rc1.Response = makeResp("first-answer")
	p.ProcessResponse(context.Background(), rc1)
	rc1.Release()

	rc2 := models.AcquireRequestContext()
	defer rc2.Release()
	rc2.Request = &models.ChatCompletionRequest{Model: "gpt-4o", Messages: second}
	rc2.Model = "gpt-4o"
	if ns2 != "" {
		rc2.Metadata["cache_namespace"] = ns2
	}
	res := p.ProcessRequest(context.Background(), rc2)
	return res.Action == pipeline.ShortCircuit, rc2.Metadata["cache_status"] + " sim=" + rc2.Metadata["cache_similarity"]
}

func TestSemanticCacheIsolatesInstructionsAndHistory(t *testing.T) {
	q := "How do I reverse a string in my favourite programming language?"
	cases := []struct {
		name          string
		first, second []models.Message
	}{
		{"system prompt",
			[]models.Message{textMsg("system", "You are a Python assistant."), textMsg("user", q)},
			[]models.Message{textMsg("system", "You are a French translator."), textMsg("user", q)}},
		{"short differing system prompts",
			[]models.Message{textMsg("system", "Be terse."), textMsg("user", q)},
			[]models.Message{textMsg("system", "Be rude."), textMsg("user", q)}},
		{"system prompt as content parts",
			[]models.Message{partsMsg("system", "You are a Python assistant."), textMsg("user", q)},
			[]models.Message{partsMsg("system", "You are a French translator."), textMsg("user", q)}},
		{"developer message",
			[]models.Message{textMsg("developer", "Answer in Rust."), textMsg("user", q)},
			[]models.Message{textMsg("developer", "Answer in Haskell."), textMsg("user", q)}},
		{"system prompt only on one side",
			[]models.Message{textMsg("user", q)},
			[]models.Message{textMsg("system", "You are a French translator."), textMsg("user", q)}},
		{"different image, same question",
			[]models.Message{imageMsg("What is shown in this picture?", "https://img.example.test/cat.png")},
			[]models.Message{imageMsg("What is shown in this picture?", "https://img.example.test/invoice.png")}},
		{"multi-turn history",
			[]models.Message{textMsg("user", "Tell me about the Eiffel Tower."), textMsg("assistant", "It is in Paris."), textMsg("user", "Can you give me more detail about that?")},
			[]models.Message{textMsg("user", "Tell me about the Great Wall."), textMsg("assistant", "It is in China."), textMsg("user", "Can you give me more detail about that?")}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if hit, st := storeThenLookup(t, c.first, c.second); hit {
				t.Errorf("served a response cached under a different context (%s)", st)
			}
		})
	}
}

// Same instructions, different question: must miss. A shared long system
// prompt must not make unrelated questions look alike.
func TestSemanticCacheSharedInstructionsDifferentQuestionMisses(t *testing.T) {
	first := []models.Message{textMsg("system", longAgentPrompt), textMsg("user", "What is the capital of France?")}
	second := []models.Message{textMsg("system", longAgentPrompt), textMsg("user", "How long does standard shipping take?")}
	if hit, st := storeThenLookup(t, first, second); hit {
		t.Errorf("unrelated question served the cached answer under a shared system prompt (%s)", st)
	}
}

// Positive controls: paraphrases in the same context still hit.
func TestSemanticCacheSameContextParaphraseHits(t *testing.T) {
	cases := []struct {
		name          string
		first, second []models.Message
	}{
		{"no instructions",
			[]models.Message{textMsg("user", "what is the capital of France")},
			[]models.Message{textMsg("user", "What is the capital of France?")}},
		{"same long system prompt",
			[]models.Message{textMsg("system", longAgentPrompt), textMsg("user", "what is the capital of France")},
			[]models.Message{textMsg("system", longAgentPrompt), textMsg("user", "What is the capital of France?")}},
		{"same image",
			[]models.Message{imageMsg("what is shown in this picture", "https://img.example.test/cat.png")},
			[]models.Message{imageMsg("What is shown in this picture?", "https://img.example.test/cat.png")}},
		{"same history",
			[]models.Message{textMsg("user", "Tell me about the Eiffel Tower."), textMsg("assistant", "It is in Paris."), textMsg("user", "can you give me more detail about that")},
			[]models.Message{textMsg("user", "Tell me about the Eiffel Tower."), textMsg("assistant", "It is in Paris."), textMsg("user", "Can you give me more detail about that?")}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if hit, st := storeThenLookup(t, c.first, c.second); !hit {
				t.Errorf("paraphrase in the same context missed (%s)", st)
			}
		})
	}
}

// x-agentcc-cache-namespace isolates the exact-match cache; the semantic layer
// must honour it too. Same namespace still hits.
func TestSemanticCacheHonoursNamespace(t *testing.T) {
	first := []models.Message{textMsg("user", "what is the capital of France")}
	second := []models.Message{textMsg("user", "What is the capital of France?")}
	if hit, st := storeThenLookupNS(t, "team-a", first, "team-b", second); hit {
		t.Errorf("served across cache namespaces (%s)", st)
	}
	if hit, st := storeThenLookupNS(t, "team-a", first, "team-a", second); !hit {
		t.Errorf("same namespace paraphrase missed (%s)", st)
	}
}
