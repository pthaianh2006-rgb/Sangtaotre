"""Use the installed full Python for Tk, retaining dependencies in the project on D."""
import os
from pathlib import Path
import subprocess
import sys


def tk_python():
    if os.name != 'nt': return None
    import winreg
    candidates=[]
    for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive,r'Software\Python\PythonCore\3.12\InstallPath') as key:
                candidates.append(Path(winreg.QueryValueEx(key,'ExecutablePath')[0]))
        except OSError: continue
    for exe in candidates:
        if not exe.is_file() or exe.resolve()==Path(sys.executable).resolve():continue
        try:
            result=subprocess.run([str(exe),'-c','import tkinter'],capture_output=True,timeout=10,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode==0:return exe
        except (OSError,subprocess.TimeoutExpired):continue
    return None


def relaunch(script):
    exe=tk_python()
    if exe is None:
        print('Khong tim thay Python 3.12 co Tkinter de mo cua so KneeROM.',file=sys.stderr)
        return 1
    return subprocess.call([str(exe),str(script),*sys.argv[1:]],
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
