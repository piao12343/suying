from pathlib import Path
import runpy


SCRIPT = Path(__file__).resolve().with_name('story_collector.py')

runpy.run_path(str(SCRIPT), run_name='__main__')
