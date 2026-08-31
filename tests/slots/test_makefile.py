from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def test_slot_selector_is_not_exported_to_child_processes(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        f'include {ROOT / "Makefile"}\ncheck-slot-environment:\n\t@test -z "$$SLOT"\n',
        encoding="utf-8",
    )

    subprocess.run(
        ["make", "-f", str(makefile), "check-slot-environment", "SLOT=auto"],
        cwd=ROOT,
        check=True,
    )
