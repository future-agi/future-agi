package sessionlease

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func fixturePolicy() Policy {
	return Policy{Sessions: Limits{4, 2, 1}, Calls: Limits{4, 2, 1}, Streams: Limits{4, 2, 1}, MaxIdle: time.Minute, MaxAbsolute: time.Hour, Version: "synthetic-policy-v1"}
}
func fixtureRequest(id string) Request {
	return Request{Binding: Binding{ID: id, Scope: Scope{"synthetic-org", "synthetic-workspace"}, Actor: Actor{"gateway_key", "synthetic-person", "synthetic-key"}, SessionHash: "synthetic-session", Purpose: Session}, Limits: Limits{4, 2, 1}, TTL: time.Second, Idle: time.Minute, Absolute: time.Hour}
}
func TestPolicyAndRequestBounds(t *testing.T) {
	p := fixturePolicy()
	for _, mutate := range []func(*Request){
		func(r *Request) { r.TTL = 3*time.Second + 1 },
		func(r *Request) { r.TTL = time.Nanosecond },
		func(r *Request) { r.Idle = r.Absolute + 1 },
		func(r *Request) { r.Limits.Org++ },
		func(r *Request) { r.Scope.WorkspaceID = "" },
		func(r *Request) { r.Purpose = 99 },
		func(r *Request) { r.Actor.Kind = "client_asserted" },
	} {
		r := fixtureRequest("synthetic-id")
		mutate(&r)
		if validateRequest(r, p) == nil {
			t.Fatalf("invalid request accepted: %+v", r)
		}
	}
	if err := validateRequest(fixtureRequest("synthetic-id"), p); err != nil {
		t.Fatal(err)
	}
}
func TestLengthFramingAndSameOrgClusterSlot(t *testing.T) {
	a := fixtureRequest("synthetic-id")
	b := a
	b.Scope.WorkspaceID = "another-workspace"
	ar := routeFor(a, fixturePolicy())
	br := routeFor(b, fixturePolicy())
	if ar.directory == br.directory {
		t.Fatal("binding aliased across workspace")
	}
	for _, k := range ar.keys {
		if !strings.Contains(k, "{o:"+ar.org+"}") {
			t.Fatal("cross-slot quota script")
		}
	}
	if ar.org != br.org || ar.workspace == br.workspace {
		t.Fatal("scope routing")
	}
	if digest("a", "bc") == digest("ab", "c") {
		t.Fatal("unframed hash")
	}
	if strings.Contains(strings.Join(ar.keys, ""), a.Scope.OrgID) {
		t.Fatal("raw scope in key")
	}
}
func TestReleaseFencesUnknownAndAcquireUsesSingleBudget(t *testing.T) {
	calls := 0
	s := &Store{policy: fixturePolicy(), eval: func(ctx context.Context, script string, keys []string, args ...any) (any, error) {
		calls++
		if _, ok := ctx.Deadline(); !ok {
			t.Fatal("missing operation deadline")
		}
		if args[0] == "release" {
			return "*", nil
		}
		return nil, errors.New("synthetic-redis-detail")
	}}
	r := fixtureRequest("synthetic-id")
	if err := s.Release(context.Background(), r.Binding); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Acquire(context.Background(), r); err != ErrUnavailable {
		t.Fatalf("raw error: %v", err)
	}
	if calls != 2 {
		t.Fatalf("unexpected retry count %d", calls)
	}
}
func TestNoLeaseOnMalformedOrLateReply(t *testing.T) {
	for _, reply := range []any{nil, []any{"OK", int64(10), int64(9), int64(20), int64(30)}, []any{"OK", int64(10), int64(40), int64(20), int64(30)}, []any{"UNKNOWN"}} {
		if _, err := decodeLease(reply, fixtureRequest("synthetic-id").Binding); err == nil {
			t.Fatal("malformed lease accepted")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	s := &Store{policy: fixturePolicy(), eval: func(_ context.Context, _ string, _ []string, args ...any) (any, error) { cancel(); return args[1], nil }}
	if _, err := s.Acquire(ctx, fixtureRequest("synthetic-id")); err != ErrUnavailable {
		t.Fatal("post-RPC cancellation ignored")
	}
}
func TestBorrowedClientMustBeQualified(t *testing.T) {
	if _, err := New(nil, fixturePolicy()); err == nil {
		t.Fatal("nil client accepted")
	}
}
