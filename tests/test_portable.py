from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from problem_manager.image_markup import image_code, image_marker, split_image_content
from problem_manager.models import AppState, Question, QuestionBasket
from problem_manager.repository import QuestionRepository
from problem_manager.runtime import application_directory, configure_runtime


class PortableDataTest(unittest.TestCase):
    def test_relative_images_survive_a_move_and_save(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / 'Mac copy' / 'data'
            (old / 'images').mkdir(parents=True)
            image = old / 'images' / 'geometry.png'
            image.write_bytes(b'test image')
            q = Question(stem='test', image_paths=[str(image)], analysis_image_paths=[str(image)],
                         analysis='before\n' + image_code(str(image), 47) + '\nafter', image_widths={image_marker(str(image)): 47})
            state = AppState(questions=[q], baskets=[QuestionBasket(name='my basket', question_ids=[q.id])])
            repo = QuestionRepository(old / 'questions.json')
            repo.save(state)
            saved = json.loads(repo.data_file.read_text())
            self.assertEqual(saved['questions'][0]['image_paths'], ['images/geometry.png'])
            moved = root / 'Windows copy' / 'data'
            shutil.copytree(old, moved)
            shutil.rmtree(old)
            loaded = QuestionRepository(moved / 'questions.json').load()
            mq = loaded.questions[0]
            self.assertEqual(mq.question_images, [str((moved / 'images' / 'geometry.png').resolve())])
            self.assertTrue(all(Path(path).exists() for path in mq.all_images))
            self.assertEqual([part[0] for part in split_image_content(mq.analysis, mq.analysis_image_paths)], ['text', 'image', 'text'])
            self.assertEqual(mq.image_widths, q.image_widths)
            self.assertEqual(loaded.baskets[0].question_ids, [q.id])
            QuestionRepository(moved / 'questions.json').save(loaded)
            self.assertNotIn(str(root), (moved / 'questions.json').read_text())

    def test_frozen_root_is_beside_exe_not_internal(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', '/portable/app/Manager.exe'):
            self.assertEqual(application_directory(), Path('/portable/app'))

    def test_private_tex_path_does_not_require_system_configuration(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'PATH': '/system/bin'}):
            root = Path(directory)
            tex = root / 'runtime' / 'TinyTeX' / 'bin' / 'windows'
            tex.mkdir(parents=True)
            configure_runtime(root)
            self.assertEqual(os.environ['PATH'], str(tex) + os.pathsep + '/system/bin')

    def test_snapshot_excludes_machine_configuration_and_does_not_touch_original(self):
        script = Path(__file__).resolve().parents[1] / 'packaging' / 'prepare_export.py'
        spec = importlib.util.spec_from_file_location('prepare_export', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source-data' / 'questions.json'
            image = source.parent / 'images' / 'drawing.png'
            image.parent.mkdir(parents=True)
            image.write_bytes(b'picture')
            repo = QuestionRepository(source)
            repo.save(AppState(questions=[Question(stem='test', image_paths=[str(image)])], vmware_profile={'private': 'do not export'}))
            original = source.read_bytes()
            output = root / 'export'
            module.prepare_export(output, source)
            self.assertEqual(original, source.read_bytes())
            self.assertNotIn('vmware_profile', json.loads((output / 'data' / 'questions.json').read_text()))
            self.assertTrue((output / 'build.bat').is_file())
            self.assertTrue((output / 'source' / 'problem_manager' / 'assets' / 'checkmark.svg').is_file())
            q = QuestionRepository(output / 'data' / 'questions.json').load().questions[0]
            self.assertTrue(Path(q.question_images[0]).is_file())
            with self.assertRaises(FileExistsError):
                module.prepare_export(output, source)


if __name__ == '__main__':
    unittest.main()
