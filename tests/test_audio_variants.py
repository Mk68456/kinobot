import contextlib
import io
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from tools import desktop_launcher as launcher
from database.connection import database
from database.migrations import migrate
from webpanel import imports


class AudioLanguageTests(unittest.TestCase):
    def test_language_aliases_and_unknown_metadata(self):
        for tag, expected in [('ru', 'rus'), ('rus', 'rus'), ('en-US', 'eng'),
                              ('ger', 'deu'), ('ja', 'jpn'), ('und', ''), ('', '')]:
            self.assertEqual(launcher.audio_language({'tags': {'LANGUAGE': tag}}), expected)
        self.assertEqual(launcher.audio_language({'tags': {'title': 'Russian studio'}}), '')

    def test_saved_selection_excludes_foreign_track_from_render_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'movie.mkv'
            source.write_bytes(b'source')
            info = {'streams': [
                {'codec_type': 'video', 'width': 1280, 'height': 720},
                {'codec_type': 'audio', 'tags': {'language': 'rus', 'title': 'Studio'}},
                {'codec_type': 'audio', 'tags': {'language': 'eng', 'title': 'Original'}},
                {'codec_type': 'audio', 'tags': {'title': 'Unknown'}},
            ]}
            with patch.object(launcher, 'probe_video', return_value=info):
                choices = launcher.audio_choices(source)
                self.assertEqual([c['language'] for c in choices], ['rus', 'eng', ''])
                for choice in choices:
                    choice['enabled'] = choice['language'] == 'rus'
                launcher.save_audio_choices(source, choices)
                loaded = launcher.audio_choices(source)
                self.assertEqual([c['index'] for c in loaded if c['enabled']], [0])
                plan = launcher.folder_queue_item({'root': directory, 'title': 'Movie'},
                    [{'entry': {'path': source.name}, 'audio': loaded, 'qualities': ['720p']}])
                self.assertEqual([v['voice'] for v in plan['variants']], ['Studio'])
                # New language metadata must survive legacy saved records too.
                self.assertEqual(loaded[1]['language'], 'eng')


class QualitySelectionTests(unittest.TestCase):
    def test_requested_qualities_filter_upscaling_and_same_size_duplicate(self):
        media = {'width': 1280, 'height': 720,
                 'eligible': ['source', '720p', '480p', '360p']}
        self.assertEqual(
            launcher.resolve_quality_selection(media, ['1080p', '720p', '480p']),
            ['720p', '480p'],
        )
        self.assertEqual(
            launcher.resolve_quality_selection(media, ['source', '720p', '480p']),
            ['source', '480p'],
        )
        low = {'width': 738, 'height': 340, 'eligible': ['source']}
        self.assertEqual(launcher.resolve_quality_selection(low, ['480p']), [])

    def test_source_profile_uses_telegram_ceiling_without_bloating_low_resolution(self):
        info = {'streams': [{'codec_type': 'video', 'width': 738, 'height': 340}]}
        profile = launcher._quality_profile('source', info)
        self.assertEqual(profile['limit'], 3.75)
        self.assertLessEqual(profile['video'], 2200)

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
class AudioVariantsTests(unittest.TestCase):
    def test_split_resume_selection_and_single_card(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'films';folder.mkdir()
            source=folder/'Film (2020).mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=size=64x64:rate=5',
                '-f','lavfi','-i','sine=frequency=400','-f','lavfi','-i','sine=frequency=800',
                '-t','0.5','-map','0:v','-map','1:a','-map','2:a','-c:v','libx264','-pix_fmt','yuv420p',
                '-c:a','aac','-metadata:s:a:0','title=Studio A','-metadata:s:a:1','title=Studio B',str(source)],
                check=True,capture_output=True)
            original=source.read_bytes();report=Mock();stop=threading.Event()
            result=launcher.prepare_media(source,report,stop)
            self.assertEqual(len(result['files']),2);self.assertNotIn('selection',result)
            times=[]
            for entry in result['files']:
                path=Path(result['root'])/entry['path'];times.append(path.stat().st_mtime_ns)
                info=launcher.probe_video(path)
                self.assertEqual(sum(s['codec_type']=='audio' for s in info['streams']),1)
                self.assertTrue(launcher.player_compatible(info))
            second=launcher.prepare_media(source,report,stop)
            self.assertEqual(result,second)
            self.assertEqual(times,[(Path(result['root'])/e['path']).stat().st_mtime_ns for e in result['files']])
            database.open(':memory:')
            try:
                with contextlib.redirect_stdout(io.StringIO()):migrate()
                draft=imports.create(99,result)
                self.assertEqual(len(draft['body']['items']),2)
                self.assertEqual(imports.create(99,result)['id'],draft['id'])
            finally:database.close()
            choices=launcher.audio_choices(source);choices[1]['enabled']=False;choices[0]['name']='Chosen studio'
            launcher.save_audio_choices(source,choices)
            selected=launcher.prepare_media(source,report,stop)
            self.assertEqual(len(selected['files']),1)
            self.assertIn('Chosen studio',selected['files'][0]['path'])
            self.assertEqual(source.read_bytes(),original)
            stop.set()
            with self.assertRaises((ValueError,InterruptedError)):launcher.prepare_media(source,report,stop)

    def test_two_qualities_and_two_audio_tracks_make_nested_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'films'; folder.mkdir()
            source = folder / 'Movie (2026).mkv'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                'color=size=1280x720:rate=5', '-f', 'lavfi', '-i', 'sine=frequency=440',
                '-f', 'lavfi', '-i', 'sine=frequency=880', '-t', '0.4', '-map', '0:v',
                '-map', '1:a', '-map', '2:a', '-c:v', 'libx264', '-preset', 'ultrafast',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-metadata:s:a:0', 'title=One',
                '-metadata:s:a:1', 'title=Two', str(source)], check=True, capture_output=True)
            launcher.save_media_choices(source, ['720p', '480p'], 'fast')
            report = Mock()
            result = launcher.prepare_media(source, report, threading.Event())
            self.assertEqual(len(result['files']), 4)
            self.assertEqual({Path(item['path']).parts[0] for item in result['files']},
                             {'720p', '480p'})
            for item in result['files']:
                info = launcher.probe_video(Path(result['root']) / item['path'])
                video = next(stream for stream in info['streams'] if stream['codec_type'] == 'video')
                self.assertLessEqual(int(video['width']), 1280)
                self.assertLessEqual(int(video['height']), 720)
                self.assertEqual(sum(s['codec_type'] == 'audio' for s in info['streams']), 1)
                self.assertTrue(launcher.player_compatible(info))
            statuses = [call.args[1] for call in report.variant.call_args_list]
            self.assertTrue(any('%' in status for status in statuses), statuses)

    def test_small_source_is_never_upscaled(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'small.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                'color=size=738x340:rate=5', '-f', 'lavfi', '-i', 'sine=frequency=440',
                '-t', '0.3', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', str(source)], check=True, capture_output=True)
            choice = launcher.quality_choices(source)
            self.assertNotIn('480p', choice['eligible'])
            result = launcher.prepare_media(source, Mock(), threading.Event())
            info = launcher.probe_video(Path(result['root']) / result['files'][0]['path'])
            video = next(stream for stream in info['streams'] if stream['codec_type'] == 'video')
            self.assertLessEqual(int(video['width']), 738)
            self.assertLessEqual(int(video['height']), 340)

class VariantTransferTests(unittest.TestCase):
    def test_rejected_variant_does_not_block_next(self):
        from unittest.mock import Mock
        import json
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);files=[];items=[]
            for index in range(2):
                p=root/f'{index+1:02d} - studio{index}.mp4';p.write_bytes(b'x')
                files.append({'path':p.name,'size':1,'mtime':str(p.stat().st_mtime_ns)})
                items.append({'asset_id':str(index),'relative_path':p.name})
            client=Mock();calls=iter([
                {'draft_status':'draft','files':[{'id':'0','status':'waiting_telegram'}]},
                {'draft_status':'draft','files':[{'id':'0','status':'waiting_telegram','error':'size mismatch'}]},
                {'draft_status':'draft','files':[{'id':'1','status':'waiting_telegram'}]},
                {'draft_status':'draft','files':[{'id':'1','status':'ready'}]}])
            client.request.side_effect=lambda *args: next(calls)
            stop=Mock();stop.is_set.return_value=False;stop.wait.return_value=False
            desktop=Mock();desktop.send_file.side_effect=lambda *a,**kw:kw['before_send']()
            with self.assertRaises(launcher.FileRejectedError):
                launcher.transfer(client,{'draft':{'id':'draft','body':{'items':items}}},
                    {'root':directory,'files':files},desktop,stop,Mock())
            self.assertEqual(desktop.send_file.call_count,2)
            journal=json.loads((root/'.kinotime-transfer.json').read_text())
            self.assertNotIn('0',journal);self.assertEqual(journal['1'],'confirmed')
