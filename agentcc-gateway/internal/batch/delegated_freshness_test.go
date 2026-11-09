package batch

import (
	"context"
	"encoding/json"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestDelegated733HealthyBatchDuringRefresh(t *testing.T) {
	keys := auth.NewKeyStore(config.AuthConfig{})
	keys.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: []auth.DelegatedMember{{Org: batch733Org, User: batch733User, Workspace: "-"}}}, time.Now().Add(-61*time.Second))
	var calls atomic.Int32
	p := NewProcessor(func(context.Context, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
		calls.Add(1)
		return &models.ChatCompletionResponse{}, nil, nil
	}, WithKeyStore(keys, true))
	defer p.Stop()
	b := p.SubmitAs(r7Requests(3), 1, "", batch733Caller(t, p))
	waitBatch733(t, b)
	if calls.Load() != 3 || b.GetStatus() != "completed" {
		t.Fatalf("healthy refresh cancelled batch: calls=%d state=%s", calls.Load(), b.GetStatus())
	}
}

func TestDelegated733StalledSnapshotHasTypedReason(t *testing.T) {
	keys := batch733Keys()
	started, release := make(chan struct{}), make(chan struct{})
	var calls atomic.Int32
	p := NewProcessor(func(context.Context, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
		if calls.Add(1) == 1 {
			close(started)
			<-release
		}
		return &models.ChatCompletionResponse{}, nil, nil
	}, WithKeyStore(keys, true))
	defer p.Stop()
	caller := batch733Caller(t, p)
	b := p.SubmitAs(r7Requests(3), 1, "", caller)
	select {
	case <-started:
	case <-time.After(5 * time.Second):
		close(release)
		t.Fatal("no first item")
	}
	// No cancel RPC arrives. Swap the eligibility source while the first item
	// is held; releasing the channel orders the replacement before item checks.
	stale := auth.NewKeyStore(config.AuthConfig{})
	stale.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: []auth.DelegatedMember{{Org: batch733Org, User: batch733User, Workspace: "-"}}}, time.Now().Add(-121*time.Second))
	p.keyCheck.keys = stale
	close(release)
	waitBatch733(t, b)
	if calls.Load() != 1 || b.GetStatus() != "cancelled" || b.Summary().Cancelled != 2 {
		t.Fatalf("stale items ran: %d %s", calls.Load(), b.GetStatus())
	}
	raw, err := json.Marshal(b)
	if err != nil {
		t.Fatal(err)
	}
	var state struct {
		ErrorCode string `json:"error_code"`
	}
	if err := json.Unmarshal(raw, &state); err != nil {
		t.Fatal(err)
	}
	if state.ErrorCode != "delegation_snapshot_stale" {
		t.Fatalf("missing typed stale reason: %s", raw)
	}
	if strings.Contains(string(raw), batch733Grant) {
		t.Fatal("grant serialized")
	}
}
