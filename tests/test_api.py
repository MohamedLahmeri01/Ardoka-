import io
import importlib
import os
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image


def png():
    buffer = io.BytesIO()
    Image.new('RGB', (90, 70), '#e5decf').save(buffer, format='PNG')
    return buffer.getvalue()


def get_client(tmp_path, monkeypatch):
    monkeypatch.setenv('ARDOKA_DATA_DIR', str(tmp_path))
    import app.main as mod
    importlib.reload(mod)
    return TestClient(mod.app)


def new_image(client):
    r = client.post('/api/uploads', files={'file': ('room.png', png(), 'image/png')})
    assert r.status_code == 200, r.text
    return r.json()['path']


def test_initial_state_and_invalid_upload(tmp_path, monkeypatch):
    client = get_client(tmp_path, monkeypatch)
    assert client.get('/api/health').json()['database'] == 'sqlite'
    assert client.get('/api/products').json() == []
    assert client.get('/api/projects').json() == []
    assert client.post('/api/uploads', files={'file': ('fake.png', b'not an image', 'image/png')}).status_code == 422
    assert client.post('/api/uploads', files={'file': ('file.txt', b'hi', 'text/plain')}).status_code == 422


def test_end_to_end_crud_and_placement(tmp_path, monkeypatch):
    client = get_client(tmp_path, monkeypatch)
    path = new_image(client)
    p = client.post('/api/products', json={'name':'Sofa', 'seller':'Example', 'image_path':path,
        'width':1.6,'depth':.8,'height':.9,'price_dzd':45000,'style':'Scandinavian'})
    assert p.status_code == 201, p.text
    product = p.json()
    assert product['model_path'] is None
    room = client.post('/api/projects',json={'name':'Living room','image_path':path,
        'prompt':'Scandinavian space','width':4,'depth':5,'height':2.8})
    assert room.status_code == 201, room.text
    pid = room.json()['id']
    recommendations = client.get(f'/api/projects/{pid}/recommendations').json()
    assert recommendations['method'] == 'local_keyword_heuristic'
    assert recommendations['products'][0]['id'] == product['id']
    valid = {'items':[{'id':'item1','product_id':product['id'],'x':0,'z':0,'rotation':0.3}]}
    saved = client.put(f'/api/projects/{pid}/scene',json=valid)
    assert saved.status_code == 200, saved.text
    assert client.get(f'/api/projects/{pid}').json()['scene'] == valid['items']
    assert client.put(f'/api/projects/{pid}/scene',json={'items':[valid['items'][0]]*2}).status_code == 422
    outside = {'items':[{'id':'out','product_id':product['id'],'x':2,'z':0,'rotation':0}]}
    assert client.put(f'/api/projects/{pid}/scene',json=outside).status_code == 422
    unknown = {'items':[{'id':'item','product_id':'missing','x':0,'z':0,'rotation':0}]}
    assert client.put(f'/api/projects/{pid}/scene',json=unknown).status_code == 422
    assert client.put(f'/api/projects/{pid}/room',json={'width':6,'depth':5,'height':3}).status_code == 200
    assert client.get('/').status_code == 200
    assert client.get('/app.js').status_code == 200


def test_wave_submit_requires_key(tmp_path, monkeypatch):
    monkeypatch.delenv('WAVESPEED_API_KEY', raising=False)
    client = get_client(tmp_path, monkeypatch)
    path = new_image(client)
    room = client.post('/api/projects',json={'name':'Test room','image_path':path}).json()
    r = client.post('/api/jobs',json={'kind':'empty_room','project_id':room['id']})
    assert r.status_code == 503
    assert client.get('/api/jobs').json() == []
