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


def test_wavespeed_job_without_real_api_calls(tmp_path, monkeypatch):
    """Exercise upload, submission, polling, and completed image assignment via fake HTTP."""
    from app import main as mod
    client = get_client(tmp_path, monkeypatch)
    monkeypatch.setenv('WAVESPEED_API_KEY', 'test-only')
    path = new_image(client)
    room = client.post('/api/projects', json={'name':'Demo room','image_path':path}).json()
    calls = []

    class FakeResponse:
        status_code=200
        def __init__(self, payload=None): self.payload=payload or {}
        def raise_for_status(self): return None
        def json(self): return self.payload

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, url, **kwargs):
            calls.append(('POST',url,kwargs))
            if url.endswith('/media/uploads'):
                return FakeResponse({'data':{'upload':{'url':'https://signed.example.net/upload','headers':{}},'download_url':'https://media.example.net/input.png'}})
            if url.endswith('/qwen-image/edit-plus'):
                assert kwargs['json']['images'] == ['https://media.example.net/input.png']
                return FakeResponse({'data':{'id':'prediction1'}})
            raise AssertionError(url)
        def request(self, method, url, **kwargs):
            assert method == 'PUT' and url == 'https://signed.example.net/upload'
            assert len(kwargs['content']) > 10
            calls.append((method,url,kwargs))
            return FakeResponse()
        def get(self, url, **kwargs):
            calls.append(('GET',url,kwargs))
            return FakeResponse({'data':{'status':'completed','outputs':['https://media.example.net/empty.png']}})

    monkeypatch.setattr(mod.httpx, 'Client', FakeClient)
    monkeypatch.setattr(mod.time, 'sleep', lambda _: None)
    jid = 'test-job'
    with mod.database() as db:
        db.execute('INSERT INTO jobs (id,kind,project_id,status,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                   (jid,'empty_room',room['id'],'queued',mod.utcnow(),mod.utcnow()))
    mod.process_job(jid)
    job=client.get('/api/jobs/'+jid).json()
    assert job['status']=='completed'
    assert job['prediction_id']=='prediction1'
    assert client.get('/api/projects/'+room['id']).json()['empty_image_path']=='https://media.example.net/empty.png'
    assert [c[0] for c in calls]==['POST','PUT','POST','GET']
