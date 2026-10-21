from __future__ import annotations

import argparse
from datetime import datetime
import importlib.metadata
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import sysconfig
import tempfile


def validate_data(root: Path):
    payload = json.loads((root / 'data' / 'questions.json').read_text(encoding='utf-8'))
    for question in payload['questions']:
        paths = question.get('image_paths', []) + question.get('analysis_image_paths', [])
        if question.get('image_path'):
            paths.append(question['image_path'])
        for path in paths:
            if Path(path).is_absolute() or '..' in Path(path).parts or not (root / 'data' / path).is_file():
                raise RuntimeError(f'Image is not portable or is missing: {path}')
    return payload


def copy_licenses(destination: Path):
    destination.mkdir()
    for package in ('PySide6', 'PySide6_Addons', 'PySide6_Essentials', 'shiboken6', 'python-docx', 'lxml', 'pyinstaller'):
        dist = importlib.metadata.distribution(package)
        for item in dist.files or []:
            if any(word in item.name.lower() for word in ('license', 'copying', 'notice')):
                source = Path(dist.locate_file(item))
                if source.is_file():
                    target = destination / package / str(item).replace('..', '_')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copy2(python_license, destination / 'Python-LICENSE.txt')


def build(root: Path, tex_root: Path):
    if sys.platform != 'win32' or struct.calcsize('P') != 8 or sysconfig.get_platform() != 'win-amd64':
        raise RuntimeError('Build this application with 64-bit Python on Windows.')
    validate_data(root)
    source = root / 'source'
    scratch = Path(tempfile.mkdtemp(prefix='package-', dir=root / '_build'))
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
        '--name', 'MathQuestionManager', '--distpath', str(scratch / 'dist'),
        '--workpath', str(scratch / 'work'), '--specpath', str(scratch),
        '--paths', str(source), '--add-data', f'{source / "problem_manager" / "assets"};problem_manager/assets',
        '--collect-data', 'docx', '--exclude-module', 'tkinter', '--exclude-module', 'matplotlib',
        str(source / 'app.py'),
    ], cwd=source, check=True)
    bundle = scratch / 'dist' / 'MathQuestionManager'
    shutil.copytree(root / 'data', bundle / 'data')
    shutil.copytree(tex_root, bundle / 'runtime' / 'TinyTeX')
    shutil.copy2(root / 'PORTABLE_README.txt', bundle / 'README.txt')
    copy_licenses(bundle / 'licenses')
    # Check the frozen app, not the source environment; the test runs with a clean PATH.
    report = scratch / 'self-test.json'
    executable = bundle / 'MathQuestionManager.exe'
    environment = dict(__import__('os').environ)
    environment['PATH'] = str(Path(environment.get('SystemRoot', r'C:\Windows')) / 'System32')
    subprocess.run([str(executable), '--portable-self-test', str(report)], env=environment, check=True, timeout=180)
    result = json.loads(report.read_text(encoding='utf-8'))
    if not result.get('ok'):
        raise RuntimeError(f'Frozen application check failed: {result}')
    destination = root / 'dist' / 'MathQuestionManager'
    if destination.exists():
        destination = destination.with_name('MathQuestionManager-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    destination.parent.mkdir(exist_ok=True)
    shutil.move(str(bundle), str(destination))
    shutil.copy2(report, destination / 'build-check.json')
    print(f'BUILD READY: {destination}', flush=True)
    print(f'Open {destination / "MathQuestionManager.exe"}. Move the entire folder, not only the exe.', flush=True)


if __name__ == '__main__':
    if '--check-python' in sys.argv:
        valid = (3, 10) <= sys.version_info[:2] <= (3, 13) and sysconfig.get_platform() == 'win-amd64'
        print(int(valid))
        raise SystemExit(0 if valid else 1)
    parser = argparse.ArgumentParser()
    parser.add_argument('--tex-root', type=Path, required=True)
    args = parser.parse_args()
    build(Path(__file__).resolve().parent, args.tex_root)
