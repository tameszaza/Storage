import io,tempfile,time,unittest,os
from pathlib import Path
from unittest.mock import patch,Mock
from types import SimpleNamespace
from flask import Flask,g,session
from PIL import Image
from werkzeug.datastructures import FileStorage
from lib.assistant_images import read_uploads,open_images,ImageInputError,MAX_IMAGES

def png(color='red',size=(16,12)):
    b=io.BytesIO();Image.new('RGB',size,color).save(b,'PNG');return b.getvalue()

class UploadTests(unittest.TestCase):
    def test_order_and_legacy_single_image(self):
        files=[FileStorage(io.BytesIO(png(c)),filename=c+'.png') for c in ['red','blue']]
        data=read_uploads(files)
        with open_images(data) as images:self.assertEqual([im.getpixel((0,0)) for im in images],[(255,0,0),(0,0,255)])
        with open_images(png()) as images:self.assertEqual(len(images),1)
    def test_empty_too_many_and_oversize_fail_before_provider(self):
        with self.assertRaises(ImageInputError):read_uploads([FileStorage(io.BytesIO(),filename='empty.png')])
        with self.assertRaises(OverflowError):read_uploads([FileStorage(io.BytesIO(b'x'),filename='a.png')]*(MAX_IMAGES+1))
        with patch('lib.assistant_images.MAX_IMAGE_BYTES',4),patch('lib.assistant_images.MAX_BATCH_BYTES',6):
            with self.assertRaises(OverflowError):read_uploads([FileStorage(io.BytesIO(b'12345'),filename='a.png')])
            with self.assertRaises(OverflowError):read_uploads([FileStorage(io.BytesIO(b'1234'),filename='a.png') for _ in range(2)])
    def test_invalid_image_and_pixel_limit(self):
        with self.assertRaises(ImageInputError):
            with open_images(b'not an image'):pass
        with patch('lib.assistant_images.MAX_IMAGE_PIXELS',20),self.assertRaises(ImageInputError):
            with open_images(png()):pass
    def test_previews_bounded_closed_and_original_unchanged(self):
        data=png(size=(2600,80));original=data
        with open_images(data) as images:
            self.assertLessEqual(max(images[0].size),2048);image=images[0]
        with self.assertRaises(ValueError):image.getpixel((0,0))
        self.assertEqual(data,original)

class RouteTests(unittest.TestCase):
    def setUp(self):
        from routes.ai import chat
        self.app=Flask(__name__);self.app.secret_key='fixture';self.app.add_url_rule('/chat',view_func=chat,methods=['POST']);self.client=self.app.test_client();self.client.environ_base["HTTP_X_WORKSPACE_CLIENT"]="1"
        with self.client.session_transaction() as s:s.update(logged_in=True,username='Admin',assistant_csrf='fixture-token')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app.config.update(WORKSPACE_AGENT_ROOT=Path(self.tmp.name)/'assistant',ICS_CALENDAR_CONFIG_PATH=str(Path(self.tmp.name)/'ics.json'),APP_TIMEZONE='Asia/Singapore')
    def test_batch_is_durably_queued_in_order_without_running_a_model(self):
        from lib.workspace_store import Store
        red,blue=png(),png('blue')
        with patch('lib.ai_client.build_ai_context',return_value=''),patch('lib.workspace_agent.generate') as model:
            response=self.client.post('/chat',data={'msg':'Compare these','csrf':'fixture-token','request_id':'ordered-images','image':[(io.BytesIO(red),'red.png'),(io.BytesIO(blue),'blue.png')]})
        self.assertEqual(response.status_code,200);model.assert_not_called()
        with Store(self.app.config['WORKSPACE_AGENT_ROOT']) as queue:
            row=queue.db.execute('SELECT payload FROM jobs WHERE id=?',(response.json['workspace_job'],)).fetchone()
            self.assertEqual([Path(p).read_bytes() for p in __import__('json').loads(row[0])['images']],[red,blue])
    def test_upload_count_failure_never_calls_provider(self):
        with patch('routes.ai.ask_image') as ask:
            response=self.client.post('/chat',data={'msg':'hi','image':[(io.BytesIO(png()),f'{i}.png') for i in range(9)]})
        self.assertEqual(response.status_code,413);ask.assert_not_called();self.assertIn('8',response.json['response'])
    def test_preview_is_private_csrf_checked_and_never_runs_ai_or_stores_images(self):
        for csrf,code in [('wrong',403),('fixture-token',200)]:
            with patch('routes.ai.ask_image') as ask,patch('routes.obsidian_images.store_image') as store:
                response=self.client.post('/chat',data={'preview':'1','csrf':csrf,'image':(io.BytesIO(png()),'source.png')})
            self.assertEqual(response.status_code,code);ask.assert_not_called();store.assert_not_called()
            if code==200:self.assertEqual(response.mimetype,'image/jpeg');self.assertIn('no-store',response.headers['Cache-Control'])
    def test_heic_can_be_previewed_without_a_model_or_storage(self):
        from pillow_heif import register_heif_opener
        register_heif_opener(thumbnails=False)
        data=io.BytesIO();Image.new('RGB',(16,12),'red').save(data,format='HEIF')
        with patch('routes.ai.ask_image') as ask,patch('routes.obsidian_images.store_image') as store:
            response=self.client.post('/chat',data={'preview':'1','csrf':'fixture-token','image':(io.BytesIO(data.getvalue()),'camera.heic')})
        self.assertEqual(response.status_code,200);ask.assert_not_called();store.assert_not_called()
        with Image.open(io.BytesIO(response.data)) as preview:self.assertEqual(preview.format,'JPEG');self.assertEqual(preview.size,(16,12))
    def test_read_error_is_json_400_before_queue_or_provider(self):
        with patch('lib.ai_client.build_ai_context',return_value=''),patch('lib.workspace_agent.generate') as model:
            response=self.client.post('/chat',data={'csrf':'fixture-token','request_id':'bad-image','image':(io.BytesIO(b'bad'),'broken.png')})
        self.assertEqual(response.status_code,400);self.assertIn('could not be read',response.json['response']);model.assert_not_called()

class ProviderTests(unittest.TestCase):
    def test_all_images_in_one_provider_call(self):
        from lib.ai_client import ask_image
        app=Flask(__name__);app.config['GEMINI_MODEL']='fixture'
        called=[]
        def generate(**kw):called.append([image.getpixel((0,0)) for image in kw['contents'][1:]]);return SimpleNamespace(text='ok')
        with app.test_request_context('/'),patch('lib.ai_client.get_client',return_value=SimpleNamespace(models=SimpleNamespace(generate_content=generate))):
            result=ask_image([], [png(),png('blue')], 'Compare', allow_tools=False)
        self.assertEqual(result[0],'ok');self.assertEqual(called,[[(255,0,0),(0,0,255)]])
    def test_calendar_keeps_both_images_for_both_checks(self):
        from lib.ai_client import ask_image
        app=Flask(__name__);app.config['GEMINI_MODEL']='fixture';calls=[]
        def generate(**kw):calls.append(len(kw['contents'])-1);return SimpleNamespace(text='{"events":[]}')
        with app.test_request_context('/'),patch('lib.ai_client.get_client',return_value=SimpleNamespace(models=SimpleNamespace(generate_content=generate))),patch('lib.ai_client.calendar_clock',return_value={'timezone':'Asia/Singapore'}):
            ask_image([], [png(),png('blue')], 'Describe', 'Add the dates in these images to my calendar',username='Admin')
        self.assertEqual(calls,[2,2])

class DiaryBatchTests(unittest.TestCase):
    def test_save_uses_all_images_and_returns_ordered_receipt(self):
        from lib import assistant_diary as d
        with tempfile.TemporaryDirectory() as tmp:
            app=Flask(__name__);app.config.update(ASSISTANT_DIARY_STATE=Path(tmp)/'diary.sqlite',ACTIVITY_FILE=str(Path(tmp)/'activity.json'))
            urls=['https://server.tailb7f57c.ts.net/obsidian-images/'+c*64+'.png' for c in ['a','b']]
            with app.test_request_context('/'):
                g.diary_context={'request_id':'multi','clock':{'today':'2026-10-24','local_datetime':'2026-10-24T18:00:00+08:00'},'images_bytes':[b'first',b'second']}
                with patch('routes.obsidian_images.store_image',side_effect=[{'url':u} for u in urls]),patch.object(d,'_publish',return_value={'success':True,'path':'Diary/2026-10-24.md','status':'saved'}) as publish:
                    result=d.append_today_diary('Admin','Photos','Visible facts')
                self.assertEqual(publish.call_args.args[0]['images'],urls);self.assertEqual(result['image_urls'],urls);self.assertEqual(g.assistant_actions[0]['image_urls'],urls)

class CalendarPreviewTests(unittest.TestCase):
    def test_each_preview_account_bounds_and_expiry_cleanup(self):
        from lib import assistant_calendar_drafts as d
        with tempfile.TemporaryDirectory() as tmp:
            app=Flask(__name__);app.config['CALENDAR_IMPORT_DIR']=tmp
            with app.app_context():
                token=d.create_draft('Admin',[{'title':'fixture'}],[],[Image.new('RGB',(10,10),'red'),Image.new('RGB',(10,10),'blue')])
                self.assertEqual(d.load_draft('Admin',token)['image_count'],2)
                self.assertIsNotNone(d.draft_image_path('Admin',token,1));self.assertIsNone(d.draft_image_path('other',token,1))
                for index in ['../file',-1,2]:self.assertIsNone(d.draft_image_path('Admin',token,index))
                old=time.time()-d.DRAFT_LIFETIME_SECONDS-10;os.utime(Path(tmp)/(token+'.json'),(old,old));d._cleanup_expired(Path(tmp))
                self.assertFalse(list(Path(tmp).glob('*.jpg')))

if __name__=='__main__':unittest.main()
