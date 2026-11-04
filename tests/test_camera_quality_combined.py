"""Isolated camera fixtures and temporary DB/files; never touches production auth/data."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parent
STAGE = Path(os.getenv('KNEEROM_TEST_APP', str(ROOT/'camera_quality_stage')))
if not STAGE.exists():
    STAGE = (Path(__file__).resolve().parents[1]/'aclproject2-main'/'aclproject2-main')
sys.path.insert(0, str(STAGE))
from camera_quality import CameraQuality
import cv2
import numpy as np
from jinja2 import ChoiceLoader, FileSystemLoader
spec = importlib.util.spec_from_file_location('quality_backend', STAGE/'app.py')
b = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = b
with patch.dict(os.environ, {'ACL_ENABLE_HARDWARE':'0','ACL_USE_IMU':'0', 'ACL_ADMIN_PASSWORD':''}):
    spec.loader.exec_module(b)

def leg():
    return [NS(x=.35,y=.2,visibility=.95), NS(x=.35,y=.5,visibility=.95), NS(x=.55,y=.5,visibility=.95)]

class QualityChecks(unittest.TestCase):
    def test_requires_continuous_visible_leg(self):
        q = CameraQuality()
        self.assertFalse(q.check(leg(), 10, 640,360,128,100)['ready'])
        self.assertFalse(q.check(leg(), 10.2, 640,360,128,100)['ready'])
        self.assertFalse(q.check(leg(), 10.4, 640,360,128,100)['ready'])
        q.check(leg(),10.6,640,360,128,100)
        self.assertTrue(q.check(leg(), 10.8, 640,360,128,100)['ready'])
        q.check(None,10.9,640,360,128,100)
        self.assertFalse(q.check(leg(),11,640,360,128,100)['ready'])

    def test_bad_samples_never_ready(self):
        for change, expected in [(lambda p: setattr(p[0],'visibility',.2),'occluded'),
                                 (lambda p: setattr(p[2],'x',1.1),'cropped'),
                                 (lambda p: setattr(p[1],'x',float('nan')),'invalid'),
                                 (lambda p: setattr(p[2],'x',p[1].x),'geometry')]:
            points = leg(); change(points)
            self.assertEqual(CameraQuality().check(points,10,640,360,128,100)['code'], expected)
        self.assertEqual(CameraQuality().check(leg(),10,640,360,10,100)['code'],'dark')
        self.assertEqual(CameraQuality().check(leg(),10,640,360,250,100)['code'],'bright')
        self.assertEqual(CameraQuality().check(leg(),10,640,360,128,0)['code'],'blur')

    def test_person_jump_rearms_gate(self):
        q = CameraQuality(settle_seconds=0)
        self.assertTrue(q.check(leg(),10,640,360,128,100)['ready'])
        p = leg()
        for point in p: point.x += .2
        self.assertEqual(q.check(p,10.1,640,360,128,100)['code'],'jump')

    def test_long_frame_gap_rearms_gate(self):
        q = CameraQuality()
        for now in (10,10.2,10.4,10.6,10.8): q.check(leg(),now,640,360,128,100)
        self.assertTrue(q.status['ready'])
        self.assertFalse(q.check(leg(),12,640,360,128,100)['ready'])

class FramePipeline(unittest.TestCase):
    def test_valid_peak_jpeg_is_same_frame_and_missing_hip_never_counts(self):
        b._reset_tracking(); b.cam_leg='auto'; b.crowd_mode=False
        b.cv2=cv2
        b.mp_pose=NS(PoseLandmark=NS(**{name:NS(value=i) for i,name in enumerate(['LEFT_HIP','LEFT_KNEE','LEFT_ANKLE','RIGHT_HIP','RIGHT_KNEE','RIGHT_ANKLE'])}))
        points=leg()+leg()
        b.pose=NS(process=lambda rgb:NS(pose_landmarks=NS(landmark=points)))
        frame=np.random.default_rng(42).integers(40,215,(360,640,3),dtype=np.uint8)
        for now in (100,100.2,100.4,100.6,100.8):
            with patch.object(b.time,'monotonic',return_value=now): jpeg=b._process_camera_frame(frame.copy())
        self.assertTrue(b._camera_quality.status['ready'])
        self.assertEqual(b.max_rom_session,90)
        self.assertEqual(b._camera_peak['jpeg'],jpeg)
        self.assertIsNotNone(cv2.imdecode(np.frombuffer(jpeg,dtype=np.uint8),cv2.IMREAD_COLOR))
        previous=b._camera_peak
        points[0].visibility=.1
        with patch.object(b.time,'monotonic',return_value=100.9): b._process_camera_frame(frame.copy())
        self.assertFalse(b._camera_quality.status['ready'])
        self.assertIs(b._camera_peak,previous)
        self.assertEqual(b.camera_last_data,100.8)
        b.pose=None; b.cv2=None

class PairedRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.dir=Path(self.tmp.name)
        b.app.config.update(TESTING=True, SECRET_KEY='temporary-tests-only', DATABASE=str(self.dir/'db.sqlite'),
                            DATABASE_URL=None, HARDWARE_ENABLED=False, CSRF_ENABLED=True, SESSION_COOKIE_SECURE=False)
        b.app.jinja_loader=ChoiceLoader([FileSystemLoader(str(STAGE)),FileSystemLoader(str(Path(__file__).resolve().parents[1]/'aclproject2-main'/'aclproject2-main'))])
        self.env=patch.dict(os.environ,{'ACL_ADMIN_PASSWORD':''}); self.env.start()
        b.CARE_UPLOAD_DIR=self.dir/'images'
        b.USE_IMU=True
        b._hardware_owner=None; b._hardware_last_seen=0; b.cam_leg='auto'; b._reset_tracking()
        b.init_db()
        c=b.connect_db()
        for uid in (1,2): c.execute('INSERT INTO patients(id,username,password,name,age,week) VALUES(?,?,?,?,?,?)',(uid,str(uid),'unused','Test',30,1))
        c.execute('INSERT INTO doctors(id,username,password,name) VALUES(1,?,?,?)',('doctor','unused','Doctor'))
        c.commit();c.close()
        self.client=self.client_for('patient',1)
        self.post('/measurement/start',json={'combined':True})

    def tearDown(self):
        b._reset_tracking(); self.env.stop(); self.tmp.cleanup()

    def client_for(self, role, uid):
        client=b.app.test_client()
        with client.session_transaction() as s:s.update(user_id=uid,role=role,name='Test',csrf_token='test-token')
        return client

    def post(self, url, **kw):
        return self.client.post(url,headers={'X-CSRF-Token':'test-token'},**kw)

    def seed(self):
        b._camera_quality.status={'ready':True,'code':'ready','message':'OK'}
        b.camera_last_data=b.imu_last_data=time.monotonic()
        b.imu_connected=True; b.imu_calibrating=False
        b.max_rom_session=82; b.imu_max_rom=85
        ok,buf=cv2.imencode('.jpg',np.full((64,64,3),100,np.uint8))
        b._camera_peak={'jpeg':buf.tobytes(),'angle':82,'owner':1,'generation':b._hardware_generation,
                        'side':'left','captured_at':'2026-11-04 12:00:00'}

    def counts(self):
        c=b.connect_db()
        result=[c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('measurements','care_updates')]
        c.close();return result

    def test_paired_save_and_private_image_and_doctor_timeline(self):
        self.seed()
        response=self.post('/measurement/save_combined')
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.counts(),[2,1])
        c=b.connect_db()
        rows=c.execute('SELECT patient_id,angle,time,source FROM measurements ORDER BY id').fetchall();c.close()
        self.assertEqual([r[3] for r in rows],['camera','imu'])
        self.assertEqual(rows[0][2],rows[1][2])
        self.assertEqual([r[0] for r in rows],[1,1])
        image=response.json['snapshot_url']
        with self.client.get(image) as response: self.assertEqual(response.status_code,200)
        self.assertEqual(self.client_for('patient',2).get(image).status_code,403)
        doctor=self.client_for('doctor',1)
        with doctor.get(image) as response: self.assertEqual(response.status_code,200)
        html=self.client.get('/care').get_data(as_text=True)
        self.assertIn('82°',html)
        self.assertNotIn('Đau None',html)
        self.assertEqual(len(list(b.CARE_UPLOAD_DIR.glob('*.jpg'))),1)
        self.assertEqual(self.post('/measurement/save_combined').status_code,409)

    def test_invalid_camera_stale_imu_and_calibrating_block_both_writes(self):
        for broken in ('camera','imu','calibrating'):
            self.seed()
            if broken=='camera': b._camera_quality.reject('occluded','Test')
            if broken=='imu': b.imu_last_data-=4
            if broken=='calibrating': b.imu_calibrating=True
            self.assertEqual(self.post('/measurement/save_combined').status_code,409)
            self.assertEqual(self.counts(),[0,0])

    def test_other_patient_cannot_save_or_read_live_data(self):
        self.seed()
        other=self.client_for('patient',2)
        self.assertEqual(other.post('/measurement/save_combined',headers={'X-CSRF-Token':'test-token'}).status_code,409)
        data=other.get('/measurement/status').json
        self.assertFalse(data['active']); self.assertNotIn('camera',data)
        self.assertEqual(self.counts(),[0,0])

    def test_atomic_rollback_if_image_write_fails(self):
        self.seed()
        b.CARE_UPLOAD_DIR.write_text('cannot be a directory')
        with self.assertLogs(b.logger,level='ERROR'):
            response=self.post('/measurement/save_combined')
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.counts(),[0,0])
        self.assertEqual(b.max_rom_session,82)
        self.assertIsNotNone(b._camera_peak)

    def test_atomic_rollback_if_second_measurement_fails(self):
        self.seed()
        original=b.insert_measurement
        def fail_second(conn,pid,angle,when,source):
            if source=='imu': raise b.DatabaseError[0]('simulated')
            return original(conn,pid,angle,when,source)
        with patch.object(b,'insert_measurement',side_effect=fail_second), self.assertLogs(b.logger,level='ERROR'):
            response=self.post('/measurement/save_combined')
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.counts(),[0,0])
        self.assertFalse(b.CARE_UPLOAD_DIR.exists())

    def test_new_owner_and_restart_clear_peak_and_both_maxima(self):
        self.seed()
        self.post('/measurement/start',json={'combined':True})
        self.assertEqual((b.max_rom_session,b.imu_max_rom),(0,0));self.assertIsNone(b._camera_peak)
        self.seed()
        self.post('/measurement/stop')
        other=self.client_for('patient',2)
        other.post('/measurement/start',headers={'X-CSRF-Token':'test-token'},json={'combined':True})
        self.assertIsNone(b._camera_peak)
        self.assertFalse(b._camera_quality.status['ready'])

    def test_status_returns_both_sources_and_render_has_single_save(self):
        self.seed()
        data=self.client.get('/measurement/status').json
        self.assertTrue(data['active']); self.assertTrue(data['camera']['tracking']);self.assertTrue(data['imu']['receiving'])
        html=self.client.get('/dashboard').get_data(as_text=True)
        self.assertIn('finishCombined(this)',html)
        self.assertNotIn('finishCamera(this)',html); self.assertNotIn('finishSensor(this)',html)

    def test_peak_from_other_generation_never_saved(self):
        self.seed(); b._camera_peak['generation']-=1
        with self.assertLogs(b.logger,level='ERROR'):
            response=self.post('/measurement/save_combined')
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.counts(),[0,0])

    def test_csrf_required(self):
        self.seed()
        self.assertEqual(self.client.post('/measurement/save_combined').status_code,400)
        self.assertEqual(self.counts(),[0,0])

if __name__=='__main__': unittest.main(verbosity=2)
