import concurrent.futures
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from jinja2 import ChoiceLoader, FileSystemLoader
from werkzeug.security import generate_password_hash

STAGE=Path(os.getenv('CAMO_FIX_DIR',str(Path(__file__).parent/'camo_fix_stage')))
if not STAGE.exists():
    STAGE=(Path(__file__).resolve().parents[1]/'aclproject2-main'/'aclproject2-main')
sys.path.insert(0,str(STAGE))
spec=importlib.util.spec_from_file_location('camo_lease_backend',STAGE/'app.py')
backend=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=backend
with mock.patch.dict(os.environ,{'ACL_ENABLE_HARDWARE':'0','ACL_USE_IMU':'0'}):
    spec.loader.exec_module(backend)

class CamoLeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        backend.app.config.update(TESTING=True,SECRET_KEY='isolated-lease-tests',
            DATABASE=str(Path(self.tmp.name)/'test.db'),DATABASE_URL=None,HARDWARE_ENABLED=False,
            CSRF_ENABLED=True,SESSION_COOKIE_SECURE=False)
        backend.app.jinja_loader=ChoiceLoader([FileSystemLoader(str(STAGE)),FileSystemLoader(str(Path(__file__).resolve().parents[1]/'aclproject2-main'/'aclproject2-main'))])
        backend._hardware_owner=None;backend._hardware_last_seen=0
        backend._reset_tracking()
        self.env=mock.patch.dict(os.environ,{'ACL_ADMIN_PASSWORD':''});self.env.start()
        backend.init_db()
        conn=backend.connect_db()
        for uid in (1,2):
            conn.execute('INSERT INTO patients(id,username,password,name,age,week) VALUES(?,?,?,?,?,?)',
                (uid,f'patient{uid}',generate_password_hash('test-password',method='pbkdf2:sha256:1000'),f'Patient {uid}',30,2))
            conn.execute('INSERT INTO measurements(patient_id,angle,time,source) VALUES(?,?,?,?)',(uid,uid*30,'2026-10-24 09:00:00','camera'))
        conn.execute('INSERT INTO doctors(id,username,password,name) VALUES(1,?,?,?)',('doctor','unused','Doctor'))
        conn.commit();conn.close()
        self.clients=[self.login(1),self.login(2)]

    def tearDown(self):
        self.env.stop();self.tmp.cleanup()

    def login(self,uid):
        client=backend.app.test_client()
        client.get('/')
        with client.session_transaction() as s:token=s['csrf_token']
        r=client.post('/login/patient',data={'username':f'patient{uid}','password':'test-password','csrf_token':token},
            environ_overrides={'REMOTE_ADDR':f'198.51.100.{uid}'})
        self.assertEqual(r.status_code,302)
        client.get('/dashboard')
        return client

    def post(self,uid,path,data=None):
        client=self.clients[uid-1]
        with client.session_transaction() as s:token=s['csrf_token']
        return client.post(path,json=data or {},headers={'X-CSRF-Token':token},
            environ_overrides={'REMOTE_ADDR':f'198.51.100.{uid}'})

    def test_different_accounts_networks_have_own_identity_and_history(self):
        for uid,client in enumerate(self.clients,1):
            with client.session_transaction() as s:
                self.assertEqual(s['user_id'],uid);self.assertEqual(s['name'],f'Patient {uid}')
            self.assertEqual(client.get('/get_measurements').json[0]['angle'],uid*30)
            self.assertEqual(client.get('/profile').status_code,200)
        self.assertIsNone(backend._hardware_owner)

    def test_opening_and_polling_pages_do_not_claim_hardware(self):
        for client in self.clients:
            for path in ('/get_angle','/get_max_rom','/imu_status','/get_imu_angle','/get_imu_max_rom','/imu_ports'):
                r=client.get(path)
                self.assertEqual(r.status_code,200)
                self.assertFalse(r.json['active'])
                self.assertIsNone(backend._hardware_owner)

    def test_explicit_start_stop_handoff_never_leaks_measurements(self):
        self.assertEqual(self.post(1,'/measurement/start').status_code,200)
        backend.max_rom_session=100;backend.camera_last_data=time.monotonic()
        seen=backend._hardware_last_seen
        r=self.clients[1].get('/get_max_rom')
        self.assertEqual(r.status_code,200);self.assertTrue(r.json['busy']);self.assertEqual(r.json['max_rom'],0)
        self.assertEqual(seen,backend._hardware_last_seen)
        self.assertEqual(self.post(2,'/save_rom/camera').status_code,409)
        self.assertEqual(self.post(2,'/measurement/stop').status_code,200)
        self.assertEqual(backend._hardware_owner,1)
        self.assertEqual(self.post(1,'/save_rom/camera').status_code,200)
        self.assertEqual(self.post(1,'/measurement/stop').status_code,200)
        self.assertEqual(self.post(2,'/measurement/start').status_code,200)
        self.assertEqual(self.clients[1].get('/get_max_rom').json['max_rom'],0)
        backend.max_rom_session=50;backend.camera_last_data=time.monotonic()
        self.assertEqual(self.post(2,'/save_rom/camera').status_code,200)
        conn=backend.connect_db()
        self.assertEqual(conn.execute('SELECT patient_id,angle FROM measurements ORDER BY id DESC LIMIT 2').fetchall(),[(2,50.),(1,100.)])
        conn.close()

    def test_same_numeric_doctor_id_cannot_release_patient_device(self):
        self.post(1,'/measurement/start')
        doctor=backend.app.test_client()
        with doctor.session_transaction() as s:s.update(user_id=1,role='doctor',name='Doctor')
        doctor.get('/logout')
        self.assertEqual(backend._hardware_owner,1)

    def test_competing_start_requests_have_one_owner(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(lambda uid:self.post(uid,'/measurement/start').status_code,(1,2)))
        self.assertEqual(sorted(results),[200,409])
        self.assertIn(backend._hardware_owner,(1,2))

    def test_mutations_require_start_and_expired_lease_does_not_self_reclaim(self):
        self.assertEqual(self.post(1,'/reset_rom/camera').status_code,409)
        self.post(1,'/measurement/start')
        backend._hardware_last_seen-=backend.HARDWARE_LEASE_SECONDS+1
        self.assertFalse(self.clients[0].get('/get_angle').json['active'])
        self.assertEqual(self.post(2,'/measurement/start').status_code,200)

    def test_camera_streams_are_bounded_and_slots_released(self):
        self.post(1,'/measurement/start')
        backend._latest_frame=b'jpeg';backend._frame_number+=1
        with mock.patch.object(backend,'_start_camera',return_value=True):
            first=self.clients[0].get('/video_feed',buffered=False)
            second=self.clients[0].get('/video_feed',buffered=False)
            try:
                self.assertEqual(first.status_code,200);self.assertEqual(second.status_code,200)
                self.assertEqual(self.clients[0].get('/video_feed').status_code,429)
            finally:first.close();second.close()
            third=self.clients[0].get('/video_feed',buffered=False)
            self.assertEqual(third.status_code,200);third.close()

    def test_camo_is_selected_by_name_without_mjpeg_forcing(self):
        cv=mock.Mock();cv.CAP_ANY=0;cv.CAP_DSHOW=700
        capture=cv.VideoCapture.return_value;capture.isOpened.return_value=True;capture.get.return_value=720
        mp=mock.Mock();enumerator=mock.Mock()
        enumerator.enumerate_cameras.return_value=[mock.Mock(name='dummy')]
        device=mock.Mock(index=7,backend=700);device.name='Camo'
        enumerator.enumerate_cameras.return_value=[device]
        modules={'cv2':cv,'mediapipe':mp,'cv2_enumerate_cameras':enumerator}
        with mock.patch.object(backend.importlib,'import_module',side_effect=lambda name:modules[name]), mock.patch.object(backend,'TasksPose') as tasks:
            tasks.return_value = mock.Mock()
            backend._open_camera()
        cv.VideoCapture.assert_called_once_with(7,700)
        cv.VideoWriter_fourcc.assert_not_called()
        backend.camera=backend.pose=None

if __name__=='__main__':unittest.main()
