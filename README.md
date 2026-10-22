# Timetrack

A small Python desktop timer for **On task** versus **Off task** time. Start immediately, toggle when your focus changes, and label segments later.

## Use

1. Click **Start session** to begin On task.
2. Use the large button to switch between On task and Off task.
3. Click **End session** to review, label, split, or adjust segments.
4. Export CSV to your chosen location.

The active segment stays in memory. Only Toggle and End session append a JSON object to `TEMP/session_*.jsonl`. A crash loses the active segment, not earlier completed segments. Cancelled or failed exports keep the capture file. Successful exports remove it. Review edits stay in memory until exported.

## Run from source

Requires Python 3.12 or later with Tkinter. No third-party runtime packages are needed.

```sh
python main.py
```

On Debian/Ubuntu, install `python3-tk` if Tkinter is missing. The application creates TEMP beneath its **launch working directory**, which must be writable.

## Portable packages

The **Test and package** GitHub Actions workflow builds Windows x86-64 ZIP and Linux x86-64 tar.gz packages on pushes to `main`, version tags, and manual runs. Pull requests run tests only. Download the artifact for your OS from the completed Actions run; version tags also publish packages to GitHub Releases.

Extract the entire archive into a writable folder; keep the executable and `_internal` folder together. Python does not need to be installed on the destination computer.

- **Windows:** use `Start-Timetrack.cmd` or `Timetrack.exe`.
- **Linux:** run `./start-timetrack.sh`. The CI build targets Ubuntu 22.04 or newer compatible glibc desktops; it requires a graphical session and is not an Alpine/musl or ARM build.

The launch scripts set the working directory to the extracted folder. Direct launches use the caller's working directory. Packages are unsigned and do not install system services.

## Tests

Run from the project root:

```sh
python -m unittest discover -s tests -v
```

The original eight tests are retained. Additional deterministic tests cover timing, retries, clock changes, labels, invalid records, long records, locks, recovery, CSV quoting, and failed exports. CI runs tests on both Windows and Linux and launches the actual source and packaged GUI as smoke tests.

## Build locally

Build on the operating system you are targeting; this does not cross-compile.

```sh
python -m pip install -r requirements-build.txt
python scripts/package.py
```

A graphical display is needed for the smoke test. On Linux CI or a headless desktop, use `xvfb-run -a python scripts/package.py` after installing Xvfb. `--skip-smoke-test` is available only for local environments without a display; CI does not use it.

Outputs are placed in `release/`, with a SHA-256 checksum beside each archive. PyInstaller builds a portable folder to avoid one-file extraction on every launch.

## Release

Update `VERSION`, commit the change, then push a matching tag:

```sh
git tag v0.1.0
git push origin v0.1.0
```

Tag builds publish a release. Alternatively, push a commit to `main` whose complete message is `release: v0.1.0` (matching `VERSION`); the workflow creates that tag at the tested commit and publishes the same assets. Test and package jobs must succeed first. The release job verifies checksums, publishes both packages, and checks the job results and all four uploaded assets. Ordinary builds remain Actions artifacts.

## Project layout

| File | Purpose |
| --- | --- |
| `main.py` | Application entry point and CI smoke-test option |
| `models.py` | Segments, validation, formatting, review edits |
| `tracker.py` | Timing and session transitions |
| `storage.py` | JSONL persistence, locks, recovery, CSV export |
| `ui.py` | Tkinter interface |
| `tests/` | Unit and regression tests |
| `scripts/package.py` | Native build, smoke test, archive, checksum |
| `.github/workflows/build.yml` | Windows/Linux CI and tagged releases |
| `Designplan.md` | Original application design specification |

## Known limitations

Sleep and clock changes are flagged for review; there is no automatic idle detection. Recovery preserves captured segments but not unsaved review edits or in-memory discrepancy warnings. A desktop smoke test verifies startup, not every interactive workflow. The design plan is a specification; some advanced recovery behaviours remain future work.
