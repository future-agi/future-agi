// Command fi-observed-catalog-backfill repairs the observed suggestion index.
// Source access is SELECT-only. Apply publishes through the live Kafka contract;
// it never writes spans, changes a catalog version, or switches a reader.
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
	limits                        observedcatalog.Limits
	pgDSN                         string
	legacyEpoch                   uint
	legacyRevision                uint64
	legacyBuild                   string
	verifiedLegacy                bool
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
	flags.BoolVar(&cfg.apply, "apply", false, "publish observations to Kafka (default: read-only preview)")
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
		cfg.kafka, err = observedcatalog.KafkaConfigFromEnv(getenv)
		if err != nil {
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

func scanBinding(cfg options, scope observedcatalog.Scope) string {
	// Never persist credentials. Include the destination to prevent resuming a
	// different Kafka cluster/topic with already-published progress.
	data, _ := json.Marshal([]any{1, cfg.mode, cfg.source.url, cfg.source.database, scope, cfg.since, cfg.until, cfg.kafka.Brokers, cfg.kafka.Topic, cfg.legacyEpoch, cfg.legacyRevision, cfg.legacyBuild, cfg.limits})
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:])
}

func buildPage(rows []map[string]any, scope observedcatalog.Scope, since, until time.Time, limits observedcatalog.Limits) (observedcatalog.Batch, error) {
	var batches []observedcatalog.Batch
	for _, row := range rows {
		if fmt.Sprint(row["is_deleted"]) == "1" {
			continue
		}
		if fmt.Sprint(row["is_deleted"]) != "0" {
			return observedcatalog.Batch{}, errors.New("invalid source tombstone")
		}
		seen, err := time.Parse(observedcatalog.TimeLayout, fmt.Sprint(row["start_time"]))
		if err != nil {
			return observedcatalog.Batch{}, errors.New("invalid source timestamp")
		}
		if seen.Before(since) || !seen.Before(until) {
			continue
		}
		if row["project_id"] != scope.ProjectID {
			return observedcatalog.Batch{}, errors.New("source project does not match authorized project")
		}
		canonical, err := canonicalRow(row)
		if err != nil {
			return observedcatalog.Batch{}, err
		}
		batch, report, err := observedcatalog.Extract(observedcatalog.ScopedSpan{
			OrganizationID: scope.OrganizationID, WorkspaceID: scope.WorkspaceID, Row: canonical,
		}, limits)
		if err != nil {
			return observedcatalog.Batch{}, err
		}
		if !report.Complete {
			return observedcatalog.Batch{}, fmt.Errorf("source page has extraction gaps %v; no checkpoint advanced", report.GapReasons)
		}
		batches = append(batches, batch)
	}
	return observedcatalog.Merge(batches...), nil
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

func runSpans(ctx context.Context, cfg options, scopes scopeReader, publisher observedcatalog.Publisher, output io.Writer) error {
	scope, err := scopes.Scope(ctx, cfg.project)
	if err != nil {
		return err
	}
	progress := checkpoint{Binding: scanBinding(cfg, scope), Hour: cfg.since.Truncate(time.Hour)}
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
		if progress.Hour.Before(cfg.since.Truncate(time.Hour)) || progress.Hour.After(cfg.until.Truncate(time.Hour).Add(time.Hour)) {
			return errors.New("checkpoint hour is outside requested scan")
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
		batch, err := buildPage(rows, scope, cfg.since, cfg.until, cfg.limits)
		if err != nil {
			return err
		}
		current, err := scopes.Scope(ctx, cfg.project)
		if err != nil || current != scope {
			return errors.New("project ownership changed during scan; page not published")
		}
		if cfg.apply && !batch.Empty() {
			if err := publisher.Publish(ctx, batch); err != nil {
				return err
			}
		}
		progress.Pages++
		progress.Rows += uint64(len(rows))
		if len(keys) < cfg.pageSize {
			progress.Hour = progress.Hour.Add(time.Hour)
			progress.After = physicalKey{}
		} else {
			progress.After = keys[len(keys)-1]
		}
		progress.Complete = !progress.Hour.Before(cfg.until)
		if cfg.apply {
			if err := saveCheckpoint(cfg.checkpointPath, progress); err != nil {
				return err
			}
		}
		if err := encoder.Encode(map[string]any{"preview": !cfg.apply, "source_rows": len(rows), "keys": len(batch.Keys), "values": len(batch.Values), "scan_complete": progress.Complete, "pages_read": progress.Pages, "consumer_visibility_verified": false}); err != nil {
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
		return errors.New("page budget reached; scan incomplete, resume the same checkpoint")
	}
	return nil
}

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	err := run(ctx, os.Args[1:], os.Getenv, os.Stdout)
	if errors.Is(err, flag.ErrHelp) {
		fmt.Fprint(os.Stdout, `Repair observed attributes using SELECT-only source access and Kafka publication.

Preview spans:
  fi-observed-catalog-backfill --project UUID --since RFC3339 --until RFC3339
Apply/resume: add --apply --checkpoint /writable/progress.json
Bounds: --page-size 64 --max-pages 100 --page-delay 100ms

Preview a verified historical batch instead:
  --source legacy --project UUID --legacy-epoch N --legacy-revision N --legacy-build UUID
Apply additionally requires --verified-legacy; omit --since and --until.

Source credentials (read-only): FI_PG_DSN and FI_OBSERVED_BACKFILL_CH_URL,
FI_OBSERVED_BACKFILL_CH_DATABASE, FI_OBSERVED_BACKFILL_CH_USERNAME,
FI_OBSERVED_BACKFILL_CH_PASSWORD. Apply uses FI_OBSERVED_CATALOG_KAFKA_BROKERS
and optional KAFKA_TOPIC/KAFKA_GROUP with the same FI_OBSERVED_CATALOG_ prefix.
Live and backfill share FI_OBSERVED_CATALOG_MAX_KEYS_PER_SPAN and
FI_OBSERVED_CATALOG_MAX_ARRAY_MEMBERS_PER_SPAN.

Progress proves Kafka acknowledgement, not consumer visibility or complete
source history. Re-run overlapping source ranges to repair late-arriving spans.
`)
		return
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "observed catalog backfill:", err)
		os.Exit(1)
	}
}

func runLegacy(ctx context.Context, cfg options, scopes scopeReader, publisher observedcatalog.Publisher, output io.Writer) error {
	scope, err := scopes.Scope(ctx, cfg.project)
	if err != nil {
		return err
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
		return errors.New("page budget reached; legacy import incomplete, resume the same checkpoint")
	}
	return nil
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
	var publisher *observedcatalog.Producer
	if cfg.apply {
		publisher, err = observedcatalog.NewProducer(cfg.kafka)
		if err != nil {
			return err
		}
		defer publisher.Close()
	}
	if cfg.mode == "legacy" {
		return runLegacy(ctx, cfg, owner, publisher, output)
	}
	return runSpans(ctx, cfg, owner, publisher, output)
}
