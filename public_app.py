"""Public Streamlit entrypoint: self-created fictional data only."""
import os
from pathlib import Path
import sys

os.environ['PUBLIC_DEMO_MODE'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parent / '02_项目代码'))
from streamlit_app import main

main()
