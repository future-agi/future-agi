package server

import (
	"context"
	"errors"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// Independent servers, managers, codecs and Redis clients share only the org's
// Redis authority. Count actual mock dispatches, not just successful claims.
func TestW5ApprovalConcurrentRetryDispatch(t *testing.T) {
	first := w5Gateway(t)
	second := w5GatewayForOrg(t, first.org)
	path := "/mcp/v/vs_01M4ZEB9E0J3AADR85Z4T4428X"
	servers := []*w5HTTPFixture{first, second}
	sessions := []string{first.session(t, path, "w5-owner"), second.session(t, path, "w5-owner")}
	id := w5ApprovalID(t, first.call(path, "w5-owner", sessions[0], ""))
	if first.count()+second.count() != 0 {
		t.Fatal("pending call reached an upstream")
	}
	decision := mcp.ApprovalDecision{OrgID: first.org, Decision: "approve", Actor: "synthetic-admin"}
	if _, err := second.manager.Decide(context.Background(), id, decision); err != nil {
		t.Fatal(err)
	}
	var successes atomic.Int32
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			w := servers[i%2].call(path, "w5-owner", sessions[i%2], id)
			if w.Code != 200 || strings.Contains(w.Body.String(), `"error":`) {
				t.Errorf("retry protocol failure: %d %s", w.Code, w.Body.String())
				return
			}
			if !strings.Contains(w.Body.String(), `"isError":true`) {
				successes.Add(1)
			}
		}(i)
	}
	close(start)
	wg.Wait()
	if count := first.count() + second.count(); count != 1 || successes.Load() != 1 {
		t.Fatalf("upstream dispatches=%d successful responses=%d", count, successes.Load())
	}
	for i, f := range servers {
		if _, err := f.manager.Decide(context.Background(), id, decision); !errors.Is(err, mcp.ErrApprovalConflict) {
			t.Fatalf("server %d reset used approval: %v", i, err)
		}
		rec, err := f.manager.Poll(context.Background(), id, mcp.Principal{Kind: mcp.PrincipalKey, OrgID: first.org, KeyID: "owner"}, "vs_01M4ZEB9E0J3AADR85Z4T4428X")
		if err != nil || rec.Status != "used" {
			t.Fatalf("server %d observed %+v, %v", i, rec, err)
		}
		w := f.call(path, "w5-owner", sessions[i], id)
		if w.Code != 200 || !strings.Contains(w.Body.String(), `"isError":true`) {
			t.Fatalf("server %d accepted replay: %d %s", i, w.Code, w.Body.String())
		}
	}
	if count := first.count() + second.count(); count != 1 {
		t.Fatalf("replay reached an upstream: %d", count)
	}
}
