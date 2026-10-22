from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lib import lyrics as l


class QualityTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.audio=Path(self.tmp.name)/'Song.opus';self.audio.write_bytes(b'audio')
  self.metadata=l.AudioMetadata('Song','Singer','Album',180)
 def tearDown(self):self.tmp.cleanup()
 def test_credit_only_and_empty_timestamps_are_not_lyrics(self):
  for text in ('[00:00.00-1] 作曲 : Max Martin Lukasz Gottwald Katy Perry','[00:00.00]Composer: A Person','[00:00.00]','[ti:Song]\n[ar:Singer]\n[00:00.00]'):
   self.assertNotEqual(l.lyrics_quality(text),'usable')
 def test_provider_instrumental_notice_is_not_lyrics(self):
  for text in ('[00:00.00] 作曲 : Someone\n[00:05.00]纯音乐，请欣赏','Instrumental','純音樂，請欣賞'):
   self.assertEqual(l.lyrics_quality(text),'instrumental')
 def test_short_real_lyrics_and_non_latin_are_preserved(self):
  for text in ('Tequila!','[00:05.00]Tequila!\n[01:05.00]Tequila!','你好','Love\nLove\nLove','Composer of my dreams, you make me sing'):
   self.assertEqual(l.lyrics_quality(text),'usable')
 def test_label_alone_is_not_a_real_transcription(self):
  self.assertEqual(l.lyrics_quality('[by:Cybrog AI]\n[00:00.00]Transcribed by Cybrog AI'),'empty')
 def test_credit_only_existing_sidecar_no_longer_blocks_fallback(self):
  self.audio.with_suffix('.txt').write_text('[00:00.00-1] 作曲 : Someone')
  self.assertIsNone(l.existing_lyrics(self.audio))
  self.audio.with_suffix('.lrc').write_text('[00:05.00]These are real lyrics')
  self.assertEqual(l.existing_lyrics(self.audio).suffix,'.lrc')
 def test_rejected_primary_provider_falls_through_to_useful_public_source(self):
  candidate={'trackName':'Song','artistName':'Singer','duration':180,'syncedLyrics':'[00:00.00] 作曲 : Someone'}
  second={'trackName':'Song','artistName':'Singer','duration':180,'providerId':'abc'}
  provenance={}
  with patch.object(l,'probe_audio',return_value=self.metadata),patch.object(l,'_search',return_value=[candidate]),patch.object(l,'_search_simpmusic',return_value=[second]),patch.object(l,'_fetch_simpmusic',return_value=('[00:03.00]A real lyric line','')):
   status,path=l.fetch_lyrics(self.audio,provider_order=('lrclib','simpmusic'),provenance=provenance)
  self.assertEqual(status,'fetched_synced');self.assertEqual(provenance['source'],'simpmusic')
  self.assertEqual(provenance['rejected'][0]['source'],'lrclib');self.assertIn('A real lyric',path.read_text())
 def test_provider_instrumental_notice_does_not_prevent_ai_fallback(self):
  c={'trackName':'Song','artistName':'Singer','duration':180,'plainLyrics':'纯音乐，请欣赏'}
  with patch.object(l,'probe_audio',return_value=self.metadata),patch.object(l,'_search',return_value=[c]):
   self.assertEqual(l.fetch_lyrics(self.audio,provider_order=('lrclib',)),('not_found',None))
  self.assertIsNone(l.existing_lyrics(self.audio))
 def test_real_existing_lyrics_do_not_trigger_any_provider_call(self):
  self.audio.with_suffix('.txt').write_text('A genuine lyric line')
  with patch.object(l,'probe_audio',side_effect=AssertionError('must not probe')):
   self.assertEqual(l.fetch_lyrics(self.audio)[0],'existing')
 def test_plain_lyrics_are_usable_when_synced_is_only_credits(self):
  status,p=l._write_lyrics(self.audio,'[00:00.00] 作曲 : Somebody','These are genuine lyrics')
  self.assertEqual(status,'fetched_plain');self.assertEqual(p.suffix,'.txt')
 def test_same_title_by_wrong_artist_is_rejected(self):
  m=l.AudioMetadata('The One that got Away','Dorin','Your fav Coffee Covers',194.54)
  bad={'trackName':'the one that got away','artistName':'LiL InK','duration':213.98912}
  self.assertIsNone(l.choose_candidate([bad],m))
  good={**bad,'artistName':'Dorin','duration':194.531}
  self.assertIs(l.choose_candidate([bad,good],m),good)
 def test_instrumental_health_is_not_an_error_and_tracks_source_version(self):
  import json
  from lib.transcription_store import Store
  root=Path(self.tmp.name);managed=root/'music/Managed Playlists/Collection';managed.mkdir(parents=True)
  audio=managed/'Song.opus';audio.write_bytes(b'audio')
  state=root/'state/playlist_runtime/id/lyrics-state.json';state.parent.mkdir(parents=True);state.write_text('{}')
  manifest=state.parent/'manifest.json';manifest.write_text(json.dumps({'files':{'id':[str(audio)]}}))
  q=Store.initialize(root/'state/transcription',root/'inputs',root/'outputs')
  st=audio.stat();options={'kind':'lyrics','lyrics_source':'Collection/Song.opus','lyrics_signature':[st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns]}
  job=q.create(audio.name,5,'Song',{},internal_options=options)
  with q.transaction() as db:db.execute("UPDATE jobs SET state='completed',stage='Likely instrumental' WHERE id=?",(job['id'],))
  summary=l.summarize_playlist_lyrics(['id'],manifest,managed,state)
  self.assertEqual(summary['instrumental'],1);self.assertEqual(summary['problems'],0)
  audio.write_bytes(b'changed recording')
  self.assertEqual(l.summarize_playlist_lyrics(['id'],manifest,managed,state)['instrumental'],0)
 def test_failed_providers_do_not_create_a_bogus_sidecar(self):
  with patch.object(l,'probe_audio',return_value=self.metadata),patch.object(l,'_search',side_effect=l.LyricsLookupError('unavailable')):
   with self.assertRaises(l.LyricsLookupError):l.fetch_lyrics(self.audio,provider_order=('lrclib',))
  self.assertIsNone(l.existing_lyrics(self.audio))

if __name__=='__main__':unittest.main()
