package server

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
)

// An async job keeps no caller credential in its metadata: the job's copy
// leaves out the key and any entry whose name reads as a credential, and
// keeps what the worker reads.
func TestAsyncJob_KeepsNoCredentialInItsMetadata(t *testing.T) {
	for _, key := range []string{asyncExecKeyOrg, asyncExecKeyOps} {
		t.Run(key, func(t *testing.T) {
			up := newCredentialRecorder(t)
			srv := newAsyncExecServer(t, up.URL)
			w := objCall(t, srv, http.MethodPost, "/v1/chat/completions", key, chatBody("gpt-4o"), map[string]string{
				"x-agentcc-async":    "true",
				"x-agentcc-metadata": `{"session_token":"planted-async-session-token-5c6d","openai_key":"planted-async-openai-key-7e8f","team":"research","token_budget":"5000"}`,
			})
			if w.Code != http.StatusAccepted {
				t.Fatalf("async request: status %d, want 202; body %s", w.Code, w.Body.String())
			}
			var accepted struct {
				JobID string `json:"job_id"`
			}
			if err := json.Unmarshal(w.Body.Bytes(), &accepted); err != nil || accepted.JobID == "" {
				t.Fatalf("no job id: %v; body %s", err, w.Body.String())
			}
			if done := waitAsyncJob(t, srv, key, accepted.JobID); done["status"] != "completed" {
				t.Fatalf("job status %v, want completed; job %v", done["status"], done)
			}
			// The job ran under the caller's key: the executor's own copy
			// of the request kept it until the job's pipeline read it.
			if seen := up.take(); len(seen) != 1 {
				t.Fatalf("the upstream got %d calls, want 1", len(seen))
			}

			job := srv.handlers.asyncStore.Get(accepted.JobID)
			if job == nil {
				t.Fatal("the job is not in the store")
			}
			meta, _ := json.Marshal(job.Metadata)
			for _, held := range []string{key, "planted-async-session-token-5c6d", "planted-async-openai-key-7e8f"} {
				if strings.Contains(string(meta), held) {
					t.Errorf("the job's metadata holds %q: %s", held, meta)
				}
			}
			if _, ok := job.Metadata["authorization"]; ok {
				t.Errorf("the job's metadata holds authorization: %s", meta)
			}
			for _, kept := range []string{"timeout_ms", "auth_key_id", "team", "token_budget"} {
				if job.Metadata[kept] == "" {
					t.Errorf("the job's metadata lost %s, which is not a credential: %s", kept, meta)
				}
			}
		})
	}
}
