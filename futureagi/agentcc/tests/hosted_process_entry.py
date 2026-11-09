"""Fresh-process rehearsal entry; invokes the real allowlisted command.

Fault injection is confined to this test module, never a production option.
The caller creates and drops a new synthetic loopback database.
"""

import importlib
import os
import sys


def main():
    if os.environ.get("G18_SYNTHETIC_REHEARSAL") != "1" or not os.environ.get(
        "OMEGA_EXEC_SOURCE_HEAD"
    ):
        raise RuntimeError("This rehearsal is cloud-only and opt-in")
    if os.environ.get("PG_HOST") not in (
        "127.0.0.1",
        "localhost",
    ) or not os.environ.get("PG_DB", "").startswith("g18_operator_"):
        raise RuntimeError("Only disposable loopback databases are allowed")
    import django
    from django.core.management import execute_from_command_line

    args = sys.argv[1:]
    if not args or args[0] not in ("hosted_gateway_migrate", "readiness", "continuity"):
        raise RuntimeError("Unknown synthetic process action")
    sys.argv = (
        ["manage.py", "hosted_gateway_migrate", *args[1:]]
        if args[0] == "hosted_gateway_migrate"
        else ["gateway-rehearsal-process"]
    )
    django.setup()
    from agentcc.tests.hosted_operator_contention import install_pause
    from agentcc.tests.hosted_process_diagnostics import install

    install()
    install_pause()
    if os.environ.get("G18_INTERRUPT_0047") == "1":
        migration = importlib.import_module(
            "agentcc.migrations.0047_encrypt_org_config_secrets"
        )
        transform = migration._transform_row
        calls = 0

        def interrupted(row, fn):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("synthetic interruption")
            return transform(row, fn)

        migration.BATCH_SIZE = 1
        migration._transform_row = interrupted
    if os.environ.get("G18_INTERRUPT_KEY_UPDATE") == "1":
        from django.db.models.query import QuerySet

        update = QuerySet.update
        updates = 0

        def interrupted_update(query, **kwargs):
            nonlocal updates
            if query.model._meta.app_label == "prism" and any(
                name.startswith("encrypted_") for name in kwargs
            ):
                updates += 1
                if updates == 2:
                    raise RuntimeError("synthetic mid-batch interruption")
            return update(query, **kwargs)

        QuerySet.update = interrupted_update
    if args[0] == "readiness":
        from agentcc.readiness import readiness

        response = readiness(None)
        print(response.status_code)
        print(response.content.decode())
        return
    if args[0] == "continuity":
        from django.apps import apps

        from agentcc.services.credential_manager import decrypt_token
        from agentcc.services.gateway_key_inventory import inventory
        from agentcc.services.org_config_secrets import iter_secret_slots
        from integrations.services.credentials import CredentialManager

        for store in inventory():
            for row in store.model._base_manager.using("default_direct").all():
                if store.config:
                    for slot in iter_secret_slots(
                        {name: getattr(row, name) for name in store.fields}
                    ):
                        if slot.value:
                            assert isinstance(decrypt_token(slot.value), str)
                else:
                    for name in store.fields:
                        value = getattr(row, name)
                        if value:
                            if store.wrapped:
                                assert isinstance(decrypt_token(value), str)
                            else:
                                assert isinstance(
                                    CredentialManager.decrypt(bytes(value)), dict
                                )
        apps.get_app_config("prism").ready()
        print("gateway-continuity-ok")
        return
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
