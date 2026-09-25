"""Build one installable ZIP per custom Blender add-on."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
ADDONS = (
    "hemy_ifc_panel",
    "bonsai_ux_host",
    "bonsai_section_box",
    "bonsai_plan_dimensions",
    "bonsai_dynamic_dimension",
    "bonsai_grid_toolbar",
    "bonsai_nucleus_files",
    "bonsai_nucleus_sync",
    "bonsai_slab_thickness",
    "bonsai_storey_toolbar",
    "workplane_toolkit",
)
EXTENSIONS = {"bonsai_plan_dimensions", "workplane_toolkit"}


def build(addon_name: str) -> Path:
    source = ROOT / addon_name
    if not (source / "__init__.py").is_file():
        raise FileNotFoundError(f"Missing add-on entry point: {source / '__init__.py'}")
    files = sorted(
        path for path in source.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    if not files:
        raise ValueError(f"No Python source in {source}")
    output = DIST / f"{addon_name}.zip"
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in files:
            archive_name = path.relative_to(source if addon_name in EXTENSIONS else ROOT).as_posix()
            archive.write(path, archive_name)
    with ZipFile(output) as archive:
        entry = "__init__.py" if addon_name in EXTENSIONS else f"{addon_name}/__init__.py"
        assert entry in archive.namelist()
        if addon_name in EXTENSIONS:
            assert "blender_manifest.toml" in archive.namelist()
        assert len(archive.namelist()) == len(set(archive.namelist()))
    return output


def main() -> None:
    DIST.mkdir(exist_ok=True)
    for addon in ADDONS:
        print(f"Built {build(addon).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
