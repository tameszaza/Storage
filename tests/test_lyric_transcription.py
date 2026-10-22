import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lib.transcription_store import Store, QueueError, ARTIFACTS, digest
from lib.lyrics import AudioMetadata, probe_audio
from lib.lyric_transcription import enqueue_playlist, publish_ready, SAVING, source_path


class LyricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name)
        self.music=self.base/'music'; self.folder=self.music/'Managed Playlists'/'Collection'
        self.folder.mkdir(parents=True); self.audio=self.folder/'Song [abcdefghijk].opus'
        self.audio.write_bytes(b'original music')
        self.manifest=self.base/'manifest.json'; self.manifest.write_text(json.dumps({'files':{'abcdefghijk':[str(self.audio)]}}))
        self.state=self.base/'lyrics-state.json'; self.state.write_text('{}')
        self.queue=Store.initialize(self.base/'state',self.base/'inputs',self.base/'outputs')
        self.space=patch('lib.transcription_store.shutil.disk_usage',return_value=SimpleNamespace(free=10*1024**3)); self.space.start()
        self.metadata=patch('lib.lyric_transcription.probe_audio',return_value=AudioMetadata('Song','Singer','Album',60));self.metadata.start()

    def tearDown(self): self.metadata.stop();self.space.stop();self.tmp.cleanup()

    def enqueue(self,item=None):
        return enqueue_playlist(item or {'id':'pop','name':'pop'},['abcdefghijk'],self.manifest,self.folder,self.state,music_root=self.music,queue=self.queue)

    def ready(self, instrumental=False):
        self.enqueue();job=self.queue.claim()
        for name in ARTIFACTS:
            path=self.queue.work_dir(job['id'])/(name+'.ready')
            path.write_bytes((b'[Cybrog AI: likely instrumental; no reliable singing recognized.]\n' if instrumental else b'[by:Automatic local transcription]\n[00:01.23]These are recognized words.\n') if name=='transcript' else b'fixture')
            self.queue.artifact_saved(job['id'],job['lease'],name,digest(path),path.stat().st_size)
        self.queue.complete(job['id'],job['lease']);return job

    def test_durable_deduplication_and_no_source_deletion(self):
        self.assertEqual(self.enqueue()['queued'],1)
        self.assertEqual(self.enqueue()['already_queued'],1)
        self.assertTrue(self.queue.pending());self.assertTrue(self.audio.is_file())
        recreated=Store(self.queue.root,self.queue.inputs,self.queue.outputs)
        job=recreated.claim();self.assertEqual(job['options']['kind'],'lyrics')
        self.assertEqual(job['options']['language'],'auto');self.assertFalse(job['options']['speakers']);self.assertFalse(job['options']['llm'])
        recreated.pause(job['id'],job['lease'],'Server tasks off')
        self.assertEqual(recreated.get(job['id'])['state'],'queued')
        self.assertTrue(recreated.input_path(job).is_file())
        self.assertEqual(self.audio.read_bytes(),b'original music')

    def test_thai_characters_excluded_but_playlist_name_not_a_rule(self):
        self.metadata.stop()
        with patch('lib.lyric_transcription.probe_audio',return_value=AudioMetadata('English title','Singer','Album',60,False,True)):
            self.assertEqual(self.enqueue()['excluded'],1)
        self.metadata.start()
        self.assertEqual(self.enqueue({'id':'086a54c51803a3b8','name':'Thai'})['queued'],1)

    def test_probe_detects_thai_in_other_tags_and_embedded_lyrics(self):
        payload={'format':{'duration':'60','tags':{'title':'English','composer':'นักร้อง','lyrics':'existing words'}}}
        with patch('lib.lyrics.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(payload))):
            metadata=probe_audio(self.audio)
        self.assertTrue(metadata.thai_text);self.assertTrue(metadata.embedded_lyrics)

    def test_existing_and_instrumental_are_skipped(self):
        self.audio.with_suffix('.txt').write_text('existing lyrics')
        self.assertEqual(self.enqueue()['existing'],1);self.audio.with_suffix('.txt').unlink()
        self.state.write_text(json.dumps({'tracks':{self.audio.name:{'status':'instrumental'}}}))
        self.assertEqual(self.enqueue()['queued'],1) # Public instrumental notices alone do not bypass AI verification.
        self.state.write_text('{}')
        with self.queue.connect() as db: ids=[r['id'] for r in db.execute('SELECT id FROM jobs')]
        for ident in ids:self.queue.action(ident,'cancel');self.queue.action(ident,'remove')
        self.metadata.stop()
        with patch('lib.lyric_transcription.probe_audio',return_value=AudioMetadata('Song','Singer','Album',60,True)):
            self.assertEqual(self.enqueue()['existing'],1)
        self.metadata.start()

    def test_complete_keeps_wake_until_published_and_cleans_snapshot_only(self):
        job=self.ready();self.assertEqual(self.queue.get(job['id'])['stage'],SAVING)
        self.assertTrue(self.queue.pending());self.assertEqual(list(self.queue.outputs.iterdir()),[])
        snapshot=self.queue.input_path(job);self.assertTrue(snapshot.is_file())
        self.assertEqual(publish_ready(self.queue,self.music),1)
        self.assertTrue(self.audio.with_suffix('.lrc').read_text().startswith('[by:'))
        self.assertEqual(self.audio.read_bytes(),b'original music');self.assertFalse(snapshot.exists())
        self.assertFalse(self.queue.pending());self.assertTrue((self.queue.work_dir(job['id'])/'details.ready').is_file())

    def test_instrumental_classification_completes_without_fake_lyrics(self):
        job=self.ready(instrumental=True);snapshot=self.queue.input_path(job)
        self.assertEqual(publish_ready(self.queue,self.music),0)
        row=self.queue.get(job['id'])
        self.assertEqual(row['state'],'completed');self.assertEqual(row['stage'],'Likely instrumental')
        self.assertFalse(self.audio.with_suffix('.lrc').exists());self.assertFalse(snapshot.exists())
        self.assertEqual(self.audio.read_bytes(),b'original music')
        self.assertEqual(self.enqueue()['instrumental'],1)

    def test_source_changed_or_removed_does_not_get_stale_lyrics(self):
        job=self.ready();self.audio.write_bytes(b'changed version')
        self.assertEqual(publish_ready(self.queue,self.music),0)
        self.assertFalse(self.audio.with_suffix('.lrc').exists());self.assertFalse(self.queue.pending())
        self.assertIn('changed',self.queue.get(job['id'])['stage'])

    def test_source_removed_is_not_recreated(self):
        self.ready();self.audio.unlink();self.assertEqual(publish_ready(self.queue,self.music),0)
        self.assertFalse(self.audio.exists());self.assertFalse(self.queue.pending())

    def test_new_provider_lyrics_are_not_overwritten(self):
        self.ready();target=self.audio.with_suffix('.lrc');target.write_text('provider lyrics')
        self.assertEqual(publish_ready(self.queue,self.music),0);self.assertEqual(target.read_text(),'provider lyrics')

    def test_publication_recovery_and_non_overwrite_race(self):
        job=self.ready();target=self.audio.with_suffix('.lrc')
        def race(*args): target.write_text('new provider lyrics');raise FileExistsError()
        with patch('lib.lyric_transcription.os.link',side_effect=race):
            self.assertEqual(publish_ready(self.queue,self.music),0)
        self.assertEqual(target.read_text(),'new provider lyrics')
        with self.queue.transaction() as db:db.execute('UPDATE jobs SET stage=? WHERE id=?',(SAVING,job['id']))
        self.assertEqual(publish_ready(self.queue,self.music),0);self.assertFalse(self.queue.pending())

    def test_cancel_remove_preserves_music_and_rejects_path_escape(self):
        self.enqueue();job=self.queue.claim();self.queue.action(job['id'],'cancel');self.queue.action(job['id'],'remove')
        self.assertTrue(self.audio.is_file())
        with self.assertRaises(QueueError):source_path({'lyrics_source':'../../outside.opus'},self.music)
        alias=self.folder/'alias.opus';alias.symlink_to(self.audio)
        with self.assertRaises(QueueError):source_path({'lyrics_source':'Collection/alias.opus'},self.music)


if __name__=='__main__':unittest.main()
