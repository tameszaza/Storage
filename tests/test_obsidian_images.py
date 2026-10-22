import hashlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from flask import Flask
from PIL import Image

spec = importlib.util.spec_from_file_location('image_route', '/app/routes/obsidian_images.py')
route = importlib.util.module_from_spec(spec)
spec.loader.exec_module(route)

def raster(fmt, mode='RGB', orientation=None):
    image = Image.new(mode, (160,96), (220,50,30,90) if mode=='RGBA' else (220,50,30))
    buffer = io.BytesIO()
    options = {}
    if orientation:
        exif = Image.Exif(); exif[274]=orientation; options['exif']=exif.tobytes()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()

class ImageUploads(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.root=Path(self.directory.name)
        route.ROOT=self.root/'images'; route.ROOT.mkdir()
        route.KEY=self.root/'key'; route.KEY.write_text('fixture-key')
        app=Flask(__name__); app.config['TESTING']=True
        route.register_routes(app); self.client=app.test_client()
    def tearDown(self): self.directory.cleanup()
    def upload(self,data,key='fixture-key'):
        return self.client.post('/obsidian-images/upload',data=data,headers={'Authorization':'Bearer '+key,'Content-Type':'application/octet-stream'})
    def check_upload(self,data,extension,converted):
        result=self.upload(data); self.assertEqual(result.status_code,200,result.data[:250])
        meta=result.get_json(); path=meta['url'].split('/obsidian-images/',1)[1]
        self.assertTrue(path.endswith('.'+extension)); self.assertEqual(meta['converted'],converted)
        self.assertEqual(meta['sourceSha256'],hashlib.sha256(data).hexdigest())
        downloaded=self.client.get('/obsidian-images/'+path)
        self.assertEqual(downloaded.status_code,200)
        self.assertEqual(hashlib.sha256(downloaded.data).hexdigest(),meta['sha256'])
        payload=downloaded.data; downloaded.close()
        with Image.open(io.BytesIO(payload)) as image: image.load()
        if not converted: self.assertEqual(payload,data)
        again=self.upload(data); self.assertEqual(again.get_json()['url'],meta['url'])
        self.assertEqual(len(list(route.ROOT.iterdir())),1)
        return payload
    def test_regular_formats_preserved(self):
        for fmt,extension in [('PNG','png'),('JPEG','jpg'),('GIF','gif'),('WEBP','webp'),('AVIF','avif')]:
            with self.subTest(format=fmt):
                for f in route.ROOT.iterdir(): f.unlink()
                self.check_upload(raster(fmt),extension,False)
    def test_actual_rejected_hdr_jpeg(self):
        path=Path('/fixtures/MPO.jpg')
        if not path.exists(): self.skipTest('private regression fixture not mounted')
        data=path.read_bytes()
        with Image.open(io.BytesIO(data)) as image: self.assertEqual(image.format,'MPO')
        self.check_upload(data,'jpg',False)
    def test_heic_to_jpeg(self):
        output=self.check_upload(raster('HEIF'),'jpg',True)
        with Image.open(io.BytesIO(output)) as image: self.assertEqual(image.size,(160,96))
    def test_heic_alpha_to_png(self):
        output=self.check_upload(raster('HEIF','RGBA'),'png',True)
        with Image.open(io.BytesIO(output)) as image:
            self.assertEqual(image.mode,'RGBA'); self.assertLess(image.getpixel((0,0))[3],255)
    def test_heic_orientation(self):
        output=self.check_upload(raster('HEIF',orientation=6),'jpg',True)
        with Image.open(io.BytesIO(output)) as image: self.assertEqual(image.size,(96,160))
    def test_tiff_orientation(self):
        output=self.check_upload(raster('TIFF',orientation=6),'png',True)
        with Image.open(io.BytesIO(output)) as image: self.assertEqual(image.size,(96,160))
    def test_bmp_lossless_conversion(self):
        output=self.check_upload(raster('BMP'),'png',True)
        with Image.open(io.BytesIO(output)) as image: self.assertEqual(image.getpixel((0,0)),(220,50,30))
    def test_animated_gif_preserved(self):
        buffer=io.BytesIO(); a=Image.new('RGB',(40,40),'red'); b=Image.new('RGB',(40,40),'blue')
        a.save(buffer,format='GIF',save_all=True,append_images=[b],duration=100,loop=0)
        output=self.check_upload(buffer.getvalue(),'gif',False)
        with Image.open(io.BytesIO(output)) as image: self.assertEqual(image.n_frames,2)
    def test_invalid_and_unauthorized(self):
        self.assertEqual(self.upload(b'not an image').status_code,415)
        self.assertEqual(self.upload(raster('PNG'),key='wrong-key').status_code,401)
        self.assertEqual(self.upload(b'').status_code,413)
        self.assertEqual(list(route.ROOT.iterdir()),[])
    def test_missing_storage(self):
        route.ROOT.rmdir()
        self.assertEqual(self.upload(raster('HEIF')).status_code,503)
        self.assertFalse(route.ROOT.exists())
    def test_converted_size_limit(self):
        data=raster('HEIF')
        converted,_,_=route.browser_image(data)
        self.assertGreater(len(converted),len(data))
        limit=route.LIMIT
        try:
            route.LIMIT=(len(data)+len(converted))//2
            self.assertEqual(self.upload(data).status_code,413)
            self.assertEqual(list(route.ROOT.iterdir()),[])
        finally: route.LIMIT=limit
    def test_limit_and_legacy_urls(self):
        limit=route.LIMIT
        try:
            route.LIMIT=50
            self.assertEqual(self.upload(raster('PNG')).status_code,413)
            self.assertEqual(self.upload(raster('HEIF')).status_code,413)
        finally: route.LIMIT=limit
        data=raster('TIFF'); name=hashlib.sha256(data).hexdigest()+'.tiff'; (route.ROOT/name).write_bytes(data)
        response=self.client.get('/obsidian-images/'+name)
        self.assertEqual(response.data,data); response.close()
        (route.ROOT/('a'*64+'.png')).symlink_to(route.KEY)
        self.assertEqual(self.client.get('/obsidian-images/'+'a'*64+'.png').status_code,404)

if __name__=='__main__': unittest.main(verbosity=2)
