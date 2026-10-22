"""Build a native portable folder, smoke-test it, then archive it."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def metadata():
    version = (ROOT / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('VERSION must contain a semantic version.')
    system = platform.system()
    if system not in ('Windows', 'Linux'):
        raise ValueError('Packaging supports Windows and Linux only.')
    if platform.machine().lower() not in ('amd64', 'x86_64'):
        raise ValueError('This packaging configuration targets x86-64 only.')
    return version, system


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-smoke-test', action='store_true',
                        help='For local builds without a display only; CI never skips.')
    args = parser.parse_args()
    version, system = metadata()
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--onedir', '--name', 'Timetrack', '--noupx']
    if system == 'Windows':
        command += ['--windowed']
    subprocess.run(command + ['main.py'], cwd=ROOT, check=True)
    folder = ROOT / 'dist' / 'Timetrack'
    shutil.copy2(ROOT / 'README.md', folder / 'README.md')
    shutil.copy2(ROOT / 'VERSION', folder / 'VERSION')
    if system == 'Windows':
        # Ensure double-click launches have a predictable writable working directory.
        (folder / 'Start-Timetrack.cmd').write_text(
            '@echo off\r\ncd /d "%~dp0"\r\nstart "" "%~dp0Timetrack.exe"\r\n', newline='')
        executable = folder / 'Timetrack.exe'
    else:
        launcher = folder / 'start-timetrack.sh'
        launcher.write_text('#!/bin/sh\ncd -- "$(dirname -- "$0")" || exit 1\nexec ./Timetrack "$@"\n')
        launcher.chmod(0o755)
        executable = folder / 'Timetrack'
    if not args.skip_smoke_test:
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([str(executable), '--smoke-test'], cwd=directory,
                           timeout=30, check=True)
    release = ROOT / 'release'
    release.mkdir(exist_ok=True)
    name = f'Timetrack-{version}-{system.lower()}-x86_64'
    archive = Path(shutil.make_archive(str(release / name),
                    'zip' if system == 'Windows' else 'gztar',
                    root_dir=ROOT / 'dist', base_dir='Timetrack'))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + '.sha256').write_bytes(f'{digest}  {archive.name}\n'.encode('utf-8'))
    print(archive)


if __name__ == '__main__':
    main()
