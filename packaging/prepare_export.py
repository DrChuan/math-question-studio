"""Create a private, relocatable Windows build kit without changing the bank."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from problem_manager.image_markup import image_marker, replace_image_code
from problem_manager.repository import QuestionRepository


def prepare_export(destination: Path, data_file: Path):
    if destination.exists():
        raise FileExistsError(f'Will not overwrite existing folder: {destination}')
    if not data_file.is_file():
        raise FileNotFoundError(data_file)
    repository = QuestionRepository(data_file)
    state = repository.load()
    originals = {path for question in state.questions for path in question.all_images}
    for path in originals:
        if not Path(path).is_file():
            raise FileNotFoundError(f'Question image is missing: {path}')
    payload = repository.to_payload(state)
    data = destination / 'data'
    images = data / 'images'
    images.mkdir(parents=True)
    mapping = {}
    for original in sorted(originals):
        source = Path(original)
        target = images / source.name
        if target.exists() and target.read_bytes() != source.read_bytes():
            digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
            target = images / f'{source.stem}-{digest}{source.suffix}'
        shutil.copy2(source, target)
        mapping[original] = target.relative_to(data).as_posix()
    for question in payload['questions']:
        for old in set(question['image_paths'] + question['analysis_image_paths']):
            new = mapping[old]
            if image_marker(old) != image_marker(new):
                question['analysis'] = replace_image_code(question['analysis'], old, new, preserve_width=True)
                widths = question['image_widths']
                if image_marker(old) in widths:
                    widths[image_marker(new)] = widths.pop(image_marker(old))
        question['image_paths'] = [mapping[path] for path in question['image_paths']]
        question['analysis_image_paths'] = [mapping[path] for path in question['analysis_image_paths']]
        question['image_path'] = question['image_paths'][0] if question['image_paths'] else ''
    (data / 'questions.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    cache = data_file.parent / '.formula-cache'
    if cache.is_dir():
        shutil.copytree(cache, data / '.formula-cache', ignore=shutil.ignore_patterns('*.tmp'))
    source = destination / 'source'
    source.mkdir()
    shutil.copy2(ROOT / 'app.py', source / 'app.py')
    shutil.copytree(ROOT / 'problem_manager', source / 'problem_manager', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
    for script in (ROOT / 'packaging' / 'windows').iterdir():
        if script.is_file():
            shutil.copy2(script, destination / script.name)
    manifest = {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'questions': len(state.questions),
        'images': len(list(images.iterdir())),
        'baskets': len(state.baskets),
        'data_sha256': hashlib.sha256((data / 'questions.json').read_bytes()).hexdigest(),
        'contains_exe': False,
        'entry_point': 'build.bat',
    }
    (destination / 'snapshot.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'export')
    parser.add_argument('--data', type=Path, default=ROOT / 'data' / 'questions.json')
    args = parser.parse_args()
    prepare_export(args.output.resolve(), args.data.resolve())
