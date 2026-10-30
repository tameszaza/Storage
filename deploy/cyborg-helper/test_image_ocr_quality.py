import unittest
from image_ocr_quality import assess
HEADER='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
def tsv(words):return HEADER+''.join(f'5\t1\t1\t1\t{line}\t{i}\t0\t0\t1\t1\t{conf}\t{text}\n' for i,(line,text,conf) in enumerate(words))
class QualityTests(unittest.TestCase):
 def test_noise_rejected_even_with_one_accidental_word(self):
  r=assess(tsv([(1,'noise',20),(1,'random',30),(2,'Soup',92)]),'noise random Soup')
  self.assertEqual(r['text'],'');self.assertEqual(r['rawText'],'noise random Soup')
 def test_code_and_equation_are_not_dictionary_filtered(self):
  words=[(1,'q[4:1]',91),(1,'<=',44),(1,'{y,q[3:0]};',87),(2,'PV=CF/(1+r)^t',95)]
  self.assertEqual(assess(tsv(words),'raw')['text'],'q[4:1] <= {y,q[3:0]};\nPV=CF/(1+r)^t')
 def test_short_clear_label_kept(self):self.assertEqual(assess(tsv([(1,'5kHz',96)]),'5kHz')['text'],'5kHz')
 def test_weak_line_removed_without_losing_clear_text(self):
  words=[(1,'A clear readable lecture sentence',95),(2,'smudge',10)]
  self.assertEqual(assess(tsv(words),'raw')['text'],'A clear readable lecture sentence')
 def test_empty_and_invalid_confidence_rejected(self):
  for conf in ['nan','inf',-1,101,'broken']:
   self.assertEqual(assess(tsv([(1,'text',conf)]),'raw')['text'],'')
  self.assertEqual(assess(HEADER,'')['text'],'')

class HelperProtocolTests(unittest.TestCase):
 def test_old_unfiltered_helper_is_deferred(self):
  import helper_client as h
  from unittest.mock import patch
  with patch.object(h,'request',return_value={'text':'unfiltered'}):
   with self.assertRaises(h.Paused):h.image('unused.png')
 def test_manual_override_uses_raw_text_only_when_explicit(self):
  import helper_client as h
  from image_ocr_quality import POLICY
  from unittest.mock import patch
  with patch.object(h,'request',return_value={'policy':POLICY,'text':'','rawText':'faint words'}):
   self.assertEqual(h.image('unused.png'),'');self.assertEqual(h.image('unused.png',force=True),'faint words')

if __name__=='__main__':unittest.main()
