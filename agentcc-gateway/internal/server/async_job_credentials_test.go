package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
)

// An async job keeps no caller credential in its metadata: the job's copy of
// the request's metadata leaves out the key and any entry whose name reads as
// a credential, and keeps what the worker reads.
func TestAsyncJob_KeepsNoCredentialInItsMetadata(t *testing.T) {
	g := newPostBlockGateway(t, postBlockSetup{})
	body := fmt.Sprintf(`{"model":"gpt-4o","messages":[{"role":"user","content":%q}]}`, postBlockPrompt)
	code, resp := g.doAs(t, postBlockOpsKey, "/v1/chat/completions", body, map[string]string{
		"x-agentcc-async":    "true",
		"x-agentcc-metadata": `{"session_token":"planted-async-session-token-5c6d","team":"research"}`,
	})
	if code != http.StatusAccepted {
		t.Fatalf("async request: status %d, want 202; body %s", code, resp)
	}
	var accepted struct {
		JobID string `json:"job_id"`
	}
	if err := json.Unmarshal([]byte(resp), &accepted); err != nil || accepted.JobID == "" {
		t.Fatalf("no job id: %v; body %s", err, resp)
	}
	if done := waitAsyncJob(t, g.srv, postBlockOpsKey, accepted.JobID); done["status"] != "completed" {
		t.Fatalf("job status %v, want completed; job %v", done["status"], done)
	}

	job := g.srv.handlers.asyncStore.Get(accepted.JobID)
	if job == nil {
		t.Fatal("the job is not in the store")
	}
	meta, _ := json.Marshal(job.Metadata)
	for _, held := range []string{postBlockOpsKey, "planted-async-session-token-5c6d"} {
		if strings.Contains(string(meta), held) {
			t.Errorf("the job's metadata holds %q: %s", held, meta)
		}
	}
	if _, ok := job.Metadata["authorization"]; ok {
		t.Errorf("the job's metadata holds authorization: %s", meta)
	}
	for _, kept := range []string{"timeout_ms", "auth_key_id", "team"} {
		if job.Metadata[kept] == "" {
			t.Errorf("the job's metadata lost %s, which is not a credential: %s", kept, meta)
		}
	}
}
