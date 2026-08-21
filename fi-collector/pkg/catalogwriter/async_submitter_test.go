package catalogwriter

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestAsyncSubmitterIsBoundedAndNonBlocking(t *testing.T) {
	writer, err := New(enabledConfig(t.TempDir()), &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	actor, err := NewAsyncSubmitter(writer, 1, 1)
	if err != nil {
		t.Fatal(err)
	}
	job, _ := writer.StageCanonicalSpans([]map[string]any{
		canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"model": "gpt"}),
	})
	if err := actor.Enqueue(job); err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	err = actor.Enqueue(job)
	if time.Since(started) > 10*time.Millisecond {
		t.Fatalf("full enqueue blocked for %s", time.Since(started))
	}
	var gap *SubmissionGapError
	if !errors.As(err, &gap) || gap.Metadata.InputSpans != 1 {
		t.Fatalf("queue gap=%v", err)
	}
}

func TestAsyncSubmitterRunsWALOffCallerAndStops(t *testing.T) {
	writer, err := New(enabledConfig(t.TempDir()), &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	actor, _ := NewAsyncSubmitter(writer, 2, 2)
	ctx, cancel := context.WithCancel(context.Background())
	actor.Run(ctx)
	job, _ := writer.StageCanonicalSpans([]map[string]any{
		canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"model": "gpt"}),
	})
	if err := actor.Enqueue(job); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(time.Second)
	for {
		pending, err := writer.Pending()
		if err != nil {
			t.Fatal(err)
		}
		if len(pending) == 1 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("async job was not durably spooled")
		}
		time.Sleep(time.Millisecond)
	}
	cancel()
	actor.Wait()
}

func TestAsyncSubmitterGracefulStopDrainsAcceptedQueue(t *testing.T) {
	writer, err := New(enabledConfig(t.TempDir()), &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	actor, _ := NewAsyncSubmitter(writer, 2, 2)
	job, _ := writer.StageCanonicalSpans([]map[string]any{
		canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"model": "gpt"}),
	})
	if err := actor.Enqueue(job); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	actor.Run(ctx)
	cancel()
	actor.Wait()
	pending, err := writer.Pending()
	if err != nil || len(pending) != 1 {
		t.Fatalf("shutdown pending=%v err=%v", pending, err)
	}
	if err := actor.Enqueue(job); err == nil {
		t.Fatal("stopped actor accepted work")
	}
}
