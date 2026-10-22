package server

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/traceavailable"
	"github.com/google/uuid"
)

type rootNotifierStub struct {
	roots  []traceavailable.Root
	stored *bool
	t      *testing.T
}

func (n *rootNotifierStub) EnqueueRoots(roots []traceavailable.Root) error {
	if !*n.stored {
		n.t.Fatal("notification before canonical write acknowledgment")
	}
	n.roots = append(n.roots, roots...)
	return errors.New("notification transport unavailable") // must not affect canonical storage
}

func TestRootNotificationFollowsSuccessfulCanonicalInsert(t *testing.T) {
	for _, fail := range []bool{false, true} {
		t.Run(map[bool]string{false: "stored", true: "write_failed"}[fail], func(t *testing.T) {
			stored := false
			ch := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if fail {
					w.WriteHeader(400)
					return
				}
				stored = true
				w.WriteHeader(200)
			}))
			defer ch.Close()
			writer, err := chwriter.New(chwriter.Config{URL: ch.URL, Database: "default", Table: "spans", MaxRetries: 1,
				RequestTimeout: time.Second, InitialBackoff: time.Millisecond, MaxBackoff: time.Millisecond, DeadLetterFile: t.TempDir() + "/dl.jsonl"})
			if err != nil {
				t.Fatal(err)
			}
			notifier := &rootNotifierStub{stored: &stored, t: t}
			s := New(Config{BatchMaxRows: 100}, writer, nil, nil, nil, WithTraceNotifier(notifier))
			org, project, trace := uuid.NewString(), uuid.NewString(), uuid.NewString()
			s.enqueueScoped([]map[string]any{{"id": "abcdef0123456789", "trace_id": trace, "project_id": project,
				"parent_span_id": "", "end_time": "2026-09-12 12:00:00"}}, nil, org, "", map[string]struct{}{project: {}})
			if len(notifier.roots) != 0 {
				t.Fatal("notified before flush")
			}
			s.drainNow(context.Background())
			expected := 1
			if fail {
				expected = 0
			}
			if len(notifier.roots) != expected {
				t.Fatalf("notifications=%d want=%d", len(notifier.roots), expected)
			}
			s.drainNow(context.Background())
			if len(notifier.roots) != expected {
				t.Fatal("empty flush repeated notification")
			}
		})
	}
}
