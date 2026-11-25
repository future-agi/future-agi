package batch

import (
	"context"
	"errors"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func successHandler(_ context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
	return &models.ChatCompletionResponse{
		ID:    "resp-1",
		Model: req.Model,
		Usage: &models.Usage{
			PromptTokens:     100,
			CompletionTokens: 50,
		},
	}, map[string]string{"cost": "0.005000"}, nil
}

func errorHandler(_ context.Context, _ *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
	return nil, nil, errors.New("provider error")
}

func slowHandler(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
	select {
	case <-time.After(500 * time.Millisecond):
		return successHandler(ctx, req)
	case <-ctx.Done():
		return nil, nil, ctx.Err()
	}
}

func makeRequests(n int) []*models.ChatCompletionRequest {
	reqs := make([]*models.ChatCompletionRequest, n)
	for i := range reqs {
		reqs[i] = &models.ChatCompletionRequest{Model: "gpt-4"}
	}
	return reqs
}

func TestSubmitAndComplete(t *testing.T) {
	p := NewProcessor(successHandler)
	b := p.Submit(makeRequests(3), 5)

	if b.ID == "" || !strings.HasPrefix(b.ID, "batch-") {
		t.Fatalf("invalid batch ID: %q", b.ID)
	}
	if b.Total != 3 {
		t.Fatalf("Total = %d, want 3", b.Total)
	}

	// Wait for completion.
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		b2, ok := p.Get(b.ID)
		if ok && b2.GetStatus() == "completed" {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}

	b, ok := p.Get(b.ID)
	if !ok {
		t.Fatal("batch not found")
	}
	if b.GetStatus() != "completed" {
		t.Fatalf("Status = %q, want completed", b.Status)
	}
	if b.CompletedCount() != 3 {
		t.Fatalf("CompletedCount = %d, want 3", b.CompletedCount())
	}
}

func TestResultsCollected(t *testing.T) {
	p := NewProcessor(successHandler)
	b := p.Submit(makeRequests(2), 5)

	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		b2, _ := p.Get(b.ID)
		if b2.GetStatus() == "completed" {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}

	b, _ = p.Get(b.ID)
	for i, r := range b.GetResults() {
		if r.Status != "success" {
			t.Fatalf("result[%d].Status = %q, want success", i, r.Status)
		}
		if r.Response == nil {
			t.Fatalf("result[%d].Response is nil", i)
		}
		if r.InputTokens != 100 {
			t.Fatalf("result[%d].InputTokens = %d, want 100", i, r.InputTokens)
		}
		if r.OutputTokens != 50 {
			t.Fatalf("result[%d].OutputTokens = %d, want 50", i, r.OutputTokens)
		}
		if r.Cost != 0.005 {
			t.Fatalf("result[%d].Cost = %f, want 0.005", i, r.Cost)
		}
	}
}

func TestSummaryAggregation(t *testing.T) {
	p := NewProcessor(successHandler)
	b := p.Submit(makeRequests(3), 5)

	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		b2, _ := p.Get(b.ID)
		if b2.GetStatus() == "completed" {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}

	b, _ = p.Get(b.ID)
	s := b.Summary()
	if s.TotalInputTokens != 300 {
		t.Fatalf("TotalInputTokens = %d, want 300", s.TotalInputTokens)
	}
	if s.TotalOutputTokens != 150 {
		t.Fatalf("TotalOutputTokens = %d, want 150", s.TotalOutputTokens)
	}
	if s.TotalCost != 0.015 {
		t.Fatalf("TotalCost = %f, want 0.015", s.TotalCost)
	}
	if s.Completed != 3 {
		t.Fatalf("Completed = %d, want 3", s.Completed)
	}
}

func TestErrorResults(t *testing.T) {
	p := NewProcessor(errorHandler)
	b := p.Submit(makeRequests(2), 5)

	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		b2, _ := p.Get(b.ID)
		if b2.GetStatus() == "completed" {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}

	b, _ = p.Get(b.ID)
	if b.FailedCount() != 2 {
		t.Fatalf("FailedCount = %d, want 2", b.FailedCount())
	}
	for i, r := range b.GetResults() {
		if r.Status != "error" {
			t.Fatalf("result[%d].Status = %q, want error", i, r.Status)
		}
		if r.Error != "provider error" {
			t.Fatalf("result[%d].Error = %q", i, r.Error)
		}
	}
}

func startCancellationTestBatch(t *testing.T, handler BatchHandler, n int) (*Processor, *Batch, <-chan struct{}) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	requests := makeRequests(n)
	p := &Processor{handler: handler}
	b := &Batch{
		ID:        "batch-cancel-test",
		Status:    "processing",
		Total:     n,
		Results:   make([]*Result, n),
		MaxConc:   1,
		CreatedAt: time.Now(),
		cancel:    cancel,
	}
	for i := range b.Results {
		b.Results[i] = &Result{Index: i, Status: "pending"}
	}
	p.batches.Store(b.ID, b)
	done := make(chan struct{})
	go func() {
		p.processBatch(ctx, b, requests)
		close(done)
	}()
	return p, b, done
}

func waitForBatchSignal(t *testing.T, signal <-chan struct{}) {
	t.Helper()
	select {
	case <-signal:
	case <-time.After(5 * time.Second):
		t.Fatal("timed out waiting for batch signal")
	}
}

func TestCancel(t *testing.T) {
	entered := make(chan struct{})
	p, b, done := startCancellationTestBatch(t, func(ctx context.Context, _ *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
		close(entered)
		<-ctx.Done()
		return nil, nil, ctx.Err()
	}, 5)
	waitForBatchSignal(t, entered)

	cancel := b.cancel
	b.cancel = func() {
		cancel()
		// Force finalization before Cancel proceeds past its cancellation signal.
		waitForBatchSignal(t, done)
	}
	got, ok := p.Cancel(b.ID)
	if !ok || got != b {
		t.Fatal("Cancel did not return the batch")
	}
	if status := b.GetStatus(); status != "cancelled" {
		t.Fatalf("Status = %q, want cancelled", status)
	}
	if b.GetCompletedAt() == nil {
		t.Fatal("CompletedAt should be set after processing drains")
	}

	failed, cancelled := 0, 0
	for i, r := range b.GetResults() {
		if r == nil || r.Index != i {
			t.Fatalf("result[%d] missing or has incorrect index", i)
		}
		switch r.Status {
		case "error":
			failed++
			if r.Error != context.Canceled.Error() {
				t.Fatalf("result[%d].Error = %q, want context canceled", i, r.Error)
			}
		case "cancelled":
			cancelled++
		default:
			t.Fatalf("result[%d].Status = %q, want error or cancelled", i, r.Status)
		}
	}
	if failed != 1 || cancelled != 4 {
		t.Fatalf("result counts: errors = %d, cancelled = %d, want 1 and 4", failed, cancelled)
	}
	if s := b.Summary(); s.Completed != 1 || s.Failed != 1 || s.Cancelled != 4 {
		t.Fatalf("unexpected summary after cancellation: %+v", s)
	}
}

func TestCancelCompletedRepeated(t *testing.T) {
	p, b, done := startCancellationTestBatch(t, successHandler, 1)
	waitForBatchSignal(t, done)
	if status := b.GetStatus(); status != "completed" {
		t.Fatalf("Status = %q, want completed", status)
	}
	completedAt := b.GetCompletedAt()
	if completedAt == nil {
		t.Fatal("CompletedAt should be set")
	}
	result := *b.GetResults()[0]
	if result.Status != "success" || result.Response == nil {
		t.Fatalf("unexpected completed result: %+v", result)
	}
	summary := b.Summary()
	for i := 0; i < 2; i++ {
		got, ok := p.Cancel(b.ID)
		if !ok || got != b {
			t.Fatal("Cancel did not return the batch")
		}
		if status := b.GetStatus(); status != "completed" {
			t.Fatalf("Status = %q, want completed", status)
		}
		if b.GetCompletedAt() != completedAt {
			t.Fatal("Cancel changed CompletedAt")
		}
		if *b.GetResults()[0] != result || b.Summary() != summary {
			t.Fatal("Cancel changed completed results or summary")
		}
	}
}

func TestCancelActiveRepeated(t *testing.T) {
	entered := make(chan struct{})
	drainCtx, release := context.WithCancel(context.Background())
	defer release()
	p, b, done := startCancellationTestBatch(t, func(ctx context.Context, _ *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
		close(entered)
		<-ctx.Done()
		<-drainCtx.Done()
		return nil, nil, ctx.Err()
	}, 1)
	waitForBatchSignal(t, entered)

	for i := 0; i < 2; i++ {
		cancelDone := make(chan struct{})
		var got *Batch
		var ok bool
		go func() {
			got, ok = p.Cancel(b.ID)
			close(cancelDone)
		}()
		waitForBatchSignal(t, cancelDone)
		if !ok || got != b {
			t.Fatal("Cancel did not return the batch")
		}
		if status := b.GetStatus(); status != "cancelled" {
			t.Fatalf("Status = %q, want cancelled", status)
		}
		if b.GetCompletedAt() != nil {
			t.Fatal("CompletedAt set before processing drains")
		}
		if b.GetResults()[0].Status != "pending" || b.Summary() != (Summary{}) {
			t.Fatal("Cancel changed results or summary before processing drains")
		}
	}

	release()
	waitForBatchSignal(t, done)
	if status := b.GetStatus(); status != "cancelled" {
		t.Fatalf("Status = %q after drainage, want cancelled", status)
	}
	if b.GetCompletedAt() == nil {
		t.Fatal("CompletedAt should be set after processing drains")
	}
	if result := b.GetResults()[0]; result.Status != "error" || result.Error != context.Canceled.Error() {
		t.Fatalf("unexpected cancelled handler result: %+v", result)
	}
	if s := b.Summary(); s.Completed != 1 || s.Failed != 1 || s.Cancelled != 0 {
		t.Fatalf("unexpected summary after drainage: %+v", s)
	}
}

func TestGetUnknown(t *testing.T) {
	p := NewProcessor(successHandler)
	_, ok := p.Get("nonexistent")
	if ok {
		t.Fatal("Get should return false for unknown batch")
	}
}

func TestCancelUnknown(t *testing.T) {
	p := NewProcessor(successHandler)
	_, ok := p.Cancel("nonexistent")
	if ok {
		t.Fatal("Cancel should return false for unknown batch")
	}
}

func TestEmptyBatch(t *testing.T) {
	p := NewProcessor(successHandler)
	b := p.Submit(nil, 5)
	if b.Total != 0 {
		t.Fatalf("Total = %d, want 0", b.Total)
	}

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		b2, _ := p.Get(b.ID)
		if b2.GetStatus() == "completed" {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}

	b, _ = p.Get(b.ID)
	if b.GetStatus() != "completed" {
		t.Fatalf("empty batch Status = %q, want completed", b.Status)
	}
}

func TestMaxConcurrency(t *testing.T) {
	var inflight atomic.Int32
	var maxInflight atomic.Int32

	handler := func(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error) {
		cur := inflight.Add(1)
		// Track max.
		for {
			prev := maxInflight.Load()
			if int32(cur) <= prev {
				break
			}
			if maxInflight.CompareAndSwap(prev, int32(cur)) {
				break
			}
		}
		time.Sleep(20 * time.Millisecond)
		inflight.Add(-1)
		return successHandler(ctx, req)
	}

	p := NewProcessor(handler)
	b := p.Submit(makeRequests(20), 3)

	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		b2, _ := p.Get(b.ID)
		if b2.GetStatus() == "completed" {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}

	b, _ = p.Get(b.ID)
	if b.GetStatus() != "completed" {
		t.Fatalf("Status = %q, want completed", b.Status)
	}

	max := maxInflight.Load()
	if max > 3 {
		t.Fatalf("maxInflight = %d, want <= 3", max)
	}
}

func TestDefaultConcurrency(t *testing.T) {
	p := NewProcessor(successHandler)
	b := p.Submit(makeRequests(3), 0)
	// 0 should default to 10, but capped to len(requests)=3
	if b.MaxConc != 3 {
		t.Fatalf("MaxConc = %d, want 3 (capped to total)", b.MaxConc)
	}
}

func TestBatchCompletedAt(t *testing.T) {
	p := NewProcessor(successHandler)
	b := p.Submit(makeRequests(1), 1)

	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		b2, _ := p.Get(b.ID)
		if b2.GetStatus() == "completed" {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}

	b, _ = p.Get(b.ID)
	completedAt := b.GetCompletedAt()
	if completedAt == nil {
		t.Fatal("CompletedAt should be set")
	}
	if completedAt.Before(b.CreatedAt) {
		t.Fatal("CompletedAt should be after CreatedAt")
	}
}
