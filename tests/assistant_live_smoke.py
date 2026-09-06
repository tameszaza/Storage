"""Opt-in real-provider smoke test; all tool data stays in a temporary directory.

Run from an environment with the deployed Gemini configuration:
    python tests/assistant_live_smoke.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
from flask import Flask, session, g
from lib.config import Config
from lib.ai_client import ask_text, initial_history, build_ai_context
from lib.planner import list_items
from docx import Document


def main():
    with tempfile.TemporaryDirectory(prefix='assistant-smoke-') as temporary:
        root = Path(temporary)
        (root/'uploads/qa').mkdir(parents=True)
        doc = Document()
        doc.add_paragraph('The project is called Cedar. Its deadline is October 12. The approved budget is 420 dollars.')
        doc.save(root/'uploads/qa/brief.docx')
        app = Flask(__name__)
        app.config.from_object(Config)
        app.config.update(UPLOAD_FOLDER=str(root/'uploads'), PLANNER_DATA_FILE=str(root/'planner.json'),
            ACTIVITY_FILE=str(root/'activity.json'), ICS_CALENDAR_CONFIG_PATH=str(root/'ics.json'),
            ICS_CALENDAR_CACHE_FILE=str(root/'ics-cache.json'))
        with app.test_request_context('/'):
            session.update(logged_in=True, username='qa')
            prompt = 'Create a task called Cedar review with no due date, then rename it to Cedar final review and mark it complete. Read back the task to verify. Also read brief.docx and summarize its project, deadline and budget. Do these actions now.'
            answer, _ = ask_text(initial_history('qa'), prompt, username='qa', extra_context=build_ai_context('qa'))
            tasks = list_items('qa')['todos']
            assert len(tasks) == 1 and tasks[0]['title'] == 'Cedar final review' and tasks[0]['completed'], tasks
            assert '420' in answer and '12' in answer, answer
            print('PASS: real AI created, edited and completed a task, read DOCX, and summarized verified contents.')
            print('Verified actions:', [a['action'] for a in getattr(g, 'assistant_actions', [])])


if __name__ == '__main__': main()
