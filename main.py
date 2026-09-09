"""
Entry point for the Blura News API.

Usage:
    python main.py                  # runs on http://0.0.0.0:8000
    uvicorn api.main:app --reload   # equivalent, with autoreload
"""

import os

import uvicorn

if __name__ == '__main__':
    port = int(os.getenv('PORT', '8000'))
    uvicorn.run('api.main:app', host='0.0.0.0', port=port, reload=False)
