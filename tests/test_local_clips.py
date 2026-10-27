import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.local_clips import (Candidate, Cancelled, Caption, Cue, ass_text, export_clip,
                                  make_captions, media_duration, parse_srt, refresh_project_srt, run_media,
                                  select_candidates, subtitles_ass, transcribe)


class SelectionTests(unittest.TestCase):
    def test_recognition_options_offsets_cache_and_forced_refresh(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'film.mp4'
            source.write_bytes(b'fake')
            model = root / 'model'
            model.mkdir()
            (model / 'model.bin').write_bytes(b'model')
            word = SimpleNamespace(start=1.2, end=2, word='Точно')
            segment = SimpleNamespace(start=1, end=3, text='Точно', words=[word])
            with patch('faster_whisper.WhisperModel') as factory, patch('services.local_clips.run_media') as media:
                factory.return_value.transcribe.side_effect = lambda *a, **kw: (iter([segment]), SimpleNamespace(duration=4))
                args = (source, model, root / 'cache', threading.Event(), lambda msg: None)
                cues = transcribe(*args, start=10, end=13, audio_index=1)
                self.assertEqual(cues[0].start, 10)
                self.assertAlmostEqual(cues[0].words[0]['start'], 10.2)
                self.assertIn('0:a:1', media.call_args.args[0])
                opts = factory.return_value.transcribe.call_args.kwargs
                self.assertEqual(opts['language'], 'ru')
                self.assertEqual(opts['beam_size'], 5)
                self.assertFalse(opts['condition_on_previous_text'])
                self.assertEqual(transcribe(*args, start=10, end=13, audio_index=1), cues)
                self.assertEqual(factory.call_count, 1)
                transcribe(*args, start=10, end=13, audio_index=1, force=True)
                transcribe(*args, start=10, end=13, audio_index=0)
                transcribe(*args, start=10, end=13, audio_index=0, language='auto')
                self.assertEqual(factory.call_count, 4)
                self.assertIsNone(factory.return_value.transcribe.call_args.kwargs['language'])
                self.assertFalse(list((root / 'cache').glob('speech-*')))

    def test_replacing_srt_refreshes_text_without_moving_scenes(self):
        project = {'cues': [], 'candidates': [{'start': 0, 'end': 2, 'text': 'old'}],
                   'caption_edits': {'0': [{'text': 'stale override'}]}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'edited.srt'
            path.write_text('1\n00:00:00,000 --> 00:00:02,000\nНовый текст\n', encoding='utf-8')
            updated = refresh_project_srt(project, path)
            self.assertEqual(updated['cues'][0]['text'], 'Новый текст')
            self.assertEqual(updated['candidates'][0], {'start': 0, 'end': 2, 'text': 'Новый текст'})
            self.assertEqual(updated['caption_edits'], {})
            self.assertTrue(project['caption_edits'])
            updated['caption_edits']['0'] = [{'text': 'manual correction'}]
            self.assertIs(refresh_project_srt(updated, path), updated)
            path.write_text('1\n00:00:00,000 --> 00:00:02,000\nЕщё правка\n', encoding='utf-8')
            changed = refresh_project_srt(updated, path)
            self.assertEqual(changed['cues'][0]['text'], 'Ещё правка')
            self.assertEqual(changed['caption_edits'], {})
            path.write_text('invalid', encoding='utf-8')
            with self.assertRaises(ValueError):
                refresh_project_srt(changed, path)
            self.assertEqual(changed['cues'][0]['text'], 'Ещё правка')

    def test_manual_captions_override_original_in_both_styles(self):
        for style in ('classic', 'reference'):
            result = subtitles_ass([Cue(0, 2, 'Старый текст')], 0, 2, '', '', style,
                                   [Caption(0, 2, 'Новая фраза')])
            self.assertIn('НОВАЯ ФРАЗА' if style == 'reference' else 'Новая фраза', result)
            self.assertNotIn('Старый текст', result)
            self.assertNotIn('Dialogue:', subtitles_ass([Cue(0, 2, 'Старый текст')],
                                                       0, 2, '', '', style, []))

    def test_srt_bom_multiline_and_bad_timing(self):
        cues = parse_srt('\ufeff1\r\n00:00:01,000 --> 00:00:03,250\r\n<b>Почему?</b>\r\nОтвет.\r\n')
        self.assertEqual(cues, [Cue(1, 3.25, 'Почему? Ответ.')])
        with self.assertRaises(ValueError):
            parse_srt('1\n00:00:03,000 --> 00:00:01,000\nОшибка')

    def test_candidates_do_not_overlap_or_cross_long_silence(self):
        cues = [Cue(i, i+4, 'Почему ты скрываешь правду?') for i in range(0, 200, 5)]
        candidates = select_candidates(cues, 200, 20, 40)
        self.assertTrue(candidates)
        for i, c in enumerate(candidates):
            self.assertLessEqual(c.end-c.start, 40)
            self.assertGreaterEqual(c.start, 0)
            self.assertLessEqual(c.end, 200)
            for other in candidates[i+1:]:
                self.assertTrue(c.end <= other.start or c.start >= other.end)
        self.assertFalse(select_candidates([Cue(1, 4, 'Один'), Cue(60, 63, 'Два')], 100, 20, 40))

    def test_subtitles_are_rebased_clipped_and_escaped(self):
        result = subtitles_ass([Cue(9, 12, '{evil}'), Cue(13, 17, 'Текст'), Cue(20, 22, 'Нет')],
                               10, 15, 'KinoTime', 'Бот')
        self.assertIn('0:00:00.00,0:00:02.00,Sub', result)
        self.assertIn('0:00:03.00,0:00:05.00,Sub', result)
        self.assertNotIn('Нет', result)
        self.assertNotIn('{evil}', result)
        self.assertNotIn('\\pos', ass_text(r'{\pos(1,2)}'))

    def test_reference_captions_use_word_times_and_color_accents(self):
        cues = [Cue(0, 2, 'Почему ты молчишь?', [
            {'start': 0, 'end': .55, 'word': 'Почему'},
            {'start': .55, 'end': .85, 'word': 'ты'},
            {'start': .85, 'end': 2, 'word': 'молчишь?'}])]
        captions = make_captions(cues, 0, 2)
        self.assertEqual(captions[0].start, 0)
        self.assertEqual(captions[0].end, 2)
        self.assertEqual(captions[0].accent, 'молчишь')
        result = subtitles_ass(cues, 0, 2, '', '', 'reference',
                               [Caption(0, 2, 'Почему ты молчишь?', 'молчишь', 'yellow')])
        self.assertIn('SubRef', result)
        self.assertIn(r'{\c&H00FFFF&}МОЛЧИШЬ', result)
        self.assertNotIn('Brand,,', result)


class ExportTests(unittest.TestCase):
    def test_actual_mp4_with_audio_and_without_audio(self):
        cancel = threading.Event()
        with tempfile.TemporaryDirectory(prefix='clips тест ') as folder:
            root = Path(folder)
            for audio in (False, True):
                source = root / f'source_{audio}.mp4'
                args = ['-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=15']
                if audio:
                    args += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100']
                args += ['-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)]
                run_media(args, cancel)
                result = export_clip(source, [Cue(0, 3, 'Проверка субтитров')],
                                     Candidate(.5, 2.5, 50, 'тест', 'тест'), root,
                                     'KinoTime', 'Переходи в бота!', cancel, crop=audio)
                self.assertTrue(result.is_file())
                self.assertAlmostEqual(media_duration(result, cancel), 2, delta=.2)
                metadata = json.loads(result.with_suffix('.json').read_text(encoding='utf-8'))
                self.assertEqual(metadata['candidate']['start'], .5)
                info = run_media(['-i', str(result), '-t', '0', '-f', 'null', '-'], cancel)
                self.assertIn('720x1280', info)
                self.assertEqual('Audio: aac' in info, audio)
                self.assertFalse(list(root.glob('*.partial.mp4')))

    def test_cancellation_removes_partial_export(self):
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.mp4'
            run_media(['-y', '-f', 'lavfi', '-i', 'color=size=320x180:rate=15',
                       '-t', '2', '-c:v', 'libx264', str(source)], cancel)
            cancel.set()
            with self.assertRaises(Cancelled):
                export_clip(source, [], Candidate(0, 2, 0, '', ''), root, 'Test', 'CTA', cancel)
            self.assertFalse(list(root.glob('*.partial.mp4')))
            self.assertFalse(list(root.glob('clip_*.mp4')))

    def test_repeating_banner_is_baked_into_full_video(self):
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, banner = root / 'source.mp4', root / 'banner.mp4'
            run_media(['-y', '-f', 'lavfi', '-i', 'color=c=blue:size=320x180:rate=15',
                       '-t', '3', '-c:v', 'libx264', str(source)], cancel)
            run_media(['-y', '-f', 'lavfi', '-i', 'color=c=red:size=300x100:rate=15',
                       '-t', '1', '-c:v', 'libx264', str(banner)], cancel)
            result = export_clip(source, [], Candidate(0, 3, 0, '', ''), root,
                                 'KinoTime', 'CTA', cancel, banner=banner)
            self.assertAlmostEqual(media_duration(result, cancel), 3, delta=.2)
            self.assertEqual(json.loads(result.with_suffix('.json').read_text(encoding='utf-8'))['banner'],
                             str(banner.resolve()))
            still = root / 'last.png'
            run_media(['-y', '-ss', '2.5', '-i', str(result), '-frames:v', '1', str(still)], cancel)
            from PIL import Image
            with Image.open(still) as picture:
                r, g, b = picture.convert('RGB').getpixel((360, 250))
            self.assertGreater(r, 180)
            self.assertLess(g, 100)
            self.assertLess(b, 100)

    def test_gif_banner_can_loop(self):
        from PIL import Image
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, banner = root / 'source.mp4', root / 'banner.gif'
            run_media(['-y', '-f', 'lavfi', '-i', 'color=c=blue:size=320x180:rate=15',
                       '-t', '2', '-c:v', 'libx264', str(source)], cancel)
            colors = [Image.new('RGB', (300, 100), c) for c in ('red', 'green')]
            colors[0].save(banner, save_all=True, append_images=colors[1:], duration=250, loop=0)
            result = export_clip(source, [], Candidate(0, 2, 0, '', ''), root,
                                 'KinoTime', '', cancel, banner=banner)
            self.assertAlmostEqual(media_duration(result, cancel), 2, delta=.2)

    def test_reference_layout_keeps_wide_frame_and_banner_separate(self):
        from PIL import Image
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, banner = root / 'source.mp4', root / 'banner.mp4'
            run_media(['-y', '-f', 'lavfi', '-i', 'color=c=blue:size=320x180:rate=15',
                       '-t', '2', '-c:v', 'libx264', str(source)], cancel)
            run_media(['-y', '-f', 'lavfi', '-i',
                       'color=c=0x00FF00:size=300x100:rate=15,drawbox=x=20:y=20:w=260:h=60:color=red:t=fill',
                       '-t', '1', '-c:v', 'libx264', str(banner)], cancel)
            result = export_clip(source, [Cue(0, 2, 'Тайна раскрыта')],
                                 Candidate(0, 2, 50, 'test', 'test'), root,
                                 'KinoTime', '', cancel, banner=banner, style='reference',
                                 captions=[Caption(0, 2, 'Тайна раскрыта', 'Тайна')])
            still = root / 'frame.png'
            run_media(['-y', '-ss', '1', '-i', str(result), '-frames:v', '1', str(still)], cancel)
            with Image.open(still) as picture:
                rgb = picture.convert('RGB')
                top = rgb.getpixel((360, 250))
                video = rgb.getpixel((360, 650))
                blank = rgb.getpixel((360, 1100))
                keyed_corner = rgb.getpixel((10, 150))
            self.assertGreater(top[0], 180)   # banner
            self.assertGreater(video[2], 180)  # wide film frame
            self.assertGreater(blank[2], 180)  # film fills the lower background
            self.assertGreater(keyed_corner[2], 180)  # film visible through key
            self.assertLess(keyed_corner[1], 60)  # no green rectangle


if __name__ == '__main__':
    unittest.main()
