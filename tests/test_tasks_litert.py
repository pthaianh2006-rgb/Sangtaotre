import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch,Mock

ROOT=Path(__file__).parent
STAGE=Path(os.getenv('KNEEROM_TEST_APP',str(ROOT/'tasks_litert_stage')))
if not STAGE.exists():STAGE=(Path(__file__).resolve().parents[1]/'aclproject2-main'/'aclproject2-main')
sys.path.insert(0,str(STAGE))
from ai_summary import SummaryJobs,summarize_facts,numbers_match_facts
from pose_tasks import TasksPose
from jinja2 import ChoiceLoader,FileSystemLoader
import numpy as np


class TasksAdapter(unittest.TestCase):
    def test_video_timestamp_is_strict_and_reset_discards_tracker(self):
        with tempfile.TemporaryDirectory() as tmp:
            model=Path(tmp)/'test.task';model.write_bytes(b'test-placeholder')
            detector=Mock()
            detector.detect_for_video.return_value=NS(pose_landmarks=[[NS(x=.3,y=.4,visibility=.9)]*33])
            with patch('mediapipe.tasks.python.vision.PoseLandmarker.create_from_options',return_value=detector) as create:
                adapter=TasksPose(model)
                image=np.zeros((32,32,3),dtype=np.uint8)
                with patch('pose_tasks.time.monotonic_ns',side_effect=[10_000_000,10_000_000,9_000_000]):
                    a=adapter.process(image);b=adapter.process(image)
                    adapter.reset();c=adapter.process(image)
                self.assertEqual([a.timestamp_ms,b.timestamp_ms,c.timestamp_ms],[10,11,12])
                self.assertEqual(len(a.pose_landmarks.landmark),33)
                self.assertEqual(create.call_count,2)
                detector.close.assert_called_once()
                adapter.close()

    def test_missing_model_errors_before_camera_inference(self):
        with self.assertRaises(FileNotFoundError):TasksPose(Path(tempfile.gettempdir())/'not-a-real-pose-task.task')


class SummaryService(unittest.TestCase):
    def test_facts_are_numeric_and_do_not_mix_sources(self):
        facts=summarize_facts([(90,'new','camera'),(92,'new','imu'),(70,'old','camera'),(float('nan'),'bad','camera')])
        self.assertEqual(facts['sources']['camera'],dict(count=2,latest_deg=90,earliest_deg=70,maximum_deg=90,mean_deg=80))
        self.assertEqual(facts['sources']['imu']['count'],1)
        self.assertTrue(numbers_match_facts('Camera 90 độ, IMU 92 độ.',facts))
        self.assertFalse(numbers_match_facts('Camera 180 độ.',facts))

    def test_success_scoped_results_no_credentials_and_releases_slot(self):
        jobs=SummaryJobs(STAGE/'litert_worker.py',Path('D:/bme'))
        facts=summarize_facts([(90,'new','camera')])
        result=NS(returncode=0,stdout=json.dumps({'text':'Camera 90 độ.','model':'test'}))
        with patch.object(jobs,'available',return_value=True),patch('ai_summary.subprocess.run',return_value=result) as run,patch.dict(os.environ,{'ACL_SECRET_KEY':'should-not-enter-worker','ACL_DATABASE_URL':'should-not-enter-worker'}):
            job=jobs.start(4,7,facts)
            for _ in range(100):
                state=jobs.get(job,4,7)
                if state['state']!='running':break
                time.sleep(.005)
            self.assertEqual(state['state'],'done')
            self.assertEqual(state['text'],'Camera 90 độ.')
            self.assertIsNone(jobs.get(job,5,7));self.assertIsNone(jobs.get(job,4,8))
            env=run.call_args.kwargs['env']
            self.assertNotIn('ACL_SECRET_KEY',env);self.assertNotIn('ACL_DATABASE_URL',env)
            self.assertEqual(json.loads(run.call_args.kwargs['input']),facts)
            self.assertEqual(run.call_args.kwargs['timeout'],90)
        self.assertTrue(jobs.slot.acquire(blocking=False));jobs.slot.release()

    def test_timeout_and_wrong_numbers_do_not_publish_draft(self):
        for failure in ('timeout','numbers'):
            jobs=SummaryJobs(STAGE/'litert_worker.py',Path('D:/bme'))
            kwargs={'side_effect':subprocess.TimeoutExpired('test',90)} if failure=='timeout' else {'return_value':NS(returncode=0,stdout=json.dumps({'text':'Camera 170 độ.'}))}
            with patch.object(jobs,'available',return_value=True),patch('ai_summary.subprocess.run',**kwargs):
                job=jobs.start(1,1,summarize_facts([(90,'now','camera')]))
                for _ in range(100):
                    state=jobs.get(job,1,1)
                    if state['state']!='running':break
                    time.sleep(.005)
                self.assertEqual(state['state'],'error')
                self.assertNotIn('text',state)
            self.assertTrue(jobs.slot.acquire(blocking=False));jobs.slot.release()

    def test_busy_and_expiry(self):
        jobs=SummaryJobs(STAGE/'litert_worker.py',Path('D:/bme'))
        jobs.slot.acquire()
        with patch.object(jobs,'available',return_value=True):self.assertIsNone(jobs.start(1,1,{}))
        jobs.slot.release()
        jobs.jobs['expired']={'doctor_id':1,'patient_id':1,'state':'done','created':time.monotonic()-301}
        self.assertIsNone(jobs.get('expired',1,1))


class DoctorRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('tasks_ai_backend',STAGE/'app.py')
        cls.b=importlib.util.module_from_spec(spec);sys.modules[spec.name]=cls.b
        with patch.dict(os.environ,{'ACL_ENABLE_HARDWARE':'0','ACL_USE_IMU':'0','ACL_ADMIN_PASSWORD':''}):spec.loader.exec_module(cls.b)
        cls.b.app.jinja_loader=ChoiceLoader([FileSystemLoader(str(STAGE)),FileSystemLoader(str(Path(__file__).resolve().parents[1]/'aclproject2-main'/'aclproject2-main'))])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.b=self.__class__.b
        self.env=patch.dict(os.environ,{'ACL_ADMIN_PASSWORD':''});self.env.start()
        self.b.app.config.update(TESTING=True,SECRET_KEY='isolated-tests',DATABASE=str(Path(self.tmp.name)/'test.db'),DATABASE_URL=None,CSRF_ENABLED=True,HARDWARE_ENABLED=False,SESSION_COOKIE_SECURE=False)
        self.b.init_db()
        c=self.b.connect_db()
        for uid in (1,2):c.execute('INSERT INTO patients(id,username,password,name,age,week) VALUES(?,?,?,?,?,?)',(uid,str(uid),'unused','Patient',20,1))
        c.execute('INSERT INTO doctors(id,username,password,name) VALUES(4,?,?,?)',('doctor','unused','Doctor'))
        c.execute('INSERT INTO measurements(patient_id,angle,time,source) VALUES(1,90,?,?)',('2026-11-04','camera'))
        c.commit();c.close()
        self.client=self.login('doctor',4)
        self.mockjobs=Mock()
        self.mockjobs.start.return_value='job-1'
        self.jobs_patch=patch.object(self.b,'_summary_jobs',self.mockjobs);self.jobs_patch.start()

    def tearDown(self):self.jobs_patch.stop();self.env.stop();self.tmp.cleanup()
    def login(self,role,uid):
        client=self.b.app.test_client()
        with client.session_transaction() as s:s.update(user_id=uid,role=role,csrf_token='token')
        return client
    def post(self,pid=1,client=None):return (client or self.client).post(f'/admin/patient/{pid}/ai-summary',headers={'X-CSRF-Token':'token'})

    def test_doctor_start_has_only_patient_facts_and_does_not_write_records(self):
        r=self.post();self.assertEqual(r.status_code,202)
        doctor,pid,facts=self.mockjobs.start.call_args.args
        self.assertEqual((doctor,pid),(4,1));self.assertEqual(facts['sources']['camera']['count'],1)
        self.assertNotIn('Patient',json.dumps(facts));self.assertNotIn('unused',json.dumps(facts))
        c=self.b.connect_db();self.assertEqual(c.execute('SELECT COUNT(*) FROM measurements').fetchone()[0],1);c.close()
        html=self.client.get('/admin/patient/1').get_data(as_text=True)
        self.assertIn('id="doctorAi"',html);self.assertIn('doctor-ai.js',html)

    def test_patient_and_anonymous_cannot_generate_or_fetch(self):
        patient=self.login('patient',1)
        self.assertEqual(self.post(client=patient).status_code,403)
        self.assertEqual(patient.get('/admin/patient/1/ai-summary/job').status_code,403)
        self.assertEqual(self.b.app.test_client().get('/admin/patient/1/ai-summary/job').status_code,401)
        self.assertEqual(self.client.post('/admin/patient/1/ai-summary').status_code,400)
        self.mockjobs.start.assert_not_called()

    def test_missing_patient_or_data_and_busy(self):
        self.assertEqual(self.post(900).status_code,404)
        self.assertEqual(self.post(2).status_code,409)
        self.mockjobs.start.return_value=None
        self.assertEqual(self.post().status_code,429)
        self.mockjobs.start.side_effect=FileNotFoundError()
        self.assertEqual(self.post().status_code,503)

    def test_status_scopes_doctor_and_patient(self):
        self.mockjobs.get.return_value=None
        self.assertEqual(self.client.get('/admin/patient/1/ai-summary/not-yours').status_code,404)
        self.mockjobs.get.assert_called_with('not-yours',4,1)
        self.mockjobs.get.return_value={'state':'done','text':'Camera 90 độ.'}
        response=self.client.get('/admin/patient/1/ai-summary/own')
        self.assertEqual(response.status_code,200);self.assertEqual(response.json['text'],'Camera 90 độ.')


if __name__=='__main__':unittest.main(verbosity=2)
