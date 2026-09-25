# Verification — Bonsai Nucleus Files 1.0.0

Verified on 14 September 2026 using Windows x64, Blender 5.2.1 LTS / Python 3.13.13,
Bonsai 0.8.5, and NVIDIA Client Library 2.73.1-release.7171+gl.9fe356ba in its Python 3.12 runtime.

| Test group | Result |
| --- | --- |
| Unit tests | 16 passed: URLs, Unicode, path rejection, revision conflicts, new-name protection, locks, failed uploads and interrupted downloads |
| Native application round trips | 17 checks passed: actual IFC wall geometry, IFCZIP, Blender objects, IFC snapshot extraction, object mappings and placement changes |
| Live Nucleus transfers | 11 checks passed: authenticated listing, IFC/IFCZIP/.blend upload and byte-identical download, locked updates, lock release and stale-writer rejection |
| Blender operators using SDK workers | 21 checks passed: browse, IFC and .blend Save As/Save, independent download, reopening with embedded IFC edits, remote association and cancellation |
| Installer | 5 checks passed: ZIP installation, enable/disable, preferences, runtime detection and panel icon compatibility in an isolated profile |

Blender's network-access guard was also exercised: the operator refused to start
when online access was disabled, then completed when it was enabled for the test
process. The user's online-access preference was not changed.

Synthetic test files were created on the test Nucleus server. The add-on package
does not include server files or a default server address.

The installer was tested in an isolated workspace profile. It has **not** been
installed in the user's normal Blender profile. Install the provided ZIP and
enable the add-on to use it in the existing Blender session.

Tests used synthetic models in separate background Blender processes. The UI
operators and RNA registration were executed, but the interactive panel layout
was not visually audited. Large-model performance, other operating systems and
other application versions remain unverified. External file dependencies are not
recursively transferred. This is native file storage, not live IFC collaboration.

`VERIFICATION.json` contains the individual passed checks. The included source
scripts reproduce the tests; the live tests create a new synthetic server folder.
