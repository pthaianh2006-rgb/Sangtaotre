"""Install the optional LM runtime separately and verify public model downloads."""
from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT=Path(r'D:\bme')
MODELS=ROOT/'models'
RUNTIME=ROOT/'ai_runtime'
MODELS.mkdir(exist_ok=True)
RUNTIME.mkdir(exist_ok=True)
assert shutil.disk_usage(ROOT).free > 3*2**30, 'Need at least 3 GiB free on D'

def download(url, target, size=None, sha=None):
    if target.exists() and (not size or target.stat().st_size==size):
        actual=hashlib.sha256(target.read_bytes()).hexdigest()
        if not sha or actual==sha:
            return {'file':str(target),'sha256':actual,'bytes':target.stat().st_size}
    partial=target.with_suffix(target.suffix+'.partial')
    digest=hashlib.sha256(); md5=hashlib.md5();count=0;last=time.monotonic()
    try:
        with urllib.request.urlopen(url,timeout=45) as response, partial.open('wb') as output:
            expected=int(response.headers.get('Content-Length','0')) or size
            hashes=response.headers.get('x-goog-hash','')
            for chunk in iter(lambda:response.read(1024*1024),b''):
                output.write(chunk);digest.update(chunk);md5.update(chunk);count+=len(chunk)
                if time.monotonic()-last>15:
                    print(target.name,round(count/2**20),'MiB',flush=True);last=time.monotonic()
        if expected and count!=expected:raise ValueError('Incomplete model download')
        if sha and digest.hexdigest()!=sha:raise ValueError('Model SHA-256 mismatch')
        goog_md5=next((part.strip()[4:] for part in hashes.split(',') if part.strip().startswith('md5=')),None)
        if goog_md5 and base64.b64encode(md5.digest()).decode()!=goog_md5:raise ValueError('Google model MD5 mismatch')
        partial.replace(target)
        return {'url':url,'file':str(target),'sha256':digest.hexdigest(),'bytes':count}
    finally:partial.unlink(missing_ok=True)

def install():
    env=os.environ.copy()
    tmp=RUNTIME/'tmp';tmp.mkdir(exist_ok=True)
    env.update(TMP=str(tmp),TEMP=str(tmp),PIP_DISABLE_PIP_VERSION_CHECK='1')
    subprocess.run([sys.executable,'-m','pip','install','--no-cache-dir','--no-deps','--target',str(RUNTIME),'litert-lm-api==0.19.0'],env=env,check=True)
    return {'runtime':'litert-lm-api==0.19.0','path':str(RUNTIME)}

with ThreadPoolExecutor(max_workers=3) as pool:
    jobs=[pool.submit(install),
          pool.submit(download,'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task',MODELS/'pose_landmarker_full.task'),
          pool.submit(download,'https://huggingface.co/litert-community/Qwen3-0.6B/resolve/722a0a54c1301a4b07a9856a9590d56362f0969b/Qwen3-0.6B.litertlm',MODELS/'Qwen3-0.6B.litertlm',613722264,'c5f39b4191926786c3e6668b62af4d6eb15248dfc7a5348252a9b406f33f2108')]
    results=[job.result() for job in jobs]
(MODELS/'tasks-litert-assets.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
print('Models and runtime ready',flush=True)
