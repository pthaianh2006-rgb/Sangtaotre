"""Compatibility spelling for knee_longer.py."""
from pathlib import Path
import runpy

if __name__=='__main__':
    runpy.run_path(str(Path(__file__).resolve().with_name('knee_longer.py')),run_name='__main__')
