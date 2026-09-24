package catalogwriter

import (
	"sort"

	"github.com/google/uuid"
)

// StagedProjectJob is one project-scoped catalog drain unit. Kafka ordering is
// project/epoch/stream scoped, so one transport envelope must not mix tenants.
type StagedProjectJob struct {
	Job    Job
	Report StageReport
}

// StageCanonicalSpansByProject splits an already-canonical drain into stable
// project groups before staging. Invalid/unscoped rows are retained together
// as one explicit gap job; they are never attached to a valid tenant. The
// whole input bound is checked before grouping, so many projects cannot bypass
// MaxJobInputSpans.
func (w *Writer) StageCanonicalSpansByProject(rows []map[string]any) []StagedProjectJob {
	if w == nil || !w.Enabled() || len(rows) == 0 {
		return nil
	}
	if len(rows) > w.cfg.MaxJobInputSpans {
		job, report := w.unscopedGapJob(len(rows), "input_span_limit")
		return []StagedProjectJob{{Job: job, Report: report}}
	}

	groups := make(map[string][]map[string]any)
	unscoped := make([]map[string]any, 0)
	for _, row := range rows {
		projectID, ok := row["project_id"].(string)
		parsed, err := uuid.Parse(projectID)
		if !ok || err != nil || parsed == uuid.Nil || parsed.String() != projectID {
			unscoped = append(unscoped, row)
			continue
		}
		groups[projectID] = append(groups[projectID], row)
	}
	projectIDs := make([]string, 0, len(groups))
	for projectID := range groups {
		projectIDs = append(projectIDs, projectID)
	}
	sort.Strings(projectIDs)

	staged := make([]StagedProjectJob, 0, len(projectIDs)+1)
	for _, projectID := range projectIDs {
		job, report := w.StageCanonicalSpans(groups[projectID])
		staged = append(staged, StagedProjectJob{Job: job, Report: report})
	}
	if len(unscoped) != 0 {
		job, report := w.StageCanonicalSpans(unscoped)
		staged = append(staged, StagedProjectJob{Job: job, Report: report})
	}
	return staged
}
