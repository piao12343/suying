from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / '源码' / 'tools' / 'story_collector.py'

runpy.run_path(str(SCRIPT), run_name='__main__')
