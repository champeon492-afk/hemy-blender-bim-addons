"""Install and enable the 11 custom BIM add-ons from their release ZIPs.

Run with Blender, not system Python:
    blender --background --python-exit-code 1 --python scripts/install_all.py
"""

import argparse
import sys
import tomllib
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

import addon_utils
import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.package import ADDONS, EXTENSIONS  # noqa: E402


# Dependencies first; the UX Host is enabled only after its companion tools.
INSTALL_ORDER = (
    "workplane_toolkit",
    "bonsai_storey_toolbar",
    "bonsai_grid_toolbar",
    "bonsai_plan_dimensions",
    "bonsai_dynamic_dimension",
    "bonsai_section_box",
    "bonsai_slab_thickness",
    "bonsai_nucleus_files",
    "bonsai_nucleus_sync",
    "hemy_ifc_panel",
    "bonsai_ux_host",
)
assert set(INSTALL_ORDER) == set(ADDONS)


def arguments():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=ROOT / "dist",
        help="directory containing the 11 add-on ZIPs (default: repository dist/)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="validate packages and prerequisites without installing anything",
    )
    parser.add_argument(
        "--install-only", action="store_true",
        help="install packages without enabling them; useful before installing Bonsai",
    )
    return parser.parse_args(argv)


def validate_packages(source):
    packages = {}
    for name in INSTALL_ORDER:
        path = source / f"{name}.zip"
        if not path.is_file():
            raise FileNotFoundError(f"Missing package: {path}")
        with ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or archive.testzip() is not None:
                raise ValueError(f"Corrupt or duplicate ZIP entry: {path}")
            if any(
                item.startswith("/") or ".." in PurePosixPath(item).parts or "\\" in item
                for item in names
            ):
                raise ValueError(f"Unsafe ZIP path: {path}")
            if name in EXTENSIONS:
                if "__init__.py" not in names or "blender_manifest.toml" not in names:
                    raise ValueError(f"Invalid extension package: {path}")
                manifest = tomllib.loads(archive.read("blender_manifest.toml").decode("utf-8"))
                if manifest.get("id") != name:
                    raise ValueError(f"Wrong extension ID in {path}")
            elif f"{name}/__init__.py" not in names or any(
                not item.startswith(f"{name}/") for item in names
            ):
                raise ValueError(f"Invalid legacy add-on package: {path}")
        packages[name] = path.resolve()
    return packages


def user_repo():
    repos = bpy.context.preferences.extensions.repos
    repo = next((item for item in repos if item.module == "user_default" and item.enabled), None)
    if repo is None:
        raise RuntimeError("Enable Blender's 'User Default' extension repository in Preferences.")
    return repo.module


def bonsai_enabled():
    return any(
        module == "bonsai" or module.startswith("bl_ext.") and module.endswith(".bonsai")
        for module in bpy.context.preferences.addons.keys()
    )


def require_finished(result, label):
    if "FINISHED" not in result:
        raise RuntimeError(f"{label} did not finish: {result}")


def main():
    args = arguments()
    source = args.source.expanduser().resolve()
    packages = validate_packages(source)
    repo = user_repo()
    if not args.install_only and not bonsai_enabled():
        raise RuntimeError(
            "Official Bonsai must be installed and enabled first. "
            "Use --install-only to place these packages before enabling Bonsai."
        )
    print(f"Validated {len(packages)} add-on ZIPs in {source}")
    if args.dry_run:
        print("Dry run complete; Blender preferences and add-on files were not changed.")
        return

    installed = []
    try:
        for name in INSTALL_ORDER:
            path = packages[name]
            if name in EXTENSIONS:
                result = bpy.ops.extensions.package_install_files(
                    filepath=str(path), repo=repo, enable_on_install=False, overwrite=True,
                )
            else:
                result = bpy.ops.preferences.addon_install(
                    filepath=str(path), overwrite=True, enable_on_install=False,
                )
            require_finished(result, f"Installing {name}")
            installed.append(name)
            print(f"Installed {name}")

        if not args.install_only:
            addon_utils.modules_refresh()
            for name in INSTALL_ORDER:
                module = f"bl_ext.{repo}.{name}" if name in EXTENSIONS else name
                require_finished(
                    bpy.ops.preferences.addon_enable(module=module), f"Enabling {module}"
                )
                if not addon_utils.check(module)[1]:
                    raise RuntimeError(f"Blender did not enable {module}")
                print(f"Enabled {module}")

            require_finished(bpy.ops.wm.save_userpref(), "Saving Blender preferences")
    except Exception:
        print(f"Installation stopped after: {', '.join(installed) or 'none'}", file=sys.stderr)
        print("An earlier package may already be installed; inspect Blender Preferences before retrying.", file=sys.stderr)
        raise
    print(f"BIM_ADDONS_OK {len(installed)} {'installed' if args.install_only else 'installed and enabled'}")


if __name__ == "__main__":
    main()
