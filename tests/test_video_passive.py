import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from lib import video_passive as passive

class PublicDestinations(unittest.TestCase):
    def address(self,ip):
        return (socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443))

    def test_private_and_mixed_answers_are_rejected(self):
        for answers in [[self.address('127.0.0.1')],[self.address('169.254.169.254')],
                        [self.address('93.184.216.34'),self.address('10.1.1.1')]]:
            with self.subTest(answers=answers),patch.object(passive.socket,'getaddrinfo',return_value=answers):
                with self.assertRaises(ValueError):passive.public_addresses('example.test',443)

    def test_public_answer_is_returned_for_pinned_connection(self):
        answers=[self.address('93.184.216.34')]
        with patch.object(passive.socket,'getaddrinfo',return_value=answers):
            self.assertEqual(passive.public_addresses('example.test',443),answers)

    def test_other_ports_are_rejected_before_dns(self):
        with patch.object(passive.socket,'getaddrinfo') as dns:
            with self.assertRaises(ValueError):passive.public_addresses('example.test',8080)
            dns.assert_not_called()

class NetworkLog(unittest.TestCase):
    def test_partial_log_and_matching_http2_headers(self):
        events=[{'params':{'url':'https://cdn.example/video.m3u8?token=test'},'source':{'id':42}},
                {'params':{'headers':[':authority: cdn.example',':path: /video.m3u8?token=test',
                                     'referer: https://vidfast.vc/','origin: https://vidfast.vc',
                                     'user-agent: Chrome test','cookie: do-not-forward']},'source':{'id':9}}]
        encoded=b''.join(json.dumps(e,separators=(',',':')).encode()+b',\n' for e in events)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'log.json'
            path.write_bytes(encoded[:25])
            reader=passive.LogReader();reader.read(path)
            self.assertEqual(reader.candidates('https://vidfast.vc','fallback'),[])
            with path.open('ab') as output:output.write(encoded[25:])
            reader.read(path);reader.read(path)
            result=reader.candidates('https://vidfast.vc','fallback')
            self.assertEqual(len(result),1)
            self.assertEqual(result[0][1]['user-agent'],'Chrome test')
            self.assertNotIn('cookie',result[0][1])
            self.assertNotIn('User-Agent',result[0][1])

    def test_unsupported_sources_do_not_launch_browser(self):
        with patch.object(passive,'browser_executable') as executable:
            for url in ['http://vidfast.pro/tv/1','https://user:password@vidfast.pro/tv/1',
                        'https://vidfast.pro:8443/tv/1','https://localhost/tv/1']:
                with self.subTest(url=url),self.assertRaises(ValueError):passive.discover(url,lambda:None)
            executable.assert_not_called()

if __name__=='__main__':unittest.main()
