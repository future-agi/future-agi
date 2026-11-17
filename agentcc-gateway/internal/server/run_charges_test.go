package server

import (
	"context"
	"fmt"
	"io"
	"os"
	"strings"
	"testing"
	"testing/iotest"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// The pending charge may sit in shared Redis; the caller's credential must
// not. The starting call's own holds stay behind too: a charge made on
// another call must not settle them again.
func TestPendingChargeLeavesTheCredentialBehind(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["authorization"] = "Bearer sk-live-secret"
	rc.Metadata["auth_key_id"] = "key_1"
	rc.Metadata["upstream_api_key"] = "sk-other"
	rc.Metadata["application"] = "billing"
	rc.Metadata["ratelimit_tpm_hold"] = "15"
	rc.Metadata["cache_key"] = "k"

	charge := newPendingCharge(rc, "gpt-4o")

	for _, dropped := range []string{"authorization", "upstream_api_key", "ratelimit_tpm_hold", "cache_key"} {
		if _, kept := charge.Metadata[dropped]; kept {
			t.Errorf("pending charge kept %q", dropped)
		}
	}
	if charge.Metadata["auth_key_id"] != "key_1" || charge.Metadata["application"] != "billing" {
		t.Errorf("pending charge lost its attribution: %v", charge.Metadata)
	}
}

func TestRunChargeStoresHandBackOnce(t *testing.T) {
	stores := map[string]runChargeStore{"memory": newMemoryRunCharges()}
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		addr = "localhost:6379"
	}
	if client, err := redisstate.NewClient(redisstate.Config{Address: addr, Timeout: time.Second}); err == nil {
		t.Cleanup(func() { client.Close() })
		stores["redis"] = &redisRunCharges{client: client}
	} else {
		t.Logf("redis store not tested: Redis not available at %s: %v", addr, err)
	}

	for name, store := range stores {
		t.Run(name, func(t *testing.T) {
			ctx := context.Background()
			runID := fmt.Sprintf("run_test_%d", time.Now().UnixNano())
			store.Put(ctx, runID, pendingCharge{Model: "gpt-4o", Metadata: map[string]string{"auth_key_id": "key_1"}})

			charge, ok := store.Take(ctx, runID)
			if !ok || charge.Model != "gpt-4o" || charge.Metadata["auth_key_id"] != "key_1" {
				t.Fatalf("Take = %+v, %v; want the charge put", charge, ok)
			}
			if _, again := store.Take(ctx, runID); again {
				t.Error("the same run was handed back twice")
			}
		})
	}
}

// The relay reads the stream in whatever pieces the upstream sends. A run
// event split across reads, with either line ending, is still read, and the
// stream reaches the client byte for byte.
func TestRunEventsReadARunSplitAcrossReads(t *testing.T) {
	for name, eol := range map[string]string{"lf": "\n", "crlf": "\r\n"} {
		t.Run(name, func(t *testing.T) {
			stream := "event: thread.run.created" + eol + "data: " + runQueuedJSON + eol + eol +
				": keep-alive" + eol + eol +
				"event: thread.run.completed" + eol + "data: " + runDoneJSON + eol + eol +
				"event: done" + eol + "data: [DONE]" + eol + eol
			h := &Handlers{runCharges: newMemoryRunCharges()}
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Metadata["auth_key_id"] = "key_1"
			runs := h.watchRuns(rc, nil, true)
			events := &runEvents{ReadCloser: io.NopCloser(iotest.OneByteReader(strings.NewReader(stream))), ctx: context.Background(), watch: runs}

			relayed, err := io.ReadAll(events)
			if err != nil || string(relayed) != stream {
				t.Fatalf("relayed %q, %v; want the stream unchanged", relayed, err)
			}
			if rc.Response == nil || rc.Response.Usage == nil || rc.Response.Usage.TotalTokens != 15 || rc.Metadata["run_id"] != "run_1" {
				t.Fatalf("the run that finished on its own stream is not counted on it: response %+v, run_id %q", rc.Response, rc.Metadata["run_id"])
			}
			if _, again := h.runCharges.Take(context.Background(), "run_1"); again {
				t.Error("the run's pending charge is still there after it was counted")
			}
		})
	}
}

// A call that sees another call's run finish carries the run's charge with
// the starter's attribution in place of its own, keeps its own holds for its
// response plugins to settle, and is answered as its own afterwards.
func TestRunChargeMovesOntoThePollAndThePollAnswersAsItself(t *testing.T) {
	h := &Handlers{runCharges: newMemoryRunCharges()}
	ctx := context.Background()
	starter := models.AcquireRequestContext()
	defer starter.Release()
	starter.Metadata["auth_key_id"] = "key_creator"
	starter.Metadata["org_id"] = "org_creator"
	starter.Metadata["assistants_operation"] = "threads.runs.create"
	starter.Provider = "openai"
	h.watchRuns(starter, nil, true).observe(ctx, []byte(runQueuedJSON))

	poll := models.AcquireRequestContext()
	defer poll.Release()
	poll.Metadata["auth_key_id"] = "key_poller"
	poll.Metadata["org_id"] = "org_poller"
	poll.Metadata["assistants_operation"] = "threads.runs.get"
	poll.Metadata["team"] = "poller-team"
	poll.Metadata["ratelimit_tpm_hold"] = "7"
	poll.UserID = "poller-user"
	runs := h.watchRuns(poll, nil, false)
	runs.observe(ctx, []byte(runDoneJSON))

	if poll.Response == nil || poll.Response.Usage == nil || poll.Response.Usage.TotalTokens != 15 {
		t.Fatalf("the poll does not carry the run's charge: %+v", poll.Response)
	}
	want := map[string]string{"auth_key_id": "key_creator", "org_id": "org_creator", "assistants_operation": "threads.runs.create", "run_id": "run_1", "ratelimit_tpm_hold": "7"}
	for key, value := range want {
		if poll.Metadata[key] != value {
			t.Errorf("while charging, %s = %q, want %q", key, poll.Metadata[key], value)
		}
	}
	if _, kept := poll.Metadata["team"]; kept {
		t.Error("the poller's team is merged into the starter's charge")
	}
	if poll.UserID != "" || poll.Provider != "openai" || poll.ResolvedModel != "gpt-4o" {
		t.Errorf("charge on user %q, provider %q, model %q; want the starter's", poll.UserID, poll.Provider, poll.ResolvedModel)
	}

	runs.answerAsCaller()
	if poll.Metadata["auth_key_id"] != "key_poller" || poll.Metadata["org_id"] != "org_poller" || poll.Metadata["team"] != "poller-team" || poll.UserID != "poller-user" {
		t.Errorf("the poll is not answered as itself: %v, user %q", poll.Metadata, poll.UserID)
	}
	if poll.Response != nil {
		t.Errorf("the poll's answer still carries the run's charge: %+v", poll.Response)
	}
	if _, again := h.runCharges.Take(ctx, "run_1"); again {
		t.Error("the run's pending charge is still there after it was charged")
	}
}
