package pricing

import (
	"context"
	"errors"
	"log/slog"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

type fakeRow struct {
	in, out float64
	err     error
}

func (r fakeRow) Scan(dest ...any) error {
	if r.err != nil {
		return r.err
	}
	*(dest[0].(*float64)) = r.in
	*(dest[1].(*float64)) = r.out
	return nil
}

type fakeDB struct {
	row   fakeRow
	calls int
}

func (f *fakeDB) QueryRow(ctx context.Context, sql string, args ...any) pgx.Row {
	f.calls++
	return f.row
}

func TestCustomPricing_Per1KMath(t *testing.T) {
	// Django: prompt*(input/1000) + completion*(output/1000)
	db := &fakeDB{row: fakeRow{in: 0.5, out: 1.5}} // $/1K tokens
	cp := NewCustomPricing(db, time.Hour, slog.Default())
	c, ok := cp.Cost(context.Background(), "org-1", "acme-model", 2000, 1000)
	if !ok {
		t.Fatal("want ok=true")
	}
	want := 2000*(0.5/1000) + 1000*(1.5/1000) // = 1.0 + 1.5 = 2.5
	if c < want-1e-9 || c > want+1e-9 {
		t.Fatalf("want %v, got %v", want, c)
	}
}

func TestCustomPricing_CachesPositiveAndNegative(t *testing.T) {
	db := &fakeDB{row: fakeRow{in: 1, out: 1}}
	cp := NewCustomPricing(db, time.Hour, slog.Default())
	cp.Cost(context.Background(), "org-1", "m", 10, 10)
	cp.Cost(context.Background(), "org-1", "m", 10, 10)
	if db.calls != 1 {
		t.Fatalf("positive result must be cached, got %d queries", db.calls)
	}

	// Not-found is cached too (Django cached {"not_found": True} for 24h).
	db2 := &fakeDB{row: fakeRow{err: pgx.ErrNoRows}}
	cp2 := NewCustomPricing(db2, time.Hour, slog.Default())
	if _, ok := cp2.Cost(context.Background(), "org-2", "ghost", 10, 10); ok {
		t.Fatal("missing custom model must return ok=false")
	}
	cp2.Cost(context.Background(), "org-2", "ghost", 10, 10)
	if db2.calls != 1 {
		t.Fatalf("negative result must be cached, got %d queries", db2.calls)
	}
}

func TestCustomPricing_DBErrorCachedBriefly(t *testing.T) {
	db := &fakeDB{row: fakeRow{err: errors.New("conn refused")}}
	cp := NewCustomPricing(db, time.Hour, slog.Default())
	if _, ok := cp.Cost(context.Background(), "org-3", "m", 10, 10); ok {
		t.Fatal("db error must yield ok=false")
	}
	// Second immediate call must be served from the short error-TTL cache
	// entry, not re-query the DB.
	if _, ok := cp.Cost(context.Background(), "org-3", "m", 10, 10); ok {
		t.Fatal("db error must yield ok=false")
	}
	if db.calls != 1 {
		t.Fatalf("transient error must be cached for errTTL, got %d queries", db.calls)
	}

	// Age the cached error entry past errTTL (in-package access to cp.cache)
	// and confirm the next call re-queries rather than serving the stale
	// error entry forever.
	key := "org-3" + "\x00" + "m"
	cp.mu.Lock()
	e := cp.cache[key]
	e.expires = time.Now().Add(-time.Second)
	cp.cache[key] = e
	cp.mu.Unlock()

	if _, ok := cp.Cost(context.Background(), "org-3", "m", 10, 10); ok {
		t.Fatal("db error must yield ok=false")
	}
	if db.calls != 2 {
		t.Fatalf("expired error entry must trigger a re-query, got %d queries", db.calls)
	}
}
