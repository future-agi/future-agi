package catalogwriter

import (
	"context"
	"errors"
	"fmt"
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

func TestAsyncSubmitterCoalescesEveryGapAfterChannelSaturation(t *testing.T) {
	cfg := enabledConfig(t.TempDir())
	cfg.MaxSpoolFiles = 1
	writer, err := NewTransportWriter(cfg)
	if err != nil {
		t.Fatal(err)
	}
	seed := stageInputCount(t, writer, 1)
	if err := writer.Submit(context.Background(), seed); err != nil {
		t.Fatal(err)
	}
	actor, err := NewAsyncSubmitter(writer, 1, 1)
	if err != nil {
		t.Fatal(err)
	}
	for count := 1; count <= 4; count++ {
		actor.submit(context.Background(), stageInputCount(t, writer, count))
	}

	var ordinary *SubmissionGapError
	select {
	case err := <-actor.Gaps():
		if !errors.As(err, &ordinary) || ordinary.Metadata.InputSpans != 1 {
			t.Fatalf("ordinary gap=%v", err)
		}
	default:
		t.Fatal("ordinary gap was not observable")
	}
	select {
	case <-actor.OverflowWake():
	default:
		t.Fatal("overflow wake was not observable")
	}
	summary, ok := actor.TakeOverflow()
	if !ok || summary.Suppressed != 3 {
		t.Fatalf("overflow=%+v ok=%v", summary, ok)
	}
	var first, last *SubmissionGapError
	if !errors.As(summary.First, &first) || first.Metadata.InputSpans != 2 ||
		!errors.As(summary.Last, &last) || last.Metadata.InputSpans != 4 {
		t.Fatalf("overflow edges first=%v last=%v", summary.First, summary.Last)
	}
	if _, ok := actor.TakeOverflow(); ok {
		t.Fatal("overflow summary was not consumed")
	}
}

func stageInputCount(t *testing.T, writer *Writer, count int) Job {
	t.Helper()
	rows := make([]map[string]any, 0, count)
	for index := 0; index < count; index++ {
		rows = append(rows, canonicalSpan(
			fmt.Sprintf("2026-08-13 12:00:%02d.000001", index),
			map[string]string{"model": fmt.Sprintf("gpt-%d", index)},
		))
	}
	job, _ := writer.StageCanonicalSpans(rows)
	return job
}
