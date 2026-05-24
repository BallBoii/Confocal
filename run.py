import os, sys, runpy

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [ROOT, os.path.join(ROOT, "gui")]
runpy.run_path(os.path.join(ROOT, "gui", "confocal_gui.py"), run_name="__main__")
