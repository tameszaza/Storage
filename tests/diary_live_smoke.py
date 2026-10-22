"""Opt-in provider test: synthetic image only; diary writer is replaced in memory."""
import io
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import Flask, g
from PIL import Image, ImageDraw, ImageFont
from lib.config import Config
from lib.ai_client import ask_image, initial_history
from lib.assistant_diary import build_diary_tools


def main():
    image = Image.new('RGB', (900, 650), 'white')
    ImageDraw.Draw(image).multiline_text((70, 60), 'CAFE\nTea       $3.00\nCake      $4.50\nTOTAL     $7.50',
                                       fill='black', font=ImageFont.load_default(size=42), spacing=25)
    buffer = io.BytesIO(); image.save(buffer, 'PNG')
    app = Flask(__name__); app.config.from_object(Config)
    scenarios = [('Log this for me. Record the visible items and total.', True),
                 ('Analyse this picture for me.', True),
                 ('Only analyse this picture here in chat. Do not log or save anything.', False)]
    for message, should_save in scenarios:
        calls = []
        def capture(username, title, markdown):
            calls.append((title, markdown))
            return dict(success=True, path='Diary/2026-10-22.md', status='saved',
                        timestamp='2026-10-22T12:30:00+08:00')
        with app.test_request_context('/'):
            g.diary_context = dict(request_id='synthetic', clock={'today':'2026-10-22','local_datetime':'2026-10-22T12:30:00+08:00'})
            with patch('lib.ai_client.build_workspace_tools', side_effect=lambda *a, **k: build_diary_tools('Admin')), \
                 patch('lib.assistant_diary.append_today_diary', side_effect=capture):
                reply, _ = ask_image(initial_history('Admin'), buffer.getvalue(), 'Describe this image.', message,
                                     username='Admin', extra_context='This is a synthetic receipt used for testing.')
        assert bool(calls) == should_save, (message, len(calls), reply[:200])
        if should_save:
            assert len(calls) == 1 and '7.50' in calls[0][1], (message, calls)
        else:
            assert '7.50' in reply
        print('PASS provider image intent:', 'saved once' if should_save else 'chat-only, no write')


if __name__ == '__main__': main()
