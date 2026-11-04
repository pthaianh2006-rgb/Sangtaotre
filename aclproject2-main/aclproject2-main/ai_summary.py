"""Doctor-only summary jobs. No cloud inference and no database writes."""
from collections import OrderedDict
import json
import math
import os
from pathlib import Path
import secrets
import re
import subprocess
import sys
import threading
import time


def summarize_facts(rows):
    facts={'kind':'knee_rom_measurements','sources':{}}
    for source in ('camera','imu'):
        samples=[r for r in rows if r[2]==source and isinstance(r[0],(int,float)) and math.isfinite(r[0])]
        if not samples:
            facts['sources'][source]={'count':0}
            continue
        values=[float(r[0]) for r in samples]
        facts['sources'][source]={'count':len(samples),'latest_deg':values[0],
            'earliest_deg':values[-1],'maximum_deg':max(values),
            'mean_deg':round(sum(values)/len(values),1)}
    return facts


def numbers_match_facts(text,facts):
    allowed={float(value) for source in facts['sources'].values() for value in source.values() if isinstance(value,(int,float))}
    observed=[float(number.replace(',','.')) for number in re.findall(r'\d+(?:[.,]\d+)?',text)]
    return all(number in allowed for number in observed)


class SummaryJobs:
    def __init__(self, worker, root, timeout=90):
        self.worker=Path(worker)
        self.root=Path(root)
        self.timeout=timeout
        self.jobs=OrderedDict()
        self.lock=threading.Lock()
        self.slot=threading.BoundedSemaphore(1)

    def available(self):
        runtime=Path(os.getenv('ACL_AI_RUNTIME',str(self.root/'ai_runtime')))
        model=Path(os.getenv('ACL_LLM_MODEL',str(self.root/'models'/'Qwen3-0.6B.litertlm')))
        return self.worker.is_file() and (runtime/'litert_lm').is_dir() and model.is_file()

    def _prune(self):
        now=time.monotonic()
        for key,job in list(self.jobs.items()):
            if now-job['created']>300 and job['state']!='running': self.jobs.pop(key,None)

    def start(self, doctor_id, patient_id, facts):
        if not self.available(): raise FileNotFoundError('AI runtime/model unavailable')
        if not self.slot.acquire(blocking=False):return None
        job_id=secrets.token_urlsafe(24)
        with self.lock:
            self._prune()
            self.jobs[job_id]={'doctor_id':doctor_id,'patient_id':patient_id,'state':'running','created':time.monotonic()}
        threading.Thread(target=self._run,args=(job_id,facts),daemon=True,name='kneerom-ai').start()
        return job_id

    def _run(self,job_id,facts):
        started=time.monotonic()
        try:
            env=os.environ.copy()
            # Child receives numeric facts; it does not need application credentials.
            for key in ('ACL_SECRET_KEY','ACL_DATABASE_URL','ACL_DATABASE','ACL_ADMIN_PASSWORD'):
                env.pop(key,None)
            env['ACL_AI_ROOT']=str(self.root)
            result=subprocess.run([sys.executable,str(self.worker)],input=json.dumps(facts),capture_output=True,
                                  text=True,encoding='utf-8',errors='replace',timeout=self.timeout,env=env,
                                  creationflags=(subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name=='nt' else 0)
            if result.returncode: raise RuntimeError('Local inference failed')
            output=json.loads(result.stdout)
            text=output.get('text','').strip()
            if not text or len(text)>6000:raise ValueError('Invalid AI result')
            if not numbers_match_facts(text,facts):raise ValueError('AI changed source numbers')
            with self.lock:self.jobs[job_id].update(state='done',text=text,model=output.get('model',''),facts=facts,
                                                 elapsed_seconds=round(time.monotonic()-started,1))
        except subprocess.TimeoutExpired:
            with self.lock:self.jobs[job_id].update(state='error',message='AI chạy quá lâu trên máy chủ. Hãy thử lại sau.')
        except ValueError:
            with self.lock:self.jobs[job_id].update(state='error',message='Bản nháp AI chưa hợp lệ hoặc không khớp số liệu. Vui lòng xem lịch sử đo và thử lại.')
        except Exception:
            with self.lock:self.jobs[job_id].update(state='error',message='Chưa chạy được LiteRT-LM. Dữ liệu đo vẫn được giữ nguyên.')
        finally:self.slot.release()

    def get(self,job_id,doctor_id,patient_id):
        with self.lock:
            self._prune()
            job=self.jobs.get(job_id)
            if not job or job['doctor_id']!=doctor_id or job['patient_id']!=patient_id:return None
            return {key:value for key,value in job.items() if key not in ('doctor_id','patient_id','created')}
