import os
import sys

# Add parent directory to path so app can be imported when run as a serverless function
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
