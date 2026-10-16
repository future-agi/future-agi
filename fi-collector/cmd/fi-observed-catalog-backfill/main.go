// Command fi-observed-catalog-backfill repairs the observed suggestion index.
// Source access is SELECT-only. Apply publishes through the live Kafka contract,
// or with FI_OBSERVED_CATALOG_MODE=direct writes the index with the collector's
// sink; it never writes spans, changes a catalog version, or switches a reader.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type options struct {
	project, mode, checkpointPath string
	since, until                  time.Time
	pageSize, maxPages            int
	delay                         time.Duration
	apply                         bool
	source                        *sourceReader
	kafka                         observedcatalog.KafkaConfig
	index                         observedcatalog.ClickHouseConfig // direct mode
	limits                        observedcatalog.Limits
	pgDSN                         string
	legacyEpoch                   uint
	legacyRevision                uint64
	legacyBuild                   string
	verifiedLegacy                bool
	replace, allowEmpty           bool
	catalog                       observedcatalog.ClickHouseConfig // --replace: the index to purge
	purger                        projectPurger
	now                           func() time.Time
}

// projectPurger deletes every catalog row of one project.
type projectPurger interface {
	DeleteProject(context.Context, observedcatalog.Scope) error
}

func (c options) clock() time.Time {
	if c.now != nil {
		return c.now()
	}
	return time.Now()
}

func parseOptions(args []string, getenv func(string) string) (options, error) {
	var cfg options
	var since, until string
	flags := flag.NewFlagSet("fi-observed-catalog-backfill", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	flags.StringVar(&cfg.project, "project", "", "one current project UUID; ownership is verified in PostgreSQL")
	flags.StringVar(&cfg.mode, "source", "spans", "source format")
	flags.UintVar(&cfg.legacyEpoch, "legacy-epoch", 0, "exact historical input epoch (import only)")
	flags.Uint64Var(&cfg.legacyRevision, "legacy-revision", 0, "exact historical input revision (import only)")
	flags.StringVar(&cfg.legacyBuild, "legacy-build", "", "exact historical input build UUID (import only)")
	flags.BoolVar(&cfg.verifiedLegacy, "verified-legacy", false, "confirm this legacy batch was verified and contains no erased observations")
	flags.StringVar(&since, "since", "", "inclusive RFC3339 observation time")
	flags.StringVar(&until, "until", "", "exclusive RFC3339 observation time")
	flags.BoolVar(&cfg.apply, "apply", false, "publish observations to Kafka, or the index in direct mode (default: read-only preview)")
	flags.BoolVar(&cfg.replace, "replace", false, "spans only: delete the project's catalog rows, then rebuild them from stored spans (requires --apply)")
	flags.BoolVar(&cfg.allowEmpty, "allow-empty-rebuild", false, "with --replace: proceed when the project has no stored span in the range (its spans have all expired)")
	flags.StringVar(&cfg.checkpointPath, "checkpoint", "", "local resumable progress file, required with --apply")
	flags.IntVar(&cfg.pageSize, "page-size", 64, "source identities per bounded page, 1..256")
	flags.IntVar(&cfg.maxPages, "max-pages", 100, "maximum pages per invocation; resume with the same checkpoint")
	flags.DurationVar(&cfg.delay, "page-delay", 100*time.Millisecond, "minimum pause between pages to yield to live traffic")
	if err := flags.Parse(args); err != nil {
		return cfg, err
	}
	if flags.NArg() != 0 {
		return cfg, errors.New("unexpected positional arguments")
	}
	id, err := uuid.Parse(cfg.project)
	if err != nil || id == uuid.Nil || id.String() != cfg.project {
		return cfg, errors.New("--project must be a canonical nonzero UUID")
	}
	if cfg.replace && !cfg.apply {
		return cfg, errors.New("--replace deletes catalog rows and requires --apply")
	}
	if cfg.allowEmpty && !cfg.replace {
		return cfg, errors.New("--allow-empty-rebuild only applies with --replace")
	}
	switch cfg.mode {
	case "spans":
		cfg.since, err = time.Parse(time.RFC3339Nano, since)
		if err != nil {
			return cfg, errors.New("--since must be RFC3339")
		}
		cfg.until, err = time.Parse(time.RFC3339Nano, until)
		if err != nil || !cfg.since.Before(cfg.until) {
			return cfg, errors.New("--until must be RFC3339 and greater than --since")
		}
		cfg.since, cfg.until = cfg.since.UTC(), cfg.until.UTC()
		if cfg.legacyEpoch != 0 || cfg.legacyRevision != 0 || cfg.legacyBuild != "" || cfg.verifiedLegacy {
			return cfg, errors.New("legacy flags require --source legacy")
		}
	case "legacy":
		if cfg.replace {
			return cfg, errors.New("--replace rebuilds from spans and cannot be used with --source legacy")
		}
		build, err := uuid.Parse(cfg.legacyBuild)
		if cfg.legacyEpoch == 0 || cfg.legacyEpoch > 65535 || cfg.legacyRevision == 0 || err != nil || build == uuid.Nil || build.String() != cfg.legacyBuild {
			return cfg, errors.New("legacy import requires the exact nonzero input epoch, revision and build UUID")
		}
		if since != "" || until != "" {
			return cfg, errors.New("legacy import selects a verified batch, not a source time range")
		}
		if cfg.apply && !cfg.verifiedLegacy {
			return cfg, errors.New("legacy apply requires --verified-legacy after preview and source verification")
		}
		cfg.since = time.Unix(0, 0).UTC()
	default:
		return cfg, errors.New("--source must be spans or legacy")
	}
	if cfg.pageSize < 1 || cfg.pageSize > 256 || cfg.maxPages < 1 || cfg.delay < 0 || cfg.delay > time.Minute {
		return cfg, errors.New("invalid page bounds/delay")
	}
	if cfg.apply && cfg.checkpointPath == "" {
		return cfg, errors.New("--apply requires --checkpoint")
	}
	cfg.source, err = newSourceReader(getenv("FI_OBSERVED_BACKFILL_CH_URL"), getenv("FI_OBSERVED_BACKFILL_CH_DATABASE"), getenv("FI_OBSERVED_BACKFILL_CH_USERNAME"), getenv("FI_OBSERVED_BACKFILL_CH_PASSWORD"))
	if err != nil {
		return cfg, err
	}
	cfg.pgDSN = getenv("FI_PG_DSN")
	if cfg.pgDSN == "" {
		return cfg, errors.New("FI_PG_DSN is required for current project ownership verification")
	}
	if cfg.apply {
		// The collector's own mode: Kafka where a consumer writes the index,
		// the index writer itself where the collector does (direct).
		switch getenv("FI_OBSERVED_CATALOG_MODE") {
		case "", "kafka":
			cfg.kafka, err = observedcatalog.KafkaConfigFromEnv(getenv)
		case "direct":
			cfg.index, err = observedcatalog.ClickHouseConfigFromEnv(getenv)
			if err == nil {
				_, err = observedcatalog.NewClickHouseSink(cfg.index)
			}
		default:
			err = errors.New("--apply requires FI_OBSERVED_CATALOG_MODE kafka or direct")
		}
		if err != nil {
			return cfg, err
		}
	}
	if cfg.replace {
		// Kafka mode holds no ClickHouse connection to the index, and the delete
		// has to reach it directly.
		cfg.catalog, err = observedcatalog.ClickHouseConfigFromEnv(getenv)
		if err != nil {
			return cfg, err
		}
		if cfg.catalog.URL == "" || cfg.catalog.Database == "" {
			return cfg, errors.New("--replace requires FI_OBSERVED_CATALOG_CH_URL and FI_OBSERVED_CATALOG_CH_DATABASE to reach the index")
		}
		if _, err := observedcatalog.NewClickHouseSink(cfg.catalog); err != nil {
			return cfg, err
		}
	}
	cfg.limits, err = observedcatalog.LimitsFromEnv(getenv)
	if err != nil {
		return cfg, err
	}
	return cfg, nil
}

type scopeReader interface {
	Scope(context.Context, string) (observedcatalog.Scope, error)
	// Holding reports whether the project is marked as holding simulated traces.
	Holding(context.Context, string) (bool, error)
}
type postgresScope struct{ conn *pgx.Conn }

func newPostgresScope(ctx context.Context, dsn string) (*postgresScope, error) {
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		return nil, errors.New("invalid PostgreSQL source configuration")
	}
	cfg.RuntimeParams["default_transaction_read_only"] = "on"
	cfg.RuntimeParams["statement_timeout"] = "10000"
	cfg.ConnectTimeout = 10 * time.Second
	conn, err := pgx.ConnectConfig(ctx, cfg)
	if err != nil {
		return nil, errors.New("PostgreSQL ownership connection failed")
	}
	return &postgresScope{conn}, nil
}

func (p *postgresScope) Scope(ctx context.Context, project string) (observedcatalog.Scope, error) {
	call, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	var scope observedcatalog.Scope
	err := p.conn.QueryRow(call, `SELECT p.organization_id::text, p.workspace_id::text, p.id::text
 FROM tracer_project p
 JOIN accounts_workspace w ON w.id = p.workspace_id AND w.organization_id = p.organization_id
 JOIN accounts_organization o ON o.id = p.organization_id
 WHERE p.id = $1::uuid AND NOT p.deleted AND NOT w.deleted AND w.is_active`, project).
		Scan(&scope.OrganizationID, &scope.WorkspaceID, &scope.ProjectID)
	if err != nil {
		return scope, errors.New("project ownership unavailable, deleted or projectless; no observations published")
	}
	return scope, nil
}

// The mark follows the project, so it is read with no soft-delete or workspace filter.
func (p *postgresScope) Holding(ctx context.Context, project string) (bool, error) {
	call, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	var holding bool
	err := p.conn.QueryRow(call, `SELECT EXISTS (SELECT 1 FROM tracer_observability_provider
 WHERE project_id = $1::uuid AND metadata @> '{"holds_simulated": true}'::jsonb)`, project).Scan(&holding)
	if err != nil {
		return false, errors.New("simulated-trace marker unavailable")
	}
	return holding, nil
}

func scanBinding(cfg options, scope observedcatalog.Scope) string {
	// Never persist credentials. Include the destination to prevent resuming a
	// different Kafka cluster/topic with already-published progress.
	// The leading element versions the binding. It is bumped to 2 because spans
	// are now replayed newest-hour-first: a checkpoint written by the ascending
	// scan records a completely different meaning for the same Hour field.
	binding := []any{2, cfg.mode, cfg.source.url, cfg.source.database, scope, cfg.since, cfg.until, cfg.kafka.Brokers, cfg.kafka.Topic, cfg.legacyEpoch, cfg.legacyRevision, cfg.legacyBuild, cfg.limits}
	if cfg.index.URL != "" {
		// Direct writes bind the index instead; Kafka bindings are unchanged.
		binding = append(binding, "direct", cfg.index.URL, cfg.index.Database)
	}
	if cfg.replace {
		// A purge-and-rebuild checkpoint means a different thing than a plain
		// one: it records that rows were deleted from this index.
		binding = append(binding, "replace", cfg.catalog.URL, cfg.catalog.Database)
	}
	data, _ := json.Marshal(binding)
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:])
}

// Apply the live writer's eligibility policy; unknown failures stay fatal.
func extractionPolicyExclusion(report observedcatalog.Report) (uint64, error) {
	if report.Complete && len(report.GapReasons) == 0 {
		return 0, nil
	}
	if report.PolicyExclusion() {
		return 1, nil // affected spans, not the number of omitted values
	}
	return 0, fmt.Errorf("source page has extraction gaps %v; no checkpoint advanced", report.GapReasons)
}

func buildPage(rows []map[string]any, scope observedcatalog.Scope, since, until time.Time, limits observedcatalog.Limits) (observedcatalog.Batch, pageReport, error) {
	var batches []observedcatalog.Batch
	var result pageReport
	for _, row := range rows {
		if fmt.Sprint(row["is_deleted"]) == "1" {
			continue
		}
		if fmt.Sprint(row["is_deleted"]) != "0" {
			return observedcatalog.Batch{}, pageReport{}, errors.New("invalid source tombstone")
		}
		seen, err := time.Parse(observedcatalog.TimeLayout, fmt.Sprint(row["start_time"]))
		if err != nil {
			return observedcatalog.Batch{}, pageReport{}, errors.New("invalid source timestamp")
		}
		if seen.Before(since) || !seen.Before(until) {
			continue
		}
		if row["project_id"] != scope.ProjectID {
			return observedcatalog.Batch{}, pageReport{}, errors.New("source project does not match authorized project")
		}
		org, ok := row["org_id"].(string)
		if !ok || org != scope.OrganizationID {
			key, err := rowKey(row)
			if err != nil {
				return observedcatalog.Batch{}, pageReport{}, err
			}
			result.Quarantined = append(result.Quarantined, quarantinedSpan{
				Project: scope.ProjectID, Seen: seen.Format(observedcatalog.TimeLayout), Key: key,
				Reason: "source_organization_mismatch_or_missing",
			})
			continue
		}
		// Every row that reaches the extractor must carry the flag: a missing one
		// is a changed query, and reading it as unmarked would put simulated keys back.
		simulated := fmt.Sprint(row[simulatedResourceColumn])
		if simulated != "0" && simulated != "1" {
			return observedcatalog.Batch{}, pageReport{}, errors.New("invalid source simulation flag")
		}
		canonical, err := canonicalRow(row)
		if err != nil {
			return observedcatalog.Batch{}, pageReport{}, err
		}
		if simulated == "1" {
			// The stored resource column is not read back whole; hand the
			// extractor the mark so one code path validates and skips the row.
			canonical["resource_attrs"] = map[string]any{observedcatalog.SimulationOriginKey: true}
		}
		batch, report, err := observedcatalog.Extract(observedcatalog.ScopedSpan{
			OrganizationID: scope.OrganizationID, WorkspaceID: scope.WorkspaceID, Row: canonical,
		}, limits)
		if err != nil {
			return observedcatalog.Batch{}, pageReport{}, err
		}
		excluded, err := extractionPolicyExclusion(report)
		if err != nil {
			return observedcatalog.Batch{}, pageReport{}, err
		}
		result.PolicyExclusions += excluded
		batches = append(batches, batch)
	}
	return observedcatalog.Merge(batches...), result, nil
}

// JSONEachRow represents typed ClickHouse Maps as objects. Rehydrate their Go
// types, but leave JSON overflow to the shared extractor/codec. No independent
// projection, key selection or number/string conversion is implemented here.
func canonicalRow(row map[string]any) (map[string]any, error) {
	data, err := json.Marshal(row)
	if err != nil {
		return nil, errors.New("invalid source JSON")
	}
	var maps struct {
		Strings  map[string]*string  `json:"attrs_string"`
		Numbers  map[string]*float64 `json:"attrs_number"`
		Booleans map[string]*uint8   `json:"attrs_bool"`
	}
	if err := json.Unmarshal(data, &maps); err != nil || maps.Strings == nil || maps.Numbers == nil || maps.Booleans == nil {
		return nil, errors.New("invalid source typed maps")
	}
	result := make(map[string]any, len(row))
	for key, value := range row {
		result[key] = value
	}
	stringsMap, err := nonnullMap(maps.Strings)
	if err != nil {
		return nil, err
	}
	numbersMap, err := nonnullMap(maps.Numbers)
	if err != nil {
		return nil, err
	}
	booleansMap, err := nonnullMap(maps.Booleans)
	if err != nil {
		return nil, err
	}
	result["attrs_string"], result["attrs_number"], result["attrs_bool"] = stringsMap, numbersMap, booleansMap
	return result, nil
}

func nonnullMap[T any](input map[string]*T) (map[string]T, error) {
	result := make(map[string]T, len(input))
	for key, value := range input {
		// JSONEachRow represents non-finite Float64 as null. Decoding directly
		// to float64 would fabricate zero instead of rejecting invalid data.
		if value == nil {
			return nil, errors.New("source typed map contains null or a non-finite value")
		}
		result[key] = *value
	}
	return result, nil
}

// Replay newest hours first so recent suggestions arrive before older history.
// Scan progress is a checkpoint/receipt concern, not a picker-page verdict.
func lastHour(until time.Time) time.Time {
	bucket := until.Truncate(time.Hour)
	// --until is exclusive, so a value exactly on the hour selects no rows in
	// its own bucket.
	if bucket.Equal(until) {
		bucket = bucket.Add(-time.Hour)
	}
	return bucket
}

const (
	// Spans still arriving, and client clocks that run ahead, can start after
	// now; the rebuild reads nothing at or after --until.
	replaceMinUntilLead = time.Hour
	// A far-future --until only scans empty hours while the catalog sits emptied.
	replaceMaxUntilLead = 24 * time.Hour
)

// The delete removes all of a project's history and the rebuild covers only
// [since, until), so the range must hold every stored span. Three bounded
// existence reads decide it; the answer says whether any span is in range.
func checkReplaceRange(ctx context.Context, cfg options) (bool, error) {
	now := cfg.clock()
	if cfg.until.Before(now.Add(replaceMinUntilLead)) {
		return false, fmt.Errorf("--replace needs --until at least %s after the current time; spans that start after --until would lose their catalog rows", replaceMinUntilLead)
	}
	if cfg.until.After(now.Add(replaceMaxUntilLead)) {
		return false, fmt.Errorf("--replace needs --until at most %s after the current time; the catalog stays empty while the scan reads empty hours", replaceMaxUntilLead)
	}
	before, err := cfg.source.spanBefore(ctx, cfg.project, cfg.since)
	if err != nil {
		return false, err
	}
	if before {
		return false, errors.New("--replace needs --since at or before the project's oldest stored span; spans before --since would lose their catalog rows")
	}
	after, err := cfg.source.spanNotBefore(ctx, cfg.project, cfg.until)
	if err != nil {
		return false, err
	}
	if after {
		return false, errors.New("--replace needs --until after the project's newest stored span; spans at or after --until would lose their catalog rows")
	}
	within, err := cfg.source.spanBetween(ctx, cfg.project, cfg.since, cfg.until)
	if err != nil {
		return false, err
	}
	if !within && !cfg.allowEmpty {
		return false, errors.New("--replace found no stored span of the project between --since and --until; check the source database, or pass --allow-empty-rebuild if its spans have all expired")
	}
	return within, nil
}

// purgeProject runs once per checkpoint, before the first page is published. The
// purge is recorded right after the deletes; a crash in between repeats the
// deletes on retry, which is harmless because nothing has been published yet.
func purgeProject(ctx context.Context, cfg options, scopes scopeReader, scope observedcatalog.Scope, progress *checkpoint, output io.Writer) error {
	if progress.Pages != 0 || progress.Complete {
		return errors.New("checkpoint has scan progress but no recorded purge; start a new checkpoint")
	}
	if cfg.purger == nil {
		return errors.New("--replace has no index connection to purge")
	}
	hasSpans, err := checkReplaceRange(ctx, cfg)
	if err != nil {
		return err
	}
	current, err := scopes.Scope(ctx, cfg.project)
	if err != nil || current != scope {
		return errors.New("project ownership changed before the purge; nothing deleted")
	}
	if err := cfg.purger.DeleteProject(ctx, scope); err != nil {
		return fmt.Errorf("purging the project's catalog rows failed, nothing published; repeat the same command: %w", err)
	}
	progress.Purged = true
	if err := saveCheckpoint(cfg.checkpointPath, *progress); err != nil {
		return err
	}
	return json.NewEncoder(output).Encode(map[string]any{"catalog_purged": true, "project_has_spans": hasSpans})
}

func runSpans(ctx context.Context, cfg options, scopes scopeReader, publisher observedcatalog.Publisher, output io.Writer) error {
	if cfg.replace && !cfg.apply {
		return errors.New("--replace deletes catalog rows and requires --apply")
	}
	scope, err := scopes.Scope(ctx, cfg.project)
	if err != nil {
		return err
	}
	progress := checkpoint{Binding: scanBinding(cfg, scope), Hour: lastHour(cfg.until)}
	if cfg.apply {
		lock, err := lockCheckpoint(cfg.checkpointPath)
		if err != nil {
			return err
		}
		defer lock.Close()
		// Seed a fresh checkpoint at the newest bucket that can hold rows, not at
		// --until itself: an --until exactly on the hour selects no rows in its own
		// bucket, and seeding there puts the scan outside its own bounds.
		progress, err = loadCheckpoint(cfg.checkpointPath, progress.Binding, lastHour(cfg.until))
		if err != nil {
			return err
		}
		if progress.Hour.Before(cfg.since.Truncate(time.Hour).Add(-time.Hour)) || progress.Hour.After(lastHour(cfg.until)) {
			return errors.New("checkpoint hour is outside requested scan")
		}
		if cfg.replace && !progress.Purged {
			if err := purgeProject(ctx, cfg, scopes, scope, &progress, output); err != nil {
				return err
			}
		}
	}
	encoder := json.NewEncoder(output)
	for pages := 0; pages < cfg.maxPages && !progress.Complete; pages++ {
		if err := ctx.Err(); err != nil {
			return err
		}
		keys, err := cfg.source.identityPage(ctx, cfg.project, progress.Hour, progress.After, cfg.pageSize)
		if err != nil {
			return err
		}
		rows, err := cfg.source.payloadPage(ctx, cfg.project, progress.Hour, keys)
		if err != nil {
			return err
		}
		batch, report, err := buildPage(rows, scope, cfg.since, cfg.until, cfg.limits)
		if err != nil {
			return err
		}
		if report.PolicyExclusions > ^uint64(0)-progress.PolicyExclusionSpans || uint64(len(report.Quarantined)) > ^uint64(0)-progress.QuarantinedSpans {
			return errors.New("checkpoint policy exclusion count would overflow; page not published")
		}
		current, err := scopes.Scope(ctx, cfg.project)
		if err != nil || current != scope {
			return errors.New("project ownership changed during scan; page not published")
		}
		if cfg.apply {
			if err := recordQuarantine(cfg.checkpointPath+".quarantine.jsonl", report.Quarantined); err != nil {
				return fmt.Errorf("record rejected source identities: %w", err)
			}
		}
		if cfg.apply && !batch.Empty() {
			if err := publisher.Publish(ctx, batch); err != nil {
				return err
			}
		}
		progress.Pages++
		progress.Rows += uint64(len(rows))
		progress.PolicyExclusionSpans += report.PolicyExclusions
		progress.QuarantinedSpans += uint64(len(report.Quarantined))
		if len(keys) < cfg.pageSize {
			progress.Hour = progress.Hour.Add(-time.Hour)
			progress.After = physicalKey{}
		} else {
			progress.After = keys[len(keys)-1]
		}
		progress.Complete = progress.Hour.Before(cfg.since.Truncate(time.Hour))
		if cfg.apply {
			if err := saveCheckpoint(cfg.checkpointPath, progress); err != nil {
				return err
			}
		}
		receipt := map[string]any{"preview": !cfg.apply, "source_rows": len(rows), "keys": len(batch.Keys), "values": len(batch.Values), "scan_complete": progress.Complete, "pages_read": progress.Pages, "consumer_visibility_verified": false, "policy_exclusion_spans": report.PolicyExclusions, "quarantined_spans": len(report.Quarantined)}
		if cfg.apply {
			receipt["recorded_policy_exclusion_spans"] = progress.PolicyExclusionSpans
			receipt["recorded_quarantined_spans"] = progress.QuarantinedSpans
		}
		if err := encoder.Encode(receipt); err != nil {
			return err
		}
		if !progress.Complete && cfg.delay > 0 {
			timer := time.NewTimer(cfg.delay)
			select {
			case <-ctx.Done():
				timer.Stop()
				return ctx.Err()
			case <-timer.C:
			}
		}
	}
	if !progress.Complete {
		if !cfg.apply {
			// A preview keeps no checkpoint, so running it again starts over.
			hours := int(lastHour(cfg.until).Sub(cfg.since.Truncate(time.Hour))/time.Hour) + 1
			return fmt.Errorf("page budget reached (--max-pages %d); preview incomplete, and a preview keeps no checkpoint to resume: "+
				"raise --max-pages (this range has %d hours, each at least one page) or narrow --since/--until", cfg.maxPages, hours)
		}
		return errors.New("page budget reached; scan incomplete, resume the same checkpoint")
	}
	return nil
}

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	err := run(ctx, os.Args[1:], os.Getenv, os.Stdout)
	if errors.Is(err, flag.ErrHelp) {
		fmt.Fprint(os.Stdout, `Repair observed attributes using SELECT-only source access, publishing to Kafka
or, with FI_OBSERVED_CATALOG_MODE=direct, writing the index directly.

Preview spans:
  fi-observed-catalog-backfill --project UUID --since RFC3339 --until RFC3339
Apply/resume: add --apply --checkpoint /writable/progress.json
Bounds: --page-size 64 --max-pages 100 --page-delay 100ms
Every hour of the range takes at least one page. A preview keeps no checkpoint:
raise --max-pages to at least the range's hours or narrow it.

Replace a project's catalog instead of adding to it (spans only):
  fi-observed-catalog-backfill --project UUID --since RFC3339 --until RFC3339 \
    --apply --replace --checkpoint /writable/progress.json
Deletes every catalog row of the project once, then rebuilds from stored spans,
which leaves out spans marked as simulated. The project's catalog is incomplete
until the scan finishes: resume the same checkpoint, which never deletes again.
Keys that only expired spans or a legacy import supplied are not rebuilt. Rows
stored under the project's previous workspace are left in place.
The delete needs the index connection (FI_OBSERVED_CATALOG_CH_URL and CH_DATABASE),
also in Kafka mode, as a ClickHouse user with ALTER DELETE on the two catalog
tables. Use a user separate from the live writer, which keeps SELECT and INSERT only.
Before deleting, the run is refused unless no stored span of the project is before
--since or at/after --until, at least one is in between, and --until is 1h to 24h
after now. For a project whose spans have all expired, add --allow-empty-rebuild.
Run it once the collector that skips simulated spans is deployed and the consumer
has no backlog, so no older message re-adds rows. A delete that times out may have
applied: repeat the same command.

Preview a verified historical batch instead:
  --source legacy --project UUID --legacy-epoch N --legacy-revision N --legacy-build UUID
Apply additionally requires --verified-legacy; omit --since and --until.
The import is refused for a project marked as holding simulated traces, because
legacy rows carry no simulation marks; rebuild that project with --replace.

Source credentials (read-only): FI_PG_DSN and FI_OBSERVED_BACKFILL_CH_URL,
FI_OBSERVED_BACKFILL_CH_DATABASE, FI_OBSERVED_BACKFILL_CH_USERNAME,
FI_OBSERVED_BACKFILL_CH_PASSWORD. Apply uses FI_OBSERVED_CATALOG_KAFKA_BROKERS
and optional KAFKA_TOPIC/KAFKA_GROUP with the same FI_OBSERVED_CATALOG_ prefix.
With FI_OBSERVED_CATALOG_MODE=direct (installs without Kafka) apply writes the
index with FI_OBSERVED_CATALOG_CH_URL, CH_DATABASE, CH_USERNAME and CH_PASSWORD.
Live and backfill share FI_OBSERVED_CATALOG_MAX_KEYS_PER_SPAN and
FI_OBSERVED_CATALOG_MAX_ARRAY_MEMBERS_PER_SPAN.
Extraction policy limits omit ineligible suggestions, retaining eligible siblings.
Operator progress counts affected spans; malformed source and unknown failures
remain fatal. Increasing limits requires a new overlapping repair checkpoint.
Rows with missing or conflicting source organization are never published.
Apply records their identities in CHECKPOINT.quarantine.jsonl before advancing;
the checkpoint and page receipts count them separately from indexed spans.

Progress proves Kafka (or direct index write) acknowledgement, not consumer
visibility or complete source history. Re-run overlapping source ranges to
repair late-arriving spans.
`)
		return
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "observed catalog backfill:", err)
		os.Exit(1)
	}
}

var errHoldingProject = errors.New("project holds simulated traces; the legacy catalog carries no simulation marks, so nothing was published; rebuild this project's catalog from spans with --replace")

func runLegacy(ctx context.Context, cfg options, scopes scopeReader, publisher observedcatalog.Publisher, output io.Writer) error {
	scope, err := scopes.Scope(ctx, cfg.project)
	if err != nil {
		return err
	}
	// Asked in preview too, so a preview never promises an import that apply
	// would refuse. A failed read is a refusal, never "not holding".
	holding, err := scopes.Holding(ctx, cfg.project)
	if err != nil {
		return fmt.Errorf("could not check whether the project holds simulated traces; nothing published: %w", err)
	}
	if holding {
		return errHoldingProject
	}
	selection := legacySelection{Scope: scope, Epoch: uint16(cfg.legacyEpoch), Revision: cfg.legacyRevision, BuildToken: cfg.legacyBuild}
	progress := checkpoint{Binding: scanBinding(cfg, scope), Hour: cfg.since}
	if cfg.apply {
		lock, err := lockCheckpoint(cfg.checkpointPath)
		if err != nil {
			return err
		}
		defer lock.Close()
		progress, err = loadCheckpoint(cfg.checkpointPath, progress.Binding, cfg.since)
		if err != nil {
			return err
		}
	}
	encoder := json.NewEncoder(output)
	for pages := 0; pages < cfg.maxPages && !progress.Complete; pages++ {
		batch, next, err := importLegacyPage(ctx, cfg.source, selection, progress.Legacy, cfg.pageSize)
		if err != nil {
			return err
		}
		current, err := scopes.Scope(ctx, cfg.project)
		if err != nil || current != scope {
			return errors.New("project ownership changed during legacy import; page not published")
		}
		holding, err := scopes.Holding(ctx, cfg.project)
		if err != nil {
			return fmt.Errorf("could not check whether the project holds simulated traces; page not published: %w", err)
		}
		if holding {
			return fmt.Errorf("%w (it started holding during this import; page not published)", errHoldingProject)
		}
		if cfg.apply && !batch.Empty() {
			if err := publisher.Publish(ctx, batch); err != nil {
				return err
			}
		}
		progress.Legacy, progress.Complete = next, next.Done
		progress.Pages++
		if cfg.apply {
			if err := saveCheckpoint(cfg.checkpointPath, progress); err != nil {
				return err
			}
		}
		if err := encoder.Encode(map[string]any{"preview": !cfg.apply, "keys": len(batch.Keys), "values": len(batch.Values), "scan_complete": progress.Complete, "pages_read": progress.Pages, "consumer_visibility_verified": false}); err != nil {
			return err
		}
		if !progress.Complete && cfg.delay > 0 {
			timer := time.NewTimer(cfg.delay)
			select {
			case <-ctx.Done():
				timer.Stop()
				return ctx.Err()
			case <-timer.C:
			}
		}
	}
	if !progress.Complete {
		if !cfg.apply {
			return fmt.Errorf("page budget reached (--max-pages %d); legacy preview incomplete, and a preview keeps no checkpoint to resume: raise --max-pages", cfg.maxPages)
		}
		return errors.New("page budget reached; legacy import incomplete, resume the same checkpoint")
	}
	return nil
}

// newPurger deletes from the catalog index, never from the span source.
func newPurger(cfg options) (projectPurger, error) {
	sink, err := observedcatalog.NewClickHouseSink(cfg.catalog)
	if err != nil {
		return nil, err
	}
	return sink, nil
}

func run(ctx context.Context, args []string, getenv func(string) string, output io.Writer) error {
	cfg, err := parseOptions(args, getenv)
	if err != nil {
		return err
	}
	owner, err := newPostgresScope(ctx, cfg.pgDSN)
	if err != nil {
		return err
	}
	defer owner.conn.Close(context.Background())
	if cfg.replace {
		cfg.purger, err = newPurger(cfg)
		if err != nil {
			return err
		}
	}
	var publisher observedcatalog.Publisher
	if cfg.apply && cfg.index.URL != "" {
		publisher, err = observedcatalog.NewClickHouseSink(cfg.index)
		if err != nil {
			return err
		}
	} else if cfg.apply {
		producer, err := observedcatalog.NewProducer(cfg.kafka)
		if err != nil {
			return err
		}
		defer producer.Close()
		publisher = producer
	}
	if cfg.mode == "legacy" {
		return runLegacy(ctx, cfg, owner, publisher, output)
	}
	return runSpans(ctx, cfg, owner, publisher, output)
}
