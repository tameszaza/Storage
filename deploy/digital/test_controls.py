import importlib.machinery, importlib.util, unittest
from unittest.mock import patch
from pathlib import Path

def load(name, filename):
    loader=importlib.machinery.SourceFileLoader(name,str(Path(__file__).with_name(filename)))
    spec=importlib.util.spec_from_loader(name,loader)
    result=importlib.util.module_from_spec(spec);loader.exec_module(result);return result

control=load('control','control.py');broker=load('broker','broker.py')

class DigitalControls(unittest.TestCase):
    def test_shared_pause_stops_both_and_records_resume(self):
        from subprocess import CompletedProcess
        with patch.object(control.ROOT.__class__,'is_mount',return_value=True), patch.object(control,'inspect',return_value={'State':{'Running':True}}), patch.object(control,'run',return_value=CompletedProcess([],0,'','')) as run, patch.object(control,'save') as save:
            control.main(['pause'])
        self.assertEqual([c.args[0][-1] for c in run.call_args_list],['cyborg-helper','cyborg-digital'])
        self.assertEqual(save.call_args.args[1],{'helper','digital'})

    def test_digital_start_respects_off(self):
        with patch.object(control,'accepting',return_value=False), patch.object(control,'run') as run:
            with self.assertRaises(RuntimeError):control.main(['start','digital'])
            run.assert_not_called()

    def test_resume_preserves_manual_digital_stop(self):
        with patch.object(control,'accepting',return_value=True), patch.object(control,'ensure_docker'), patch.object(control,'policy',return_value={'digital'}), patch.object(control,'inspect',return_value={'State':{'Running':False}}), patch.object(control,'run') as run:
            run.return_value.returncode=0
            control.main(['resume'])
        self.assertEqual([c.args[0] for c in run.call_args_list if 'start' in c.args[0]],[['/usr/bin/docker','start','cyborg-helper']])

    def test_broker_digital_action_targets_digital(self):
        broker.JOBS['test']={'name':'cyborg-digital','action':'stop'}
        with patch.object(broker,'remote_control') as remote, patch.object(broker,'refresh_remote'), patch.object(broker.time,'sleep'):
            broker._run('test')
        remote.assert_called_once_with('stop digital',timeout=25)
        self.assertEqual(broker.JOBS['test']['state'],'done')

    def test_remote_commands_remain_allowlisted(self):
        with patch('subprocess.run') as run:
            for command in ['exec digital sh','start digital; reboot','delete digital','detail other']:
                with self.assertRaises(broker.ControlError):broker.remote_control(command)
            run.assert_not_called()

    def test_digital_details_do_not_use_helper(self):
        with patch.object(broker,'remote_control',return_value={'state':'running'}) as remote:
            broker.remote_detail('cyborg-digital')
        remote.assert_called_once_with('detail digital',timeout=5)

if __name__=='__main__':unittest.main()
