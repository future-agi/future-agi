package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
)

// Once a job has run, completed or failed, the stored job marshalled whole to
// JSON holds no caller credential. The executor the handler builds for a
// queued job runs the job through the pipeline (auth included) and is dropped
// when the job starts (async.Store.start, pinned by
// TestWorker_FinishedJobDropsItsExecutor).
func TestAsyncJob_WholeStoredJobHoldsNoCallerCredential(t *testing.T) {
	for _, tc := range []struct {
		name, model, want string
	}{
		{"completed", "gpt-4o", "completed"},
		{"failed", postBlockBroken, "failed"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newPostBlockGateway(t, postBlockSetup{})
			body := fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":%q}]}`, tc.model, postBlockPrompt)
			code, resp := g.doAs(t, postBlockOpsKey, "/v1/chat/completions", body, map[string]string{
				"x-agentcc-async":    "true",
				"x-agentcc-metadata": `{"session_token":"planted-async-session-token-7e8f"}`,
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
			if done := waitAsyncJob(t, g.srv, postBlockOpsKey, accepted.JobID); done["status"] != tc.want {
				t.Fatalf("job status %v, want %s; job %v", done["status"], tc.want, done)
			}

			job := g.srv.handlers.asyncStore.Get(accepted.JobID)
			if job == nil {
				t.Fatal("the job is not in the store")
			}
			whole, err := json.Marshal(job)
			if err != nil {
				t.Fatal(err)
			}
			for _, held := range []string{postBlockOpsKey, "planted-async-session-token-7e8f"} {
				if strings.Contains(string(whole), held) {
					t.Errorf("the stored job holds %q: %s", held, whole)
				}
			}
		})
	}
}
