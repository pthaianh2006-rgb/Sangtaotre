"""Local-only UI review with disposable sample accounts and hardware disabled."""
import argparse
import importlib.util
import os
from pathlib import Path
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT/'aclproject2-main'/'aclproject2-main'
sys.path.insert(0, str(APP))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5003)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    with tempfile.TemporaryDirectory(prefix='kneerom-ui-preview-') as temporary:
        directory = Path(temporary)
        os.environ.update(ACL_DATABASE=str(directory/'preview.sqlite3'), ACL_DATABASE_URL='',
                          ACL_ENABLE_HARDWARE='0', ACL_USE_IMU='1', ACL_ADMIN_PASSWORD='',
                          ACL_COOKIE_SECURE='0', ACL_SECRET_KEY=secrets.token_hex(32),
                          ACL_CARE_UPLOAD_DIR=str(directory/'images'), ACL_EXPORT_DIR=str(directory/'exports'))
        spec = importlib.util.spec_from_file_location('kneerom_ui_preview', APP/'app.py')
        backend = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = backend
        spec.loader.exec_module(backend)
        backend.app.config['TEMPLATES_AUTO_RELOAD'] = True
        backend.init_db()
        from werkzeug.security import generate_password_hash
        password = generate_password_hash('preview-only-2026')
        conn = backend.connect_db()
        for uid, name, age, week in [(1,'Trần Minh An',25,3),(2,'Lê Hoài Phương',31,2),(3,'Vũ Đức Khang',42,4),(4,'Ngô Khánh Vy',28,1)]:
            conn.execute('INSERT INTO patients(id,username,password,name,age,week) VALUES(?,?,?,?,?,?)',
                         (uid,'patient_demo' if uid==1 else 'patient_demo_'+str(uid),password,name,age,week))
        conn.execute('INSERT INTO doctors(id,username,password,name) VALUES(1,?,?,?)',('doctor_demo',password,'BS. Linh Chi'))
        for uid, angle, when, source in [(1,60,'2026-10-26 09:00:00','camera'),(1,61.4,'2026-10-26 09:00:00','imu'),(1,72.2,'2026-10-29 09:00:00','camera'),(1,71.8,'2026-10-29 09:00:00','imu'),(2,78.3,'2026-11-01 08:00:00','camera'),(1,82.1,'2026-11-01 09:00:00','camera'),(1,81.5,'2026-11-01 09:00:00','imu'),(1,87.4,'2026-11-04 09:42:00','camera'),(1,86.8,'2026-11-04 09:42:00','imu')]:
            conn.execute('INSERT INTO measurements(patient_id,angle,time,source) VALUES(?,?,?,?)',(uid,angle,when,source))
        conn.commit()
        conn.close()
        @backend.app.after_request
        def preview_notice(response):
            if response.mimetype == 'text/html':
                html = response.get_data(as_text=True)
                notice = '<div class="preview-notice" role="note">Bản xem thử UI. Dữ liệu minh họa, không kết nối camera hoặc cảm biến.</div>'
                response.set_data(html.replace('</body>',notice+'</body>'))
            return response
        print(f'UI preview: http://127.0.0.1:{args.port}', flush=True)
        print('Demo logins: patient_demo / doctor_demo; password: preview-only-2026', flush=True)
        print('Only temporary sample data is used. Live database and hardware are untouched.', flush=True)
        try:
            from waitress import serve
            serve(backend.app, host='127.0.0.1', port=args.port, threads=4)
        finally:
            backend.shutdown_resources()


if __name__ == '__main__':
    main()
