import copy
import unittest
from gateway import combine
from validation import validate,protect_markup


def match(start,length,rule,replacements=(),message='warning'):
 return dict(offset=start,length=length,message=message,rule={'id':rule,'category':{'id':'LLM' if rule=='LLM_RULE' else 'STYLE'}},replacements=[{'value':x} for x in replacements])


class OverlapTests(unittest.TestCase):
 def test_sentence_warning_does_not_hide_inner_ai(self):
  broad=match(0,80,'TOO_LONG_SENTENCE');inner=match(24,3,'LLM_RULE',['goes'])
  self.assertEqual(combine({'matches':[broad]},{'matches':[inner]})['matches'],[broad,inner])
 def test_distinct_same_span_actions_are_available_to_ignore_individually(self):
  native=match(10,3,'NATIVE',['goes']);ai=match(10,3,'LLM_RULE',['went'])
  self.assertEqual(len(combine({'matches':[native]},{'matches':[ai]})['matches']),2)
 def test_duplicate_action_keeps_native(self):
  native=match(10,3,'NATIVE',['goes']);ai=match(10,3,'LLM_RULE',['goes'])
  self.assertEqual(combine({'matches':[native]},{'matches':[ai]})['matches'],[native])
 def test_ai_correction_validates_inside_broad_warning(self):
  text='She go to school and studies every day.';broad=match(0,len(text),'TOO_LONG_SENTENCE');ai=match(4,2,'LLM_RULE',['goes'])
  def verify(p):
   self.assertEqual(p['text'],'She goes to school and studies every day.')
   return {'matches':[match(0,len(p['text']),'TOO_LONG_SENTENCE')]}
  self.assertEqual(len(validate({'text':text},{'matches':[broad]},{'matches':[ai]},verify)['matches']),1)
 def test_new_native_error_still_rejects_ai(self):
  text='The list is ready.';ai=match(9,2,'LLM_RULE',['are'])
  self.assertEqual(validate({'text':text},{'matches':[]},{'matches':[ai]},lambda p:{'matches':[match(9,3,'AGREEMENT')]}),{'matches':[]})
 def test_markup_still_protected(self):
  p={'data':{'annotation':[{'markup':'[['},{'text':'Target'},{'markup':']]'}, {'text':' She go.'}]}}
  self.assertEqual(protect_markup(p,[match(2,6,'LLM_RULE',['Other'])]),[])
  self.assertEqual(len(protect_markup(p,[match(15,2,'LLM_RULE',['goes'])])),1)

if __name__=='__main__':unittest.main()
