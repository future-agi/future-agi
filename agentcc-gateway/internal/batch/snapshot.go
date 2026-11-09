package batch

import (
	"encoding/json"
	"sort"
	"time"
)

// Snapshot is deliberately credential-free. It is also safe to serialize
// concurrently with processing or repeated cancellation/readback.
type Snapshot struct {
	ID          string     `json:"batch_id"`
	Status      string     `json:"status"`
	ErrorCode   string     `json:"error_code,omitempty"`
	OrgID       string     `json:"org_id"`
	KeyID       string     `json:"key_id,omitempty"`
	Total       int        `json:"total"`
	Results     []*Result  `json:"results,omitempty"`
	MaxConc     int        `json:"max_concurrency"`
	CreatedAt   time.Time  `json:"created_at"`
	CompletedAt *time.Time `json:"completed_at,omitempty"`
}

func (b *Batch) Snapshot() Snapshot {
	b.mu.Lock()
	defer b.mu.Unlock()
	return Snapshot{ID: b.ID, Status: b.Status, ErrorCode: b.ErrorCode, OrgID: b.OrgID, KeyID: b.KeyID, Total: b.Total, Results: append([]*Result(nil), b.Results...), MaxConc: b.MaxConc, CreatedAt: b.CreatedAt, CompletedAt: b.CompletedAt}
}
func (b *Batch) MarshalJSON() ([]byte, error) { return json.Marshal(b.Snapshot()) }

// Counts are the item tallies get's summary reports: completed counts every
// item that ran (failed ones included), cancelled every item that never ran.
type Counts struct {
	Completed int `json:"completed"`
	Failed    int `json:"failed"`
	Cancelled int `json:"cancelled"`
}

// ListRow is one batch in a GET /-/batches listing, in either mode (733
// add.13(3)): identity, progress and timestamps only. It has no field for
// results, request bodies, prompt text or max_concurrency, so neither listing
// can carry them; get (Snapshot) keeps its own contract.
type ListRow struct {
	ID          string     `json:"batch_id"`
	Status      string     `json:"status"`
	ErrorCode   string     `json:"error_code,omitempty"`
	OrgID       string     `json:"org_id"`
	KeyID       string     `json:"key_id"`
	Total       int        `json:"total"`
	Counts      Counts     `json:"counts"`
	CreatedAt   time.Time  `json:"created_at"`
	CompletedAt *time.Time `json:"completed_at,omitempty"`
}

// Inventory is the admin listing: every org's batches, sorted by id.
func (p *Processor) Inventory() []ListRow {
	return p.listRows(func(string) bool { return true })
}

// OrgInventory is Inventory for one org's caller: only batches whose OrgID is
// orgID, sorted by id. An empty orgID lists nothing, so a batch without an
// org is never anyone's.
func (p *Processor) OrgInventory(orgID string) []ListRow {
	if orgID == "" {
		return []ListRow{}
	}
	return p.listRows(func(org string) bool { return org == orgID })
}

func (p *Processor) listRows(keep func(orgID string) bool) []ListRow {
	rows := []ListRow{}
	p.batches.Range(func(_, value any) bool {
		if row, ok := value.(*Batch).listRow(keep); ok {
			rows = append(rows, row)
		}
		return true
	})
	sort.Slice(rows, func(i, j int) bool { return rows[i].ID < rows[j].ID })
	return rows
}

// listRow reads b's row if keep accepts its org; the org check and the row's
// fields come from one locked read.
func (b *Batch) listRow(keep func(orgID string) bool) (ListRow, bool) {
	b.mu.Lock()
	if !keep(b.OrgID) {
		b.mu.Unlock()
		return ListRow{}, false
	}
	row := ListRow{ID: b.ID, Status: b.Status, ErrorCode: b.ErrorCode, OrgID: b.OrgID, KeyID: b.KeyID, Total: b.Total, CreatedAt: b.CreatedAt}
	if b.CompletedAt != nil {
		at := *b.CompletedAt
		row.CompletedAt = &at
	}
	b.mu.Unlock()
	row.Counts = Counts{Completed: int(b.completed.Load()), Failed: int(b.failed.Load()), Cancelled: int(b.cancelled.Load())}
	return row, true
}
