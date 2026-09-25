"""Build installable ZIPs for the two Blender add-ons."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
ADDONS = ("hemy_ifc_panel", "bonsai_ux_host")


def build(addon_name: str) -> Path:
    source = ROOT / addon_name
    if not (source / "__init__.py").is_file():
        raise FileNotFoundError(f"Missing add-on entry point: {source / '__init__.py'}")
    files = sorted(path for path in source.rglob("*.py") if "__pycache__" not in path.parts)
    if not files:
        raise ValueError(f"No Python source in {source}")
    output = DIST / f"{addon_name}.zip"
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    with ZipFile(output) as archive:
        assert f"{addon_name}/__init__.py" in archive.namelist()
        assert len(archive.namelist()) == len(set(archive.namelist()))
    return output


def main() -> None:
    DIST.mkdir(exist_ok=True)
    for addon in ADDONS:
        print(f"Built {build(addon).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
