package main

import (
	"bytes"
	"context"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"github.com/jackc/pgx/v5"
)

func localPostgres(t *testing.T) (string, *pgx.Conn) {
	t.Helper()
	dsn := os.Getenv("OBS_TEST_PG_DSN")
	if dsn == "" {
		t.Skip("set OBS_TEST_PG_DSN for isolated PostgreSQL integration")
	}
	u, err := url.Parse(dsn)
	if err != nil || u.Hostname() != "127.0.0.1" || u.Port() == "" || u.Scheme != "postgres" {
		t.Fatal("test PostgreSQL must be loopback with explicit port")
	}
	admin, err := pgx.Connect(context.Background(), dsn)
	if err != nil {
		t.Fatal(err)
	}
	database := fmt.Sprintf("observed_test_%d", time.Now().UnixNano())
	if _, err := admin.Exec(context.Background(), "CREATE DATABASE "+database); err != nil {
		admin.Close(context.Background())
		t.Fatal(err)
	}
	u.Path = "/" + database
	fixture, err := pgx.Connect(context.Background(), u.String())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		fixture.Close(context.Background())
		if _, err := admin.Exec(context.Background(), "DROP DATABASE "+database); err != nil {
			t.Error(err)
		}
		admin.Close(context.Background())
	})
	_, err = fixture.Exec(context.Background(), `CREATE TABLE accounts_organization(id uuid PRIMARY KEY);
CREATE TABLE accounts_workspace(id uuid PRIMARY KEY, organization_id uuid, deleted boolean DEFAULT false, is_active boolean DEFAULT true);
CREATE TABLE tracer_project(id uuid PRIMARY KEY, organization_id uuid, workspace_id uuid, deleted boolean DEFAULT false);`)
	if err != nil {
		t.Fatal(err)
	}
	scope := exampleScope()
	if _, err = fixture.Exec(context.Background(), `INSERT INTO accounts_organization VALUES ($1);`, scope.OrganizationID); err != nil {
		t.Fatal(err)
	}
	if _, err = fixture.Exec(context.Background(), `INSERT INTO accounts_workspace(id, organization_id) VALUES ($1,$2)`, scope.WorkspaceID, scope.OrganizationID); err != nil {
		t.Fatal(err)
	}
	if _, err = fixture.Exec(context.Background(), `INSERT INTO tracer_project(id, organization_id, workspace_id) VALUES ($1,$2,$3)`, scope.ProjectID, scope.OrganizationID, scope.WorkspaceID); err != nil {
		t.Fatal(err)
	}
	return u.String(), fixture
}

func TestPostgresOwnershipIsCurrentAndReadOnly(t *testing.T) {
	dsn, fixture := localPostgres(t)
	reader, err := newPostgresScope(context.Background(), dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.conn.Close(context.Background())
	var readonly string
	if err := reader.conn.QueryRow(context.Background(), "SHOW transaction_read_only").Scan(&readonly); err != nil || readonly != "on" {
		t.Fatal("ownership reader is not readonly", err)
	}
	scope, err := reader.Scope(context.Background(), exampleScope().ProjectID)
	if err != nil || scope != exampleScope() {
		t.Fatal("valid scope rejected", err)
	}
	if _, err := fixture.Exec(context.Background(), "UPDATE accounts_workspace SET deleted=true"); err != nil {
		t.Fatal(err)
	}
	if _, err := reader.Scope(context.Background(), exampleScope().ProjectID); err == nil {
		t.Fatal("deleted workspace accepted")
	}
	if _, err := fixture.Exec(context.Background(), "UPDATE accounts_workspace SET deleted=false; UPDATE tracer_project SET workspace_id=NULL"); err != nil {
		t.Fatal(err)
	}
	if _, err := reader.Scope(context.Background(), exampleScope().ProjectID); err == nil {
		t.Fatal("projectless scope widened")
	}
}

func TestBackfillCLIThroughKafkaWithResume(t *testing.T) {
	dsn, _ := localPostgres(t)
	kafka := localKafka(t)
	origin, sourceDB := localClickHouse(t)
	_, catalogDB, sink := localCatalog(t)
	scope := exampleScope()
	localSQL(t, origin, `CREATE TABLE `+sourceDB+`.spans (
project_id UUID, org_id Nullable(UUID), observation_type String, service_name String, trace_id String, id String,
start_time DateTime64(6, 'UTC'), attrs_string Map(String,String), attrs_number Map(String,Float64), attrs_bool Map(String,UInt8),
attributes_extra JSON, model String, _version UInt64, is_deleted UInt8)
ENGINE=MergeTree ORDER BY (project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id)`)
	localSQL(t, origin, `INSERT INTO `+sourceDB+`.spans FORMAT JSONEachRow
{"project_id":"`+scope.ProjectID+`","org_id":"`+scope.OrganizationID+`","observation_type":"span","service_name":"s","trace_id":"t","id":"a","start_time":"2026-01-01 12:01:00","attrs_string":{"company_id":"001"},"attrs_number":{},"attrs_bool":{},"attributes_extra":{},"model":"m","_version":1,"is_deleted":0}
{"project_id":"`+scope.ProjectID+`","org_id":"`+scope.OrganizationID+`","observation_type":"span","service_name":"s","trace_id":"t","id":"b","start_time":"2026-01-01 12:02:00","attrs_string":{"company_id":"002"},"attrs_number":{},"attrs_bool":{},"attributes_extra":{},"model":"m","_version":1,"is_deleted":0}`)
	fingerprintSQL := "SELECT count(), groupBitXor(cityHash64(toJSONString(tuple(*)))) FROM " + sourceDB + ".spans"
	before := localSQL(t, origin, fingerprintSQL)
	env := map[string]string{"FI_PG_DSN": dsn, "FI_OBSERVED_BACKFILL_CH_URL": origin, "FI_OBSERVED_BACKFILL_CH_DATABASE": sourceDB,
		"FI_OBSERVED_BACKFILL_CH_USERNAME": "test", "FI_OBSERVED_BACKFILL_CH_PASSWORD": "test",
		"FI_OBSERVED_CATALOG_KAFKA_BROKERS": kafka.Brokers[0], "FI_OBSERVED_CATALOG_KAFKA_TOPIC": kafka.Topic, "FI_OBSERVED_CATALOG_KAFKA_GROUP": kafka.Group}
	getenv := func(k string) string { return env[k] }
	args := []string{"--project", scope.ProjectID, "--since", "2026-01-01T12:00:00Z", "--until", "2026-01-01T13:00:00Z", "--page-size", "1", "--page-delay", "0s"}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := run(ctx, args, getenv, &bytes.Buffer{}); err != nil {
		t.Fatal("preview", err)
	}
	if result := localSQL(t, origin, "SELECT count() FROM "+catalogDB+".observed_attribute_keys"); strings.TrimSpace(result) != "0" {
		t.Fatal("preview wrote catalog")
	}
	checkpointPath := filepath.Join(t.TempDir(), "progress.json")
	apply := append(append([]string{}, args...), "--apply", "--checkpoint", checkpointPath)
	if err := run(ctx, append(append([]string{}, apply...), "--max-pages", "1"), getenv, &bytes.Buffer{}); err == nil || !strings.Contains(err.Error(), "page budget reached") {
		t.Fatal("bounded scan did not stop honestly", err)
	}
	if err := run(ctx, apply, getenv, &bytes.Buffer{}); err != nil {
		t.Fatal("resume failed", err)
	}
	consumer, err := observedcatalog.NewConsumer(kafka, sink)
	if err != nil {
		t.Fatal(err)
	}
	work, stop := context.WithCancel(ctx)
	done := make(chan error, 1)
	go func() { done <- consumer.Run(work) }()
	defer func() { stop(); <-done; consumer.Close() }()
	for {
		count := localSQL(t, origin, "SELECT uniqExact(tuple(attribute_key,value_json)) FROM "+catalogDB+".observed_attribute_values")
		if strings.TrimSpace(count) == "3" {
			break
		}
		select {
		case err := <-done:
			done <- err
			t.Fatal("consumer exited", err)
		case <-ctx.Done():
			t.Fatal("backfill visibility timeout")
		case <-time.After(100 * time.Millisecond):
		}
	}
	if after := localSQL(t, origin, fingerprintSQL); before != after {
		t.Fatal("backfill changed source spans")
	}
}
