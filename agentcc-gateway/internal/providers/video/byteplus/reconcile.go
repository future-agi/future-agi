package byteplus

import (
	"context"
	"net/url"
	"strconv"
	"sync"
	"time"

	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
)

type listLimiter struct {
	mu   sync.Mutex
	next time.Time
}

func (l *listLimiter) wait(ctx context.Context) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	if delay := time.Until(l.next); delay > 0 {
		timer := time.NewTimer(delay)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-timer.C:
		}
	}
	if e := ctx.Err(); e != nil {
		return e
	}
	l.next = time.Now().Add(time.Second)
	return nil
}
func (a *Adapter) Reconcile(ctx context.Context, j *video.JobView, c video.Correlation) (video.ReconcileResult, error) {
	unresolved := video.ReconcileResult{}
	if e := a.checkCorrelation(ctx, j, c); e != nil {
		return unresolved, e
	}
	res, e := a.resolved(j)
	if e != nil {
		return unresolved, e
	}
	if j.AttemptStartedAt.IsZero() {
		return unresolved, &video.SchemaError{Field: "attempt_started_at"}
	}
	until := j.SubmitBy
	if until.IsZero() {
		until = j.AttemptStartedAt.Add(a.cfg.Provider.Deadlines.Submit)
	}
	if until.Before(j.AttemptStartedAt) {
		return unresolved, &video.SchemaError{Field: "submit_by"}
	}
	from := j.AttemptStartedAt.Add(-60 * time.Second)
	until = until.Add(60 * time.Second)
	ctx, cancel := context.WithTimeout(ctx, a.cfg.Provider.Deadlines.Reconcile)
	defer cancel()
	matches := map[string]bool{}
	seen := map[string]bool{}
	total := -1
	// A bounded complete scan with no status filter covers every terminal state.
	// Changing totals, duplicate pages or exhausting the bound remain unresolved.
	for page := 1; page <= 100; page++ {
		if e = a.limiter.wait(ctx); e != nil {
			return unresolved, e
		}
		if a.cfg.ListLimiter != nil {
			if e = a.cfg.ListLimiter(ctx, a.cfg.Provider.AccountRef); e != nil {
				return unresolved, e
			}
		}
		q := url.Values{"page_num": {strconv.Itoa(page)}, "page_size": {strconv.Itoa(a.pageSize)}, "filter.model": {res.ModelID}, "filter.service_tier": {"default"}}
		var response struct {
			Items []taskResponse `json:"items"`
			Total *int           `json:"total"`
		}
		_, _, e = a.call(ctx, "GET", "", q, nil, a.cfg.Provider.Deadlines.Read, &response)
		if e != nil {
			return unresolved, e
		}
		if response.Total == nil || *response.Total < 0 || response.Items == nil {
			return unresolved, &video.SchemaError{Field: "list.items/total"}
		}
		if total < 0 {
			total = *response.Total
		} else if total != *response.Total {
			return unresolved, nil
		}
		if total > a.pageSize*100 {
			return unresolved, nil
		}
		if len(response.Items) > a.pageSize || len(response.Items) == 0 && len(seen) < total {
			return unresolved, &video.SchemaError{Field: "list.pagination"}
		}
		for _, item := range response.Items {
			if !validID(item.ID) {
				return unresolved, &video.SchemaError{Field: "list.id"}
			}
			if seen[item.ID] {
				return unresolved, nil
			}
			seen[item.ID] = true
			if item.SafetyIdentifier != c.Token || item.Model != res.ModelID || item.CreatedAt < from.Unix() || item.CreatedAt > until.Unix() {
				continue
			}
			if _, e = normalizeState(item.Status); e != nil {
				return unresolved, e
			}
			if item.Resolution != "" && item.Resolution != res.Request.Resolution || item.Ratio != "" && item.Ratio != res.Request.AspectRatio {
				continue
			}
			if frames, ok := res.Request.ProviderOptions["frames"].(int64); ok {
				if item.Frames != 0 && int64(item.Frames) != frames {
					continue
				}
			} else if item.Duration > 0 && res.Request.DurationSeconds != -1 && float64(item.Duration) != res.Request.DurationSeconds {
				continue
			}
			if seed, ok := res.Request.ProviderOptions["seed"].(int64); ok && seed != -1 && item.Seed != nil && *item.Seed != seed {
				continue
			}
			matches[item.ID] = true
		}
		if len(seen) > total {
			return unresolved, &video.SchemaError{Field: "list.total"}
		}
		if len(seen) == total {
			if len(matches) == 1 {
				for id := range matches {
					return video.ReconcileResult{Found: true, ProviderJobID: id}, nil
				}
			}
			return unresolved, nil
		}
	}
	return unresolved, nil
}
