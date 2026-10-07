"""
CORS is configured from CORS_ORIGINS at import time, so each test reloads
api.main under the environment it needs and restores the default afterwards.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import api.main


@pytest.fixture
def client_with_env(monkeypatch):
    def make(cors_origins=None):
        if cors_origins is None:
            monkeypatch.delenv('CORS_ORIGINS', raising=False)
        else:
            monkeypatch.setenv('CORS_ORIGINS', cors_origins)
        return TestClient(importlib.reload(api.main).app)

    yield make
    monkeypatch.delenv('CORS_ORIGINS', raising=False)
    importlib.reload(api.main)


def test_no_cors_headers_by_default(client_with_env):
    client = client_with_env()
    resp = client.get('/health', headers={'Origin': 'https://soceye.example.com'})
    assert resp.status_code == 200
    assert 'access-control-allow-origin' not in resp.headers


def test_listed_origin_is_allowed(client_with_env):
    client = client_with_env('https://soceye.example.com/, http://localhost:3000')
    resp = client.get('/health', headers={'Origin': 'https://soceye.example.com'})
    assert resp.headers['access-control-allow-origin'] == 'https://soceye.example.com'
    resp = client.get('/health', headers={'Origin': 'http://localhost:3000'})
    assert resp.headers['access-control-allow-origin'] == 'http://localhost:3000'


def test_unlisted_origin_is_not_allowed(client_with_env):
    client = client_with_env('https://soceye.example.com')
    resp = client.get('/health', headers={'Origin': 'https://evil.example.com'})
    assert 'access-control-allow-origin' not in resp.headers


def test_wildcard_allows_any_origin(client_with_env):
    client = client_with_env('*')
    resp = client.get('/health', headers={'Origin': 'https://anything.example.com'})
    assert resp.headers['access-control-allow-origin'] == '*'


def test_preflight_request_is_answered(client_with_env):
    client = client_with_env('https://soceye.example.com')
    resp = client.options('/news/articles', headers={
        'Origin': 'https://soceye.example.com',
        'Access-Control-Request-Method': 'GET',
    })
    assert resp.status_code == 200
    assert resp.headers['access-control-allow-origin'] == 'https://soceye.example.com'
