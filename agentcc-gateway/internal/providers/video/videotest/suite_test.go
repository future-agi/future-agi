package videotest

import (
	"context"
	"errors"
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"io"
	"net/http"
	"testing"
	"time"
)

func TestContractSuiteFake(t *testing.T) {
	f := NewFakeProvider(Script{
		Submit: JSONReply(video.SubmitResult{ProviderJobID: "task", Normalized: video.StateQueued}),
		Poll:   []Reply{JSONReply(video.Observation{ProviderState: "running", Normalized: video.StateRunning}), JSONReply(video.Observation{ProviderState: "succeeded", Normalized: video.StateCompleted})},
		Fetch:  Reply{Body: MP4(), Header: http.Header{"Content-Type": []string{"video/mp4"}}},
	})
	t.Cleanup(f.Close)
	RunContractSuite(t, f.Adapter(), Fixtures{Job: &video.JobView{ID: "video_test"}, PollStates: []video.State{video.StateRunning, video.StateCompleted}, FetchBytes: MP4()})
	if f.SubmitCount("video_test") != 1 {
		t.Fatal("submit counter")
	}
}

func TestScriptErrors(t *testing.T) {
	for _, tc := range []struct {
		name   string
		reply  Reply
		schema bool
	}{{"429", Reply{Status: 429, Body: []byte("limited")}, false}, {"503", Reply{Status: 503}, false}, {"malformed", Reply{Body: []byte("{")}, true}, {"unknown state", JSONReply(video.Observation{Normalized: "new_state"}), true}} {
		t.Run(tc.name, func(t *testing.T) {
			f := NewFakeProvider(Script{Poll: []Reply{tc.reply}})
			t.Cleanup(f.Close)
			_, err := f.Adapter().Poll(context.Background(), video.ProviderRef{})
			if tc.schema {
				var se *video.SchemaError
				if !errors.As(err, &se) {
					t.Fatalf("%T %v", err, err)
				}
			} else {
				var ue *video.UpstreamError
				if !errors.As(err, &ue) || ue.Status != tc.reply.Status {
					t.Fatalf("%T %v", err, err)
				}
			}
		})
	}
}
func TestFakeDelayCancellationAndDuplicates(t *testing.T) {
	f := NewFakeProvider(Script{Submit: JSONReply(video.SubmitResult{Normalized: video.StateQueued}), Poll: []Reply{{Delay: time.Second}}})
	t.Cleanup(f.Close)
	a := f.Adapter()
	ctx := context.Background()
	for i := 0; i < 2; i++ {
		if _, err := a.Submit(ctx, &video.JobView{}, video.Correlation{Token: "same"}); err != nil {
			t.Fatal(err)
		}
	}
	if f.SubmitCount("same") != 2 {
		t.Fatal("missing duplicate")
	}
	c, cancel := context.WithTimeout(ctx, 10*time.Millisecond)
	defer cancel()
	if _, err := a.Poll(c, video.ProviderRef{}); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal(err)
	}
	if _, err := a.Cancel(ctx, video.ProviderRef{}); !errors.Is(err, video.ErrCancelUnsupported) {
		t.Fatal(err)
	}
	if _, err := a.Reconcile(ctx, &video.JobView{}, video.Correlation{}); !errors.Is(err, video.ErrReconcileUnsupported) {
		t.Fatal(err)
	}
	r, _, err := a.Fetch(ctx, video.ProviderRef{}, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	_, _ = io.ReadAll(r)
}
