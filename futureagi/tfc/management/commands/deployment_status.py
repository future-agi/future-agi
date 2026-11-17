"""Read-only deployment summary; never emit configuration or exception values."""

import importlib
import logging
import os
import sys
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout

from django.core.management.base import BaseCommand, CommandError, CommandParser


@contextmanager
def _quiet_derivation():
    """Contain synchronous helper output and incidental import bytecode writes."""
    streams = (sys.stdout, sys.stderr)
    with open(os.devnull, "w") as sink, ExitStack() as cleanup:
        previous_logging = logging.root.manager.disable
        previous_bytecode = sys.dont_write_bytecode
        cleanup.callback(logging.disable, previous_logging)
        cleanup.callback(setattr, sys, "dont_write_bytecode", previous_bytecode)
        logging.disable(max(logging.CRITICAL, previous_logging))
        sys.dont_write_bytecode = True
        descriptors = {1, 2}
        for stream in streams:
            stream.flush()
            try:
                descriptors.add(stream.fileno())
            except (AttributeError, OSError, ValueError):
                # StringIO/capsys-style streams are handled by redirect_* below.
                pass
        for descriptor in descriptors:
            saved = os.dup(descriptor)
            cleanup.callback(os.close, saved)
            cleanup.callback(os.dup2, saved, descriptor)
            os.dup2(sink.fileno(), descriptor)
        with redirect_stdout(sink), redirect_stderr(sink):
            try:
                yield
            finally:
                # A helper may retain an original stream rather than sys.stdout.
                # Drain its buffer while its descriptor still targets devnull.
                for stream in streams:
                    stream.flush()


class _NoArgumentsParser(CommandParser):
    def parse_args(self, args=None, namespace=None):
        # Reject even argparse's otherwise-empty '--' and Django's base flags.
        if args:
            self.error("deployment-status-failed")
        return super().parse_args(args, namespace)

    def error(self, message):
        # argparse's normal diagnostic echoes the rejected tokens.
        if self.called_from_command_line:
            self.exit(2)
        raise CommandError("deployment-status-failed", returncode=2)


class Command(BaseCommand):
    requires_system_checks = []
    requires_migrations_checks = False

    def create_parser(self, prog_name, subcommand, **kwargs):
        parser = _NoArgumentsParser(
            prog="deployment_status",
            add_help=False,
            allow_abbrev=False,
            called_from_command_line=getattr(self, "_called_from_command_line", False),
        )
        # BaseCommand's dispatch still receives its defaults, but no option is
        # registered: the command has exactly one invocation, with no arguments.
        parser.set_defaults(
            settings=None,
            pythonpath=None,
            traceback=False,
            force_color=False,
            no_color=True,
        )
        return parser

    def handle(self, *args, **options):
        try:
            with _quiet_derivation():
                from django.conf import settings

                from tfc.settings.stripe_billing import stripe_key_mode
                from tfc.views.deployment_mode import get_deployment_mode

                mode = get_deployment_mode()
                stripe = stripe_key_mode(getattr(settings, "STRIPE_SECRET_KEY", None))
                if mode not in {"oss", "ee", "cloud"} or stripe not in {
                    "test",
                    "live",
                    "disabled",
                }:
                    raise ValueError("deployment-status-failed")
                if stripe == "disabled":
                    stripe = "absent"
                try:
                    importlib.import_module("ee.cloud")
                except Exception:
                    ee_cloud = "no"
                else:
                    ee_cloud = "yes"
        except BaseException:
            # Assemble every field before writing anything; do not expose the
            # exception (even with Django's usual --traceback option).
            self.stderr.write("deployment-status-failed\n")
            raise SystemExit(1) from None
        return f"mode={mode}\nee-cloud={ee_cloud}\nstripe={stripe}\n"
