"""Entry point for PyInstaller and for running from a checkout: python run_insights.py [log]"""
import sys

from landingrate.app import main

if __name__ == "__main__":
    sys.exit(main())
