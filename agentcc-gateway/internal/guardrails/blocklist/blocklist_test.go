package blocklist

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func makeInput(content string) *guardrails.CheckInput {
	raw, _ := json.Marshal(content)
	return &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model: "gpt-4o",
			Messages: []models.Message{
				{Role: "user", Content: raw},
			},
		},
		Metadata: map[string]string{},
	}
}

func TestExactMatch(t *testing.T) {
	g := New(map[string]interface{}{
		"words": []interface{}{"badword"},
	})
	r := g.Check(context.Background(), makeInput("This contains a badword in it"))
	if r.Pass {
		t.Fatal("expected match")
	}
}

func TestNoMatch(t *testing.T) {
	g := New(map[string]interface{}{
		"words": []interface{}{"badword"},
	})
	r := g.Check(context.Background(), makeInput("This is clean text"))
	if !r.Pass {
		t.Fatal("expected no match")
	}
}

func TestCaseInsensitive(t *testing.T) {
	g := New(map[string]interface{}{
		"words": []interface{}{"BADWORD"},
	})
	r := g.Check(context.Background(), makeInput("this has badword inside"))
	if r.Pass {
		t.Fatal("expected case-insensitive match")
	}
}

func TestPhraseMatch(t *testing.T) {
	g := New(map[string]interface{}{
		"words": []interface{}{"bad phrase"},
	})
	r := g.Check(context.Background(), makeInput("This has a bad phrase in it"))
	if r.Pass {
		t.Fatal("expected phrase match")
	}
}

func TestWildcard(t *testing.T) {
	g := New(map[string]interface{}{
		"words": []interface{}{"f*ck"},
	})
	r := g.Check(context.Background(), makeInput("what the f-ck"))
	if r.Pass {
		t.Fatal("expected wildcard match")
	}
}

func TestWildcard_NoMatch(t *testing.T) {
	g := New(map[string]interface{}{
		"words": []interface{}{"f*ck"},
	})
	r := g.Check(context.Background(), makeInput("this is a fork"))
	if !r.Pass {
		t.Fatal("fork should not match f*ck")
	}
}

func TestWholeWord_Match(t *testing.T) {
	g := New(map[string]interface{}{
		"words":      []interface{}{"bad"},
		"whole_word": true,
	})
	r := g.Check(context.Background(), makeInput("this is bad"))
	if r.Pass {
		t.Fatal("expected whole word match")
	}
}

func TestWholeWord_NoSubstring(t *testing.T) {
	g := New(map[string]interface{}{
		"words":      []interface{}{"bad"},
		"whole_word": true,
	})
	r := g.Check(context.Background(), makeInput("this is a badge"))
	if !r.Pass {
		t.Fatal("badge should not match bad with whole_word")
	}
}

func TestSubstringMatch_Default(t *testing.T) {
	g := New(map[string]interface{}{
		"words": []interface{}{"bad"},
	})
	r := g.Check(context.Background(), makeInput("this is a badge"))
	if r.Pass {
		t.Fatal("substring match should find bad in badge")
	}
}

func TestMultipleWords(t *testing.T) {
	g := New(map[string]interface{}{
		"words":       []interface{}{"word1", "word2", "word3"},
		"max_matches": 3,
	})
	r := g.Check(context.Background(), makeInput("word1 and word2 found"))
	if r.Pass {
		t.Fatal("expected match")
	}
	matched := r.Details["match_count"].(int)
	if matched != 2 {
		t.Errorf("expected 2 matches, got %d", matched)
	}
}

func TestScore_SingleMatch(t *testing.T) {
	g := New(map[string]interface{}{
		"words":       []interface{}{"bad"},
		"max_matches": 1,
	})
	r := g.Check(context.Background(), makeInput("this is bad"))
	if r.Score != 1.0 {
		t.Errorf("expected score 1.0, got %f", r.Score)
	}
}

func TestScore_Partial(t *testing.T) {
	g := New(map[string]interface{}{
		"words":       []interface{}{"word1", "word2", "word3"},
		"max_matches": 4,
	})
	r := g.Check(context.Background(), makeInput("word1 word2"))
	expected := 2.0 / 4.0
	if r.Score != expected {
		t.Errorf("expected score %f, got %f", expected, r.Score)
	}
}

func TestEmptyWordList(t *testing.T) {
	g := New(map[string]interface{}{
		"words": []interface{}{},
	})
	r := g.Check(context.Background(), makeInput("anything"))
	if !r.Pass {
		t.Fatal("empty word list should always pass")
	}
}

func TestNilConfig(t *testing.T) {
	g := New(nil)
	r := g.Check(context.Background(), makeInput("anything"))
	if !r.Pass {
		t.Fatal("nil config should pass")
	}
}

func TestNilInput(t *testing.T) {
	g := New(map[string]interface{}{
		"words": []interface{}{"bad"},
	})
	r := g.Check(context.Background(), nil)
	if !r.Pass {
		t.Fatal("nil input should pass")
	}
}

func TestName(t *testing.T) {
	g := New(nil)
	if g.Name() != "keyword-blocklist" {
		t.Errorf("expected keyword-blocklist, got %s", g.Name())
	}
}

func TestStage(t *testing.T) {
	g := New(nil)
	if g.Stage() != guardrails.StagePre {
		t.Error("expected StagePre")
	}
}

func TestResponseContent(t *testing.T) {
	raw, _ := json.Marshal("This response has a badword")
	g := New(map[string]interface{}{
		"words": []interface{}{"badword"},
	})
	input := &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{Model: "gpt-4o"},
		Response: &models.ChatCompletionResponse{
			Choices: []models.Choice{
				{Message: models.Message{Role: "assistant", Content: raw}},
			},
		},
		Metadata: map[string]string{},
	}
	r := g.Check(context.Background(), input)
	if r.Pass {
		t.Fatal("expected match in response")
	}
}

func TestMatchedWordsInDetails(t *testing.T) {
	g := New(map[string]interface{}{
		"words": []interface{}{"alpha", "beta"},
	})
	r := g.Check(context.Background(), makeInput("alpha beta gamma"))
	if r.Pass {
		t.Fatal("expected match")
	}
	words := r.Details["matched_words"].([]string)
	if len(words) != 2 {
		t.Errorf("expected 2 matched words, got %d", len(words))
	}
}
