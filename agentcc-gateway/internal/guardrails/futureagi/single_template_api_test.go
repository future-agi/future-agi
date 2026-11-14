package futureagi

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sort"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// singleTemplateAPI behaves like the backend's /sdk/api/v1/eval/ view: it
// keeps only the last entry of the request's config map, runs that one
// template and answers with a single evaluation that carries the request's
// SDK id rather than the template id.
type singleTemplateAPI struct {
	mu        sync.Mutex
	verdicts  map[string]string // template id -> "Passed", "Failed" or "" for HTTP 500
	requested []string          // template ids, one per request
	configLen []int
}

func (a *singleTemplateAPI) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	var payload struct {
		Config json.RawMessage `json:"config"`
	}
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	keys := orderedKeys(payload.Config)
	if len(keys) == 0 {
		http.Error(w, "config is required.", http.StatusBadRequest)
		return
	}
	last := keys[len(keys)-1]
	a.mu.Lock()
	a.requested = append(a.requested, last)
	a.configLen = append(a.configLen, len(keys))
	verdict := a.verdicts[last]
	a.mu.Unlock()
	if verdict == "" {
		w.WriteHeader(http.StatusInternalServerError)
		return
	}
	json.NewEncoder(w).Encode(map[string]interface{}{
		"result": []map[string]interface{}{
			{"evaluations": []map[string]interface{}{{
				"name":        "template-" + last,
				"output":      verdict,
				"reason":      "template " + last + " " + verdict,
				"runtime":     1.0,
				"output_type": "Pass/Fail",
				"eval_id":     "synthetic-sdk-uuid",
			}}},
		},
	})
}

func orderedKeys(raw json.RawMessage) []string {
	dec := json.NewDecoder(bytes.NewReader(raw))
	if tok, err := dec.Token(); err != nil || tok != json.Delim('{') {
		return nil
	}
	var keys []string
	for dec.More() {
		tok, err := dec.Token()
		if err != nil {
			return nil
		}
		keys = append(keys, tok.(string))
		var skip json.RawMessage
		if err := dec.Decode(&skip); err != nil {
			return nil
		}
	}
	return keys
}

func newSingleTemplateCheck(t *testing.T, verdicts map[string]string, ids ...string) (*FutureAGIGuardrail, *singleTemplateAPI) {
	t.Helper()
	api := &singleTemplateAPI{verdicts: verdicts}
	srv := httptest.NewServer(api)
	t.Cleanup(srv.Close)
	evalIDs := make([]interface{}, len(ids))
	for i, id := range ids {
		evalIDs[i] = id
	}
	return New("protect", map[string]interface{}{
		"provider":   "futureagi",
		"eval_ids":   evalIDs,
		"api_key":    "synthetic",
		"secret_key": "synthetic",
		"base_url":   srv.URL,
	}), api
}

func TestFutureAGI_SingleTemplateAPI_AllTemplatesPass(t *testing.T) {
	g, api := newSingleTemplateCheck(t, map[string]string{"15": "Passed", "76": "Passed"}, "15", "76")

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
	if result.Err != nil || !result.Pass {
		t.Fatalf("expected pass for a safe request, got pass=%v err=%v message=%q", result.Pass, result.Err, result.Message)
	}
	got := append([]string(nil), api.requested...)
	sort.Strings(got)
	if len(got) != 2 || got[0] != "15" || got[1] != "76" {
		t.Errorf("expected one request per template, got %v", api.requested)
	}
	for _, n := range api.configLen {
		if n != 1 {
			t.Errorf("expected one template per request, got %d config entries", n)
		}
	}
}

func TestFutureAGI_SingleTemplateAPI_OneTemplateFails(t *testing.T) {
	for _, ids := range [][]string{{"15", "76"}, {"76", "15"}} {
		g, _ := newSingleTemplateCheck(t, map[string]string{"15": "Passed", "76": "Failed"}, ids...)

		result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
		if result.Err != nil {
			t.Fatalf("order %v: a Failed verdict must be a content decision, got execution failure %v", ids, result.Err)
		}
		if result.Pass || result.Score != 1.0 {
			t.Fatalf("order %v: expected the failing template to fail the check, got pass=%v score=%v", ids, result.Pass, result.Score)
		}
		if result.Details["eval_id"] != "76" || result.Message != "template 76 Failed" {
			t.Errorf("order %v: expected template 76 in the result, got eval_id=%v message=%q", ids, result.Details["eval_id"], result.Message)
		}
	}
}

func TestFutureAGI_SingleTemplateAPI_FailedVerdictWinsOverUnavailableTemplate(t *testing.T) {
	g, _ := newSingleTemplateCheck(t, map[string]string{"15": "", "76": "Failed"}, "15", "76")

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
	if result.Err != nil || result.Pass {
		t.Fatalf("expected the Failed verdict to decide, got pass=%v err=%v", result.Pass, result.Err)
	}
}

func TestFutureAGI_SingleTemplateAPI_UnavailableTemplateIsExecutionFailure(t *testing.T) {
	g, _ := newSingleTemplateCheck(t, map[string]string{"15": "", "76": "Passed"}, "15", "76")

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hello")}))
	if result.Err == nil {
		t.Fatalf("expected an execution failure so fail_open decides, got pass=%v message=%q", result.Pass, result.Message)
	}
}
