package videotest

import (
	"bytes"
	"context"
	"errors"
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"io"
	"testing"
)

// Fixtures describes a scripted happy path. Provider-specific mapping/error
// fixtures extend this foundation when concrete adapters are introduced.
type Fixtures struct {
	Job        *video.JobView
	PollStates []video.State
	FetchBytes []byte
}

func RunContractSuite(t *testing.T, a video.Adapter, f Fixtures) {
	t.Helper()
	ctx := context.Background()
	if a.Capabilities().Service == "" || a.Capabilities().Revision == "" {
		t.Fatal("unrevisioned adapter")
	}
	corr, err := a.Prepare(ctx, f.Job)
	if err != nil {
		t.Fatal(err)
	}
	result, err := a.Submit(ctx, f.Job, corr)
	if err != nil {
		t.Fatal(err)
	}
	if result.ProviderJobID == "" || !result.Normalized.Valid() {
		t.Fatal("invalid submit receipt")
	}
	ref := video.ProviderRef{ProviderJobID: result.ProviderJobID}
	for _, want := range f.PollStates {
		obs, err := a.Poll(ctx, ref)
		if err != nil {
			t.Fatal(err)
		}
		if obs.Normalized != want {
			t.Fatalf("state %s, want %s", obs.Normalized, want)
		}
	}
	if f.FetchBytes != nil {
		r, _, err := a.Fetch(ctx, ref, 0)
		if err != nil {
			t.Fatal(err)
		}
		got, err := io.ReadAll(r)
		closeErr := r.Close()
		if err != nil || closeErr != nil || !bytes.Equal(got, f.FetchBytes) {
			t.Fatal("fetch mismatch", err, closeErr)
		}
	}
	if a.Capabilities().CancelSupport == video.CancelNone {
		if _, err := a.Cancel(ctx, ref); !errors.Is(err, video.ErrCancelUnsupported) {
			t.Fatal("cancel must be unsupported", err)
		}
	}
	if a.Capabilities().CorrelationLookup == video.LookupNone {
		if _, err := a.Reconcile(ctx, f.Job, corr); !errors.Is(err, video.ErrReconcileUnsupported) {
			t.Fatal("reconcile must be unsupported", err)
		}
	}
}
