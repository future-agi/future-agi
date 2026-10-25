package gemini

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// embedUpstream is a Gemini embedContent / batchEmbedContents upstream that
// answers with usage (a usageMetadata JSON object, or none when empty) and
// keeps the body of every request it was sent.
type embedUpstream struct {
	*httptest.Server
	mu     sync.Mutex
	bodies []string
}

func startEmbedUpstream(t *testing.T, usage string) *embedUpstream {
	t.Helper()
	u := &embedUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		u.mu.Lock()
		u.bodies = append(u.bodies, string(body))
		u.mu.Unlock()
		meta := ""
		if usage != "" {
			meta = `,"usageMetadata":` + usage
		}
		w.Header().Set("Content-Type", "application/json")
		if strings.HasSuffix(r.URL.Path, ":batchEmbedContents") {
			io.WriteString(w, `{"embeddings":[{"values":[0.1,0.2]},{"values":[0.3,0.4]}]`+meta+`}`)
			return
		}
		io.WriteString(w, `{"embedding":{"values":[0.1,0.2]}`+meta+`}`)
	}))
	t.Cleanup(u.Close)
	return u
}

func (u *embedUpstream) last() string {
	u.mu.Lock()
	defer u.mu.Unlock()
	if len(u.bodies) == 0 {
		return ""
	}
	return u.bodies[len(u.bodies)-1]
}

var (
	singleEmbed = &models.EmbeddingRequest{Model: "gemini-embedding-001", Input: json.RawMessage(`"the quarterly report is due on Friday"`)}
	batchEmbed  = &models.EmbeddingRequest{Model: "gemini-embedding-001", Input: json.RawMessage(`["first passage to index","second passage to index"]`)}
)

// The Gemini upstream that reports no usage leaves the response's usage
// unset rather than a fake zero, and the adapter's estimate for the same
// input is a non-zero token count, so a paid call is never costed at $0.
// Usage the upstream does report (usageMetadata.promptTokenCount) is used
// as reported.
func TestGeminiUsageEstimatedNotZero(t *testing.T) {
	silent := startEmbedUpstream(t, "")
	p := newTestProvider(t, silent.URL)
	for name, req := range map[string]*models.EmbeddingRequest{"single": singleEmbed, "batch": batchEmbed} {
		resp, err := p.CreateEmbedding(context.Background(), req)
		if err != nil {
			t.Fatalf("%s: CreateEmbedding: %v", name, err)
		}
		if resp.Usage != nil {
			t.Errorf("%s: the upstream reported no usage, but the response carries %+v", name, *resp.Usage)
		}
		est := p.EstimateEmbeddingUsage(req)
		if est == nil || est.PromptTokens <= 0 || est.TotalTokens != est.PromptTokens {
			t.Errorf("%s: estimate = %+v, want a non-zero prompt count equal to the total", name, est)
		}
	}
	if one, two := p.EstimateEmbeddingUsage(singleEmbed), p.EstimateEmbeddingUsage(batchEmbed); one == nil || two == nil || two.PromptTokens < 2 {
		t.Errorf("estimates single %+v, batch %+v: every input counts at least one token", one, two)
	}
	if est := p.EstimateEmbeddingUsage(&models.EmbeddingRequest{Model: "gemini-embedding-001", Input: json.RawMessage(`[1,2]`)}); est != nil {
		t.Errorf("an input the adapter cannot embed got an estimate: %+v", est)
	}

	reported := startEmbedUpstream(t, `{"promptTokenCount":7}`)
	for name, req := range map[string]*models.EmbeddingRequest{"single": singleEmbed, "batch": batchEmbed} {
		resp, err := newTestProvider(t, reported.URL).CreateEmbedding(context.Background(), req)
		if err != nil {
			t.Fatalf("%s: CreateEmbedding: %v", name, err)
		}
		if resp.Usage == nil || resp.Usage.PromptTokens != 7 || resp.Usage.TotalTokens != 7 {
			t.Errorf("%s: reported usage = %+v, want 7 prompt tokens", name, resp.Usage)
		}
	}
}

// A request's input_type goes out as Gemini's taskType on both endpoints;
// without one no taskType is sent.
func TestGeminiTaskTypeFromInputType(t *testing.T) {
	up := startEmbedUpstream(t, "")
	p := newTestProvider(t, up.URL)
	if !p.AcceptsEmbeddingInputType() {
		t.Fatal("the Gemini adapter takes a per-request input_type but does not say so")
	}
	cases := map[string]string{
		models.EmbeddingInputTypeQuery:          "RETRIEVAL_QUERY",
		models.EmbeddingInputTypeDocument:       "RETRIEVAL_DOCUMENT",
		models.EmbeddingInputTypeClassification: "CLASSIFICATION",
		models.EmbeddingInputTypeClustering:     "CLUSTERING",
	}
	for in, want := range cases {
		for name, base := range map[string]*models.EmbeddingRequest{"single": singleEmbed, "batch": batchEmbed} {
			req := *base
			req.InputType = in
			if _, err := p.CreateEmbedding(context.Background(), &req); err != nil {
				t.Fatalf("%s %s: %v", name, in, err)
			}
			if got := sentTaskTypes(t, up.last()); len(got) == 0 || !allEqual(got, want) {
				t.Errorf("%s: input_type %q sent taskType %v, want %s", name, in, got, want)
			}
		}
	}
	for name, req := range map[string]*models.EmbeddingRequest{"single": singleEmbed, "batch": batchEmbed} {
		if _, err := p.CreateEmbedding(context.Background(), req); err != nil {
			t.Fatal(err)
		}
		if strings.Contains(up.last(), "taskType") {
			t.Errorf("%s: no input_type, but the request named a taskType: %s", name, up.last())
		}
	}
}

// sentTaskTypes is every taskType in an embedContent or batchEmbedContents
// body.
func sentTaskTypes(t *testing.T, body string) []string {
	t.Helper()
	var single geminiEmbedRequest
	var batch geminiBatchEmbedRequest
	if err := json.Unmarshal([]byte(body), &batch); err == nil && len(batch.Requests) > 0 {
		out := make([]string, 0, len(batch.Requests))
		for _, r := range batch.Requests {
			out = append(out, r.TaskType)
		}
		return out
	}
	if err := json.Unmarshal([]byte(body), &single); err != nil {
		t.Fatalf("unreadable request body %s: %v", body, err)
	}
	return []string{single.TaskType}
}

func allEqual(got []string, want string) bool {
	for _, g := range got {
		if g != want {
			return false
		}
	}
	return true
}
