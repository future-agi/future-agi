"""Import the Official entries of an exported Omega commit as `channel=github` submissions
(SPEC §14.1 "Seeding"). Each becomes `received`; the real checker takes them to `in_review`.

    C=<commit>; D=$TMPDIR/omega-hub-src-${C:0:9}
    git -C <omega checkout> archive "$C" | tar -x -C "$D"
    python manage.py omega_hub_import_official --omega-dir "$D" --commit "$C" --submitted-by <A's email>

`--fill-missing` is for commits that predate the hub-cli lane's C1 (no `version` or `disclosure` in
the manifests): it sets version 1.0.0 and the entry's draft disclosure from
omega_hub/seeds/official-disclosures.json, written by hand from the entry's code. A maintainer still
confirms or rewrites that draft in review. An entry with no seed, or whose code changed since its
seed was written, is refused, and then nothing is imported: there is no placeholder text.
"""

import io
import json
import re
import tarfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from omega_hub import official_seed
from omega_hub.models import Publisher
from omega_hub.services import intake

SOURCE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
SKIP = {"node_modules", ".git", "dist"}


def add_tree(archive: tarfile.TarFile, root: Path, prefix: str) -> None:
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if (
            any(part in SKIP for part in rel.parts)
            or path.is_symlink()
            or not path.is_file()
        ):
            continue
        info = tarfile.TarInfo(f"{prefix}/{rel.as_posix()}")
        data = path.read_bytes()
        info.size, info.mode, info.mtime = len(data), 0o644, 0
        archive.addfile(info, io.BytesIO(data))


def pack_folder(entry_dir: Path, manifest: dict, omega_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        manifest_bytes = (
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        for path in sorted(entry_dir.rglob("*")):
            rel = path.relative_to(entry_dir)
            if (
                any(part in SKIP for part in rel.parts)
                or path.is_symlink()
                or not path.is_file()
            ):
                continue
            data = (
                manifest_bytes
                if rel.as_posix() == "omega.hub.json"
                else path.read_bytes()
            )
            info = tarfile.TarInfo(f"entry/{rel.as_posix()}")
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            archive.addfile(info, io.BytesIO(data))
        source = manifest.get("source")
        if isinstance(source, str):
            if not SOURCE.match(source) or not (omega_dir / source).is_dir():
                raise CommandError(
                    f"{manifest['id']}: source {source!r} is not a folder at the commit's root"
                )
            add_tree(archive, omega_dir / source, "source")
    return buffer.getvalue()


class Command(BaseCommand):
    help = (
        "Create one github-channel submission per hub/<id> of an exported Omega commit."
    )

    def add_arguments(self, parser):
        parser.add_argument("--omega-dir", required=True)
        parser.add_argument("--commit", required=True)
        parser.add_argument("--submitted-by", required=True)
        parser.add_argument(
            "--only", default="", help="Comma-separated ids (default: every entry)."
        )
        parser.add_argument(
            "--fill-missing",
            action="store_true",
            help="Pre-C1 commits: add version 1.0.0 and the seed draft disclosure (omega_hub/seeds), "
            "which a maintainer confirms in review.",
        )

    def handle(
        self, *args, omega_dir, commit, submitted_by, only, fill_missing, **options
    ):
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise CommandError("--commit is a full 40-character commit SHA")
        omega = Path(omega_dir).resolve()
        hub = omega / "hub"
        if not hub.is_dir():
            raise CommandError(f"{hub} does not exist")
        user = get_user_model().objects.filter(email=submitted_by).first()
        if user is None:
            raise CommandError(f"no user {submitted_by}")
        publisher = Publisher.objects.filter(
            handle="future-agi", tier=Publisher.Tier.OFFICIAL
        ).first()
        if publisher is None:
            raise CommandError(
                "no Official future-agi publisher: an operator must run "
                "omega_hub_ensure_official_publisher --owner-email <staff-email> first"
            )
        wanted = {item for item in only.split(",") if item}
        planned, refused = [], []
        for entry_dir in sorted(
            p for p in hub.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))
        ):
            if wanted and entry_dir.name not in wanted:
                continue
            manifest_path = entry_dir / "omega.hub.json"
            if not manifest_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if fill_missing:
                manifest.setdefault("version", "1.0.0")
                if "disclosure" not in manifest:
                    try:
                        manifest["disclosure"] = official_seed.disclosure_for(
                            str(manifest.get("id")),
                            official_seed.fingerprint(entry_dir, manifest, omega),
                        )
                    except official_seed.SeedError as error:
                        refused.append(str(error))
                        continue
            planned.append((entry_dir, manifest))
        if refused:
            # Checked for every entry first, so a refusal leaves no partial import behind.
            raise CommandError("nothing imported:\n" + "\n".join(refused))
        created = []
        for entry_dir, manifest in planned:
            data = pack_folder(entry_dir, manifest, omega)
            submission = intake.receive(
                user,
                publisher,
                data=data,
                channel="github",
                name=manifest.get("id"),
                version=manifest.get("version"),
                notes=f"Imported from Omega {commit[:9]}.",
                github_commit=commit,
                enforce_limits=False,
            )
            created.append(
                {
                    "id": manifest.get("id"),
                    "submission": str(submission.pk),
                    "bytes": len(data),
                }
            )
            self.stdout.write(f"received {manifest.get('id')} as {submission.pk}")
        self.stdout.write(json.dumps({"commit": commit, "submissions": len(created)}))
