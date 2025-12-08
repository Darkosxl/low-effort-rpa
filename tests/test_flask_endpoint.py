"""
Simple tests for Flask endpoint.
Run with: pytest tests/ -v
"""
import sys
import os

# Add parent directory to path 
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from flask_endpoint import app


@pytest.fixture
def client():
    """Create test client for Flask app"""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


def test_whatsapp_endpoint_exists(client):
    """The /reply_whatsapp endpoint should exist and accept POST"""
    # Empty POST - should not crash even with missing data
    response = client.post('/reply_whatsapp', data={})
    # We just check it doesn't return 404
    assert response.status_code != 404


def test_whatsapp_returns_xml(client):
    """Response should be XML (TwiML format)"""
    response = client.post('/reply_whatsapp', data={
        'Body': 'test message',
        'NumMedia': '0',
        'From': 'whatsapp:+1234567890'
    })
    assert 'xml' in response.content_type.lower()
