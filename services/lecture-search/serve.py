#!/usr/bin/env python3
"""One systemd-accepted HTTP connection; exit after response, no idle model."""
import json, os, socket, sys
from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace
import search

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def reply(self,code,value):
        data=json.dumps(value,ensure_ascii=False).encode()
        self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)));self.send_header('Connection','close')
        self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data);self.close_connection=True
    def do_GET(self):
        if self.path!='/status':return self.reply(404,{'error':'Not found'})
        try:self.reply(200,search.status())
        except Exception:self.reply(503,{'error':'Index/NAS unavailable'})
    def do_POST(self):
        if self.path not in ('/search','/read','/image','/image-text'):return self.reply(404,{'error':'Not found'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=8192:raise ValueError('Invalid request size')
            body=json.loads(self.rfile.read(length))
            result=search.image_text(body) if self.path=='/image-text' else search.query(body) if self.path=='/search' else search.read_pages(body) if self.path=='/read' else search.page_image(body)
            self.reply(200,result)
        except (ValueError,TypeError):self.reply(400,{'error':'Invalid search request'})
        except RuntimeError as e:self.reply(503,{'error':str(e)})
        except Exception:self.reply(503,{'error':'Search unavailable; use direct file reading'})

if __name__=='__main__':
    sock=socket.socket(fileno=0);sock.settimeout(int(os.environ.get('TAMES_SEARCH_TIMEOUT','90')))
    Handler(sock,('127.0.0.1',0),SimpleNamespace(server_name='lecture-search',server_port=8095))
    sock.close()
