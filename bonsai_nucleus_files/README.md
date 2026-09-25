# Bonsai Nucleus Files 1.0.0

A Blender add-on for browsing NVIDIA Omniverse Nucleus and opening/saving native **IFC, IFCZIP, and Blender (.blend)** files. It uses Bonsai's native IFC save pipeline and Blender's native file operators. It does not convert your project to USD.

## Install on this PC

1. In Blender, open **Edit → Preferences → Add-ons → menu → Install from Disk**.
2. Select **`bonsai_nucleus_files-1.0.0.zip`** and enable **Bonsai Nucleus Files**. Keep **Bonsai** enabled for IFC projects.
3. Set the NVIDIA SDK Python and SDK folder in the add-on preferences. You can also set `BONSAI_NUCLEUS_PYTHON` and `BONSAI_NUCLEUS_SDK` environment variables, then click **Detect Runtime**. The receiver app itself does not need to be running.
4. Enter your Nucleus home folder in the add-on preferences. In the 3D View, press **N** and choose the **Nucleus** tab. Click **Connect / Refresh**. Allow online access in Blender if it is disabled. Complete NVIDIA's browser login if prompted.

Use your own Nucleus home folder, for example:

```text
omniverse://your-server.example.com/Projects/
```

The space is encoded internally as `%20`; underscores are literal.

## Open a project

Select a folder and click **Open from Nucleus** to enter it. Select an `.ifc`, `.ifczip`, or `.blend` file and click the same button to download it. Confirm **Open** when the download finishes. Opening replaces the current project, so save any current edits first. Cancelling this confirmation keeps the downloaded file available through **Open Downloaded Project…**.

Other file types remain visible in the browser. Revit `.rvt` and USD files cannot be opened as native Bonsai projects by this add-on.

## Save a project

Choose **IFC project** or **Blender project**, then:

- **Save As…** creates a new native file in the chosen Nucleus folder. It refuses an existing name.
- **Save** updates the Nucleus file associated with the active project. If there is no association, it opens Save As.
- A remote change since the last open/save causes a conflict error. Use a new name to preserve your work, or reopen the latest remote version before editing it. There is no force-overwrite button.

The status must say **Saved to Nucleus** before you consider the upload complete. The **Local copy** field identifies the recovery file. Failed or cancelled uploads retain it. An interrupted upload can have an uncertain remote result; refresh and inspect the remote version before retrying.

The commands are also available under **File → Nucleus**. Ordinary Blender/Bonsai save commands and Ctrl+S save locally; they do not upload. Use the Nucleus commands to update the server.

## IFC data inside .blend

When a Bonsai IFC model is active, a Nucleus `.blend` save first runs Bonsai's IFC save pipeline and embeds the resulting IFC text in the `.blend`. On opening, this add-on restores that snapshot into its managed local cache before Bonsai loads the BIM model. The round-trip test removed the original IFC and nearby sidecar and confirmed that the embedded model still opened.

This snapshot includes the active IFC model. It is separate from any independently saved `.ifc` on Nucleus. Saving `.blend` does not also update a separate remote IFC file.

Keep this add-on and Bonsai enabled when reopening these `.blend` files for IFC editing. Without the add-on, Blender can open the scene, but automatic IFC extraction is unavailable. The snapshot is stored in the text block `.nucleus-embedded.ifc` and can be exported manually as an IFC file. Re-save through the Nucleus commands after IFC edits to refresh the snapshot.

**Pack supported .blend resources** calls Blender's Pack Resources operation before saving. Unpackable external resources, linked libraries, IFC document references, linked IFC models, and separate Bonsai metadata files are not recursively uploaded or downloaded. The connector suppresses Bonsai's automatic metadata `.blend` sidecar creation during its IFC saves. Use self-contained files or manage their dependencies separately. Large IFC files retain Bonsai's normal advanced-loading behavior.

## Runtime setup on another computer

The installer contains the add-on source; NVIDIA's proprietary SDK and Python runtime are not bundled. Windows x64 is the tested platform.

Supply these two settings in the add-on preferences:

| Setting | Required value |
| --- | --- |
| SDK Python executable | A Python interpreter matching a compiled binding shipped in your NVIDIA Client Library, usually Python 3.10, 3.11, or 3.12 |
| NVIDIA SDK folder | An extracted `omni_client_library` package, its `release/bindings-python` directory, or a Kit `extscore/omni.client.lib` directory |
| Working copies folder | Optional writable local location; the default is Blender's user data directory under `bonsai_nucleus_files` |
| Transfer timeout | Default 180 seconds; increase for large files or slow connections |

An existing Kit installation may provide `kit/python/python.exe` and `kit/extscore/omni.client.lib`.

Alternatively obtain NVIDIA's Client Library from the [official connector sample instructions](https://github.com/NVIDIA-Omniverse/connect-samples). Environment overrides are `BONSAI_NUCLEUS_PYTHON`, `BONSAI_NUCLEUS_SDK`, and `BONSAI_NUCLEUS_CACHE`. Explicit preference values take priority. Non-Windows platforms are not verified; a Linux standalone SDK may additionally require its library directory in `LD_LIBRARY_PATH` before the worker starts. NVIDIA's current standalone Client Library distribution does not provide macOS binaries.

The worker reuses NVIDIA's cached tokens or normal browser authentication. The add-on has no password field and does not save account credentials in `.blend` files, request files, or its settings. For noninteractive credentials, use NVIDIA's documented authentication facilities in your own environment.

## How file protection works

Each open/save uses a fresh local working directory. Downloads compare remote revisions before and after transfer and check the file size. Saves compare the expected remote revision, take a Nucleus file lock for existing files, recheck the revision under that lock, upload, verify remote size, and release the lock. New files use the SDK's error-if-exists operation. Saves include a Nucleus checkpoint comment through the SDK where the server supports checkpoints.

Transfers run in separate SDK processes. Blender polls their completion with app timers; no background thread accesses Blender's data. Native IFC/Blender serialization still runs on Blender's main thread and can take time for large models. Cancel stops the helper process. Local copies and diagnostic logs remain under the working-copy folder; they are not automatically pruned.

This is native file storage with conflict protection, not simultaneous multi-user IFC editing or live USD synchronization. Your existing Bonsai Live Sync add-on is independent.

## Troubleshooting

| Message or symptom | Action |
| --- | --- |
| SDK worker cannot import `omni.client` | Check the SDK directory and Python ABI. Blender's Python 3.13 is not used to load the tested NVIDIA bindings. |
| Connection times out | Check network access to Nucleus, finish browser login, and increase the timeout if needed. |
| Access denied / lock error | Check folder/file write permissions and whether another client holds a lock. |
| File changed or name already in use | Save As to a new filename, or open the latest remote file. |
| IFC restore failed | Keep the local `.blend`; confirm Bonsai is enabled and inspect the embedded IFC snapshot. |
| Resources missing on another PC | Pack supported resources and distribute external dependencies separately. |

## Development

The add-on supports Blender 4.2+ at the API level. The verified runtime is **Blender 5.2.1 LTS, Bonsai 0.8.5, NVIDIA Client Library 2.73.1, Windows x64**. Other versions are compatibility targets, not verified combinations.

```text
python -m unittest discover -s tests -v
python scripts/build.py
```

The integration scripts use the real applications. From a source checkout, run
`scripts/test_blender_native.py` with Blender's `--background --python` flags first.
It creates only synthetic local fixtures. Run `scripts/test_nucleus_live.py` with
the SDK-compatible Python to create a uniquely named test folder on Nucleus and
exercise transfers. Set `BONSAI_NUCLEUS_TEST_FOLDER` to choose its parent folder;
the default is the supplied test server. This script intentionally creates test
files and leaves them available for inspection. Then run
`scripts/test_blender_operators.py` with Blender's
`--background --online-mode --python` flags to exercise the UI operators against
that test folder. These tests use `work/` beneath the source checkout. Use a
separate background Blender process with Bonsai enabled; they replace that
process's current project.

See `VERIFICATION.md` for executed tests and their limits. The implementation is GPL-3.0-or-later. It is an independent integration, not an official NVIDIA or IfcOpenShell connector.

## API references

- [NVIDIA Client Library Python API](https://docs.omniverse.nvidia.com/kit/docs/client_library/latest/docs/python.html): listing, transfer, file locks and result codes.
- [NVIDIA Client Library](https://docs.omniverse.nvidia.com/kit/docs/client_library/latest/index.html): distribution and Nucleus authentication.
- [Bonsai project operators](https://github.com/IfcOpenShell/IfcOpenShell/blob/v0.8.0/src/bonsai/bonsai/bim/module/project/operator.py): native IFC load/save integration.
- [Bonsai native-file workflow](https://docs.bonsaibim.org/reference/general/topbar.html): IFC and Blender file roles.
