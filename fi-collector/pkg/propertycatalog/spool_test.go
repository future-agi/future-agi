package propertycatalog

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

type recordingPublisher struct {
	envelopes []WireEnvelope
	err       error
}

func (p *recordingPublisher) Publish(_ context.Context, envelope WireEnvelope) error {
	p.envelopes = append(p.envelopes, envelope)
	return p.err
}

func testSpool(t *testing.T) (*Spool, string) {
	t.Helper()
	directory := t.TempDir()
	spool, err := NewSpool(SpoolConfig{Directory: directory, MaxFiles: 8, MaxBytes: 8 << 20})
	if err != nil {
		t.Fatal(err)
	}
	spool.now = func() time.Time { return time.Date(2026, 8, 14, 1, 2, 3, 4, time.UTC) }
	return spool, directory
}

func TestSpoolDurablyReplaysAndExactEnqueueIsIdempotent(t *testing.T) {
	spool, directory := testSpool(t)
	envelope := mustEnvelope(t, testEnvelopeInput(t, 1, ZeroSHA256, 1))
	if err := spool.Enqueue(envelope); err != nil {
		t.Fatal(err)
	}
	if err := spool.Enqueue(envelope); err != nil {
		t.Fatal(err)
	}
	entries, _ := os.ReadDir(directory)
	if len(entries) != 1 {
		t.Fatalf("exact duplicate created %d files", len(entries))
	}

	restarted, err := NewSpool(SpoolConfig{Directory: directory, MaxFiles: 8, MaxBytes: 8 << 20})
	if err != nil {
		t.Fatal(err)
	}
	publisher := &recordingPublisher{}
	result, err := restarted.Replay(context.Background(), publisher)
	if err != nil || result.Attempted != 1 || result.Delivered != 1 || len(publisher.envelopes) != 1 {
		t.Fatalf("replay=%+v published=%d err=%v", result, len(publisher.envelopes), err)
	}
	entries, _ = os.ReadDir(directory)
	if len(entries) != 0 {
		t.Fatalf("acknowledged envelope remained: %v", entries)
	}
}

func TestSpoolRetainsFailedAndCorruptEnvelopes(t *testing.T) {
	spool, directory := testSpool(t)
	if err := spool.Enqueue(mustEnvelope(t, testEnvelopeInput(t, 1, ZeroSHA256, 1))); err != nil {
		t.Fatal(err)
	}
	publisher := &recordingPublisher{err: errors.New("Kafka unavailable")}
	result, err := spool.Replay(context.Background(), publisher)
	if err == nil || result.Attempted != 1 || result.Delivered != 0 {
		t.Fatalf("failed replay=%+v err=%v", result, err)
	}
	entries, _ := os.ReadDir(directory)
	if len(entries) != 1 {
		t.Fatalf("failed envelope not retained: %v", entries)
	}

	path := filepath.Join(directory, entries[0].Name())
	raw, _ := os.ReadFile(path)
	raw[len(raw)/2] ^= 1
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	restarted, err := NewSpool(SpoolConfig{Directory: directory, MaxFiles: 8, MaxBytes: 8 << 20})
	if err != nil {
		t.Fatal(err)
	}
	result, err = restarted.Replay(context.Background(), &recordingPublisher{})
	if err == nil || result.Delivered != 0 {
		t.Fatalf("corrupt replay=%+v err=%v", result, err)
	}
	entries, _ = os.ReadDir(directory)
	if len(entries) != 1 {
		t.Fatal("corrupt durable envelope was deleted")
	}
}

type blockingPublisher struct {
	started chan struct{}
	release chan struct{}
}

func (p *blockingPublisher) Publish(ctx context.Context, _ WireEnvelope) error {
	close(p.started)
	select {
	case <-p.release:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func TestBlockedReplayDoesNotBlockConcurrentEnqueue(t *testing.T) {
	spool, directory := testSpool(t)
	first := mustEnvelope(t, testEnvelopeInput(t, 1, ZeroSHA256, 1))
	if err := spool.Enqueue(first); err != nil {
		t.Fatal(err)
	}
	publisher := &blockingPublisher{started: make(chan struct{}), release: make(chan struct{})}
	replayDone := make(chan error, 1)
	go func() {
		_, err := spool.Replay(context.Background(), publisher)
		replayDone <- err
	}()
	<-publisher.started
	second := mustEnvelope(t, testEnvelopeInput(t, 2, first.PayloadSHA256(), 1))
	enqueueDone := make(chan error, 1)
	go func() { enqueueDone <- spool.Enqueue(second) }()
	select {
	case err := <-enqueueDone:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("network-blocked replay held enqueue mutex")
	}
	close(publisher.release)
	if err := <-replayDone; err != nil {
		t.Fatal(err)
	}
	entries, _ := os.ReadDir(directory)
	if len(entries) != 1 {
		t.Fatalf("replay removed concurrently enqueued envelope: %v", entries)
	}
}
