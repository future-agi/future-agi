package batch

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// BatchHandler processes a single chat completion request through the pipeline.
// Returns the response, metadata (with "cost" key if available, and
// MetaRequestID once the item ran), and error. ctx carries the trace id of the
// batch request that submitted the item (models.GetTraceID), when it had one.
type BatchHandler func(ctx context.Context, req *models.ChatCompletionRequest) (*models.ChatCompletionResponse, map[string]string, error)

// MetaRequestID is the metadata key a BatchHandler sets to the request id of
// the item's run: the id the request log keeps the item's entry under. It
// becomes the item's Result.RequestID.
const MetaRequestID = "request_id"

// Processor manages batch lifecycle.
type Processor struct {
	batches   sync.Map // batch_id → *Batch
	handler   BatchHandler
	retention time.Duration
	stopCh    chan struct{}
	// keyCheck settles each submit's batch key (WithKeyStore); nil checks
	// none and requires none.
	keyCheck *KeyCheck
}

// Batch represents a group of requests processed together.
type Batch struct {
	ID     string `json:"batch_id"`
	Status string `json:"status"`
	// OrgID and KeyID are the org and id of the batch key the items run as
	// (KeyHeader); empty for a batch submitted without one. Get and cancel
	// answer only for the batch's own org (GetForOrg, CancelForOrg).
	OrgID       string     `json:"org_id"`
	KeyID       string     `json:"key_id,omitempty"`
	Total       int        `json:"total"`
	Results     []*Result  `json:"results,omitempty"`
	MaxConc     int        `json:"max_concurrency"`
	CreatedAt   time.Time  `json:"created_at"`
	CompletedAt *time.Time `json:"completed_at,omitempty"`

	cancel    context.CancelFunc
	mu        sync.Mutex
	completed atomic.Int32
	failed    atomic.Int32
	cancelled atomic.Int32
}

// Result is the outcome of a single sub-request.
type Result struct {
	Index  int    `json:"index"`
	Status string `json:"status"` // success, error, cancelled
	// RequestID is the request id the item ran with (MetaRequestID), so its
	// request-log entry can be found from the batch; empty for an item that
	// never ran (cancelled, or refused before the pipeline).
	RequestID    string                         `json:"request_id,omitempty"`
	Response     *models.ChatCompletionResponse `json:"response,omitempty"`
	Error        string                         `json:"error,omitempty"`
	Cost         float64                        `json:"cost,omitempty"`
	InputTokens  int                            `json:"input_tokens,omitempty"`
	OutputTokens int                            `json:"output_tokens,omitempty"`
}

// Summary holds aggregated batch metrics.
type Summary struct {
	TotalCost         float64 `json:"total_cost"`
	TotalInputTokens  int     `json:"total_input_tokens"`
	TotalOutputTokens int     `json:"total_output_tokens"`
	Completed         int     `json:"completed"`
	Failed            int     `json:"failed"`
	Cancelled         int     `json:"cancelled"`
}

const defaultBatchRetention = 1 * time.Hour

// NewProcessor creates a batch processor with the given handler.
// Completed batches are cleaned up after 1 hour.
func NewProcessor(handler BatchHandler, opts ...Option) *Processor {
	p := &Processor{
		handler:   handler,
		retention: defaultBatchRetention,
		stopCh:    make(chan struct{}),
	}
	for _, opt := range opts {
		opt(p)
	}
	go p.cleanupLoop()
	return p
}

// Stop halts the background cleanup goroutine.
func (p *Processor) Stop() {
	close(p.stopCh)
}

func (p *Processor) cleanupLoop() {
	ticker := time.NewTicker(5 * time.Minute)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			now := time.Now()
			p.batches.Range(func(key, val any) bool {
				b := val.(*Batch)
				if ct := b.GetCompletedAt(); ct != nil && now.Sub(*ct) > p.retention {
					p.batches.Delete(key)
				}
				return true
			})
		case <-p.stopCh:
			return
		}
	}
}

// Submit creates and starts processing a batch. Returns immediately.
func (p *Processor) Submit(requests []*models.ChatCompletionRequest, maxConc int) *Batch {
	return p.SubmitWithTraceID(requests, maxConc, "")
}

// SubmitWithTraceID is Submit for a batch request whose trace id is traceID:
// each item's handler gets it on its context (models.WithTraceID), so every
// item's run carries the trace id of the request that submitted it.
func (p *Processor) SubmitWithTraceID(requests []*models.ChatCompletionRequest, maxConc int, traceID string) *Batch {
	return p.SubmitAs(requests, maxConc, traceID, Caller{})
}

// SubmitAs is SubmitWithTraceID for a batch that runs as caller (CallerFor):
// the batch records the caller's org and key id, and each item's handler gets
// the caller on its context, which RunAsChatRequest sends as the item's
// credential and client address.
func (p *Processor) SubmitAs(requests []*models.ChatCompletionRequest, maxConc int, traceID string, caller Caller) *Batch {
	if maxConc <= 0 {
		maxConc = 10
	}
	if maxConc > len(requests) {
		maxConc = len(requests)
	}

	ctx, cancel := context.WithCancel(context.Background())
	if traceID != "" {
		ctx = models.WithTraceID(ctx, traceID)
	}
	ctx = withCaller(ctx, caller)

	b := &Batch{
		ID:        generateBatchID(),
		Status:    "processing",
		OrgID:     caller.OrgID,
		KeyID:     caller.KeyID,
		Total:     len(requests),
		Results:   make([]*Result, len(requests)),
		MaxConc:   maxConc,
		CreatedAt: time.Now(),
		cancel:    cancel,
	}

	// Pre-initialize results.
	for i := range b.Results {
		b.Results[i] = &Result{Index: i, Status: "pending"}
	}

	p.batches.Store(b.ID, b)

	go p.processBatch(ctx, b, requests)

	return b
}

// Get returns a batch by ID.
func (p *Processor) Get(batchID string) (*Batch, bool) {
	raw, ok := p.batches.Load(batchID)
	if !ok {
		return nil, false
	}
	return raw.(*Batch), true
}

// GetForOrg is Get for a caller acting for orgID: a batch of another org
// (or, for an orgID of "", any batch with an org) is not found.
func (p *Processor) GetForOrg(batchID, orgID string) (*Batch, bool) {
	b, ok := p.Get(batchID)
	if !ok || b.OrgID != orgID {
		return nil, false
	}
	return b, true
}

// CancelForOrg is Cancel for a caller acting for orgID: a batch of another
// org is not found and keeps running.
func (p *Processor) CancelForOrg(batchID, orgID string) (*Batch, bool) {
	if _, ok := p.GetForOrg(batchID, orgID); !ok {
		return nil, false
	}
	return p.Cancel(batchID)
}

// Cancel cancels a batch and returns its current state.
func (p *Processor) Cancel(batchID string) (*Batch, bool) {
	raw, ok := p.batches.Load(batchID)
	if !ok {
		return nil, false
	}
	b := raw.(*Batch)
	b.cancel()

	b.mu.Lock()
	if b.Status == "processing" {
		b.Status = "cancelled"
	}
	b.mu.Unlock()

	return b, true
}

func (p *Processor) processBatch(ctx context.Context, b *Batch, requests []*models.ChatCompletionRequest) {
	sem := make(chan struct{}, b.MaxConc)
	var wg sync.WaitGroup

	for i, req := range requests {
		wg.Add(1)
		go func(idx int, r *models.ChatCompletionRequest) {
			defer wg.Done()

			// Acquire semaphore.
			select {
			case sem <- struct{}{}:
				defer func() { <-sem }()
			case <-ctx.Done():
				b.mu.Lock()
				b.Results[idx] = &Result{Index: idx, Status: "cancelled"}
				b.mu.Unlock()
				b.cancelled.Add(1)
				return
			}

			// Check cancellation before calling handler.
			if ctx.Err() != nil {
				b.mu.Lock()
				b.Results[idx] = &Result{Index: idx, Status: "cancelled"}
				b.mu.Unlock()
				b.cancelled.Add(1)
				return
			}

			resp, metadata, err := p.handler(ctx, r)

			result := &Result{Index: idx, RequestID: metadata[MetaRequestID]}
			if err != nil {
				result.Status = "error"
				result.Error = err.Error()
				b.failed.Add(1)
			} else {
				result.Status = "success"
				result.Response = resp
				if resp != nil && resp.Usage != nil {
					result.InputTokens = resp.Usage.PromptTokens
					result.OutputTokens = resp.Usage.CompletionTokens
				}
				if costStr, ok := metadata["cost"]; ok {
					if cost, err := strconv.ParseFloat(costStr, 64); err == nil {
						result.Cost = cost
					}
				}
			}

			b.mu.Lock()
			b.Results[idx] = result
			b.mu.Unlock()
			b.completed.Add(1)
		}(i, req)
	}

	wg.Wait()

	b.mu.Lock()
	if b.Status == "processing" {
		b.Status = "completed"
	}
	now := time.Now()
	b.CompletedAt = &now
	b.mu.Unlock()
}

// Summary returns aggregated metrics for the batch.
func (b *Batch) Summary() Summary {
	s := Summary{
		Completed: int(b.completed.Load()),
		Failed:    int(b.failed.Load()),
		Cancelled: int(b.cancelled.Load()),
	}
	b.mu.Lock()
	for _, r := range b.Results {
		if r == nil {
			continue
		}
		s.TotalCost += r.Cost
		s.TotalInputTokens += r.InputTokens
		s.TotalOutputTokens += r.OutputTokens
	}
	b.mu.Unlock()
	return s
}

// CompletedCount returns the number of completed sub-requests.
func (b *Batch) CompletedCount() int {
	return int(b.completed.Load())
}

// FailedCount returns the number of failed sub-requests.
func (b *Batch) FailedCount() int {
	return int(b.failed.Load())
}

// GetStatus returns the batch status under the lock.
func (b *Batch) GetStatus() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.Status
}

// GetCompletedAt returns the completion time under the lock.
func (b *Batch) GetCompletedAt() *time.Time {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.CompletedAt
}

// GetResults returns a copy of results under the lock.
func (b *Batch) GetResults() []*Result {
	b.mu.Lock()
	defer b.mu.Unlock()
	cp := make([]*Result, len(b.Results))
	copy(cp, b.Results)
	return cp
}

func generateBatchID() string {
	b := make([]byte, 6)
	if _, err := rand.Read(b); err != nil {
		panic("crypto/rand.Read failed: " + err.Error())
	}
	return "batch-" + hex.EncodeToString(b)
}
