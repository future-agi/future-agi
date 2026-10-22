"""E2E-image-only provisioning of one fixed, read-only SQL fixture account."""

import ipaddress
import os

from clickhouse_driver import Client

from tracer.sql_workbench.provision import service_access_ddl

ACCOUNT = "e2e_sql_service"
FIXTURE_PASSWORD = "e2e-sql-workbench-password-for-tests"


def provision_fixture(environ, client_factory=Client):
    if (
        environ.get("FUTURE_AGI_E2E_FIXTURE") != "sql-workbench"
        or environ.get("SQL_WORKBENCH_CH_USER") != ACCOUNT
        or environ.get("SQL_WORKBENCH_CH_PASSWORD") != FIXTURE_PASSWORD
        or environ.get("CH_HOST") not in {"clickhouse", "localhost", "127.0.0.1"}
    ):
        raise ValueError(
            "SQL fixture bootstrap requires the explicit managed E2E configuration."
        )
    client = client_factory(
        host=environ["CH_HOST"],
        port=int(environ.get("CH_PORT", "9000")),
        database=environ.get("CH_DATABASE", "default"),
        user=environ.get("CH_USERNAME", "default"),
        password=environ.get("CH_PASSWORD", ""),
        connect_timeout=3,
        send_receive_timeout=10,
    )
    try:
        address = ipaddress.ip_address(
            str(
                client.execute(
                    "SELECT address FROM system.processes WHERE query_id=currentQueryID()"
                )[0][0]
            )
        )
        address = getattr(address, "ipv4_mapped", None) or address
        if not (address.is_private or address.is_loopback):
            raise ValueError("SQL fixture bootstrap requires a private test network.")
        # The bootstrap, backend and worker have separate IPs on the isolated
        # Compose bridge. This account never permits public or HOST ANY access.
        prefix = (
            (32 if address.is_loopback else 16)
            if address.version == 4
            else (128 if address.is_loopback else 64)
        )
        network = str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))
        statements = service_access_ddl(
            database=environ.get("CH_DATABASE", "default"),
            principal=ACCOUNT,
            password=FIXTURE_PASSWORD,
            cidr=network,
            eval_table=environ.get("CH25_EVAL_LOGGER_TABLE", "tracer_eval_logger"),
        )
        exists = client.execute(
            "SELECT count() FROM system.users WHERE name=%(name)s", {"name": ACCOUNT}
        )[0][0]
        if exists:
            roles = client.execute(
                "SELECT count() FROM system.role_grants WHERE user_name=%(name)s",
                {"name": ACCOUNT},
            )[0][0]
            if roles:
                raise ValueError(
                    "Unexpected roles on the SQL fixture account; refusing to normalize it."
                )
            client.execute(statements[0].replace("CREATE USER", "ALTER USER", 1))
            client.execute(f"REVOKE ALL ON *.* FROM {ACCOUNT}")
        else:
            client.execute(statements[0])
        for statement in statements[1:]:
            client.execute(statement)
    finally:
        client.disconnect()


if __name__ == "__main__":
    try:
        provision_fixture(os.environ)
    except Exception as exc:
        raise SystemExit(
            f"SQL fixture bootstrap failed ({type(exc).__name__})."
        ) from None
    print("Read-only SQL fixture account provisioned.")
