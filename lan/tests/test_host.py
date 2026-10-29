# SPDX-License-Identifier: AGPL-3.0-or-later
import http.client
import io
import os
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from host import Handler, LANServer, Workspace, Problem, access_settings, inside, safe_name

class FakeEngine:
    def __init__(self): self.calls=[]
    def call(self, method, **params):
        self.calls.append((method, params))
        if method=='version':return {'version':'test'}
        if method=='export.status':return {'state':'done','fraction':1}
        if method=='export.run':return {'job':'engine-job'}
        return {'project':{'media':[]},'settings':{'width':640,'height':360}}
    def close(self):pass

class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.engine=FakeEngine();self.workspace=Workspace(self.root,self.engine)
        self.server=LANServer(('127.0.0.1',0),self.workspace,Path(__file__).parents[1]/'web',['127.0.0.1'],custom_code='Mi-código-123')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.workspace.close();self.temp.cleanup()
    def request(self,path='/api',body=None,headers=None,method='POST'):
        if os.environ.get('CONCAT_TEST_OFFLINE') == '1':
            # Exercise the HTTP parser and handler in memory. Live socket tests remain
            # the default in CI; this mode does not claim to verify LAN transport.
            payload=json.dumps(body or {'op':'info'}).encode()
            values={'Host':'127.0.0.1','X-Concat-LAN':'1','Content-Type':'application/json','Content-Length':str(len(payload))}
            values.update(headers or {})
            raw=(f'{method} {path} HTTP/1.0\r\n'+''.join(f'{k}: {v}\r\n' for k,v in values.items())+'\r\n').encode()+payload
            class Wire:
                def __init__(self): self.output=io.BytesIO()
                def settimeout(self,value):pass
                def makefile(self,*args):return io.BytesIO(raw)
                def sendall(self,data):self.output.write(data)
            wire=Wire();Handler(wire,('127.0.0.1',1234),self.server)
            head,data=wire.output.getvalue().split(b'\r\n\r\n',1)
            lines=head.decode().split('\r\n')
            return int(lines[0].split()[1]),dict(line.split(': ',1) for line in lines[1:]),data
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        values={'X-Concat-LAN':'1','Content-Type':'application/json'};values.update(headers or {})
        conn.request(method,path,json.dumps(body or {'op':'info'}).encode(),values)
        r=conn.getresponse();data=r.read();result=(r.status,dict(r.getheaders()),data);conn.close();return result
    def pair(self):
        status,headers,_=self.request('/pair',{'code':self.server.code});self.assertEqual(status,200)
        self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        return {'Cookie':headers['Set-Cookie'].split(';')[0]}
    def test_custom_unicode_code_authenticates(self):
        self.assertEqual(self.request()[0],401)
        self.assertEqual(self.request('/pair',{'code':'wrong'})[0],401)
        self.assertEqual(self.request(headers=self.pair())[0],200)
    def test_disabled_code_allows_api_but_not_cross_origin(self):
        self.server.require_code=False
        self.assertEqual(self.request()[0],200)
        self.assertEqual(self.request(headers={'Origin':'https://attacker.example'})[0],403)
        self.assertEqual(self.request(headers={'Host':'attacker.example'})[0],403)
        self.assertEqual(self.request(headers={'X-Concat-LAN':''})[0],403)
    def test_custom_code_does_not_expire(self):
        self.assertEqual(self.server.code_until,float('inf'));self.pair()
    def test_random_code_expires_and_sessions_survive(self):
        headers=self.pair();self.server.code_until=0
        self.assertEqual(self.request('/pair',{'code':self.server.code})[0],401)
        self.assertEqual(self.request(headers=headers)[0],200)
    def test_throttle(self):
        for _ in range(5):self.assertEqual(self.request('/pair',{'code':'wrong'})[0],401)
        self.assertEqual(self.request('/pair',{'code':self.server.code})[0],429)
    def test_access_preferences_persist_and_can_be_disabled(self):
        self.assertEqual(access_settings(self.root),(None,True))
        access_settings(self.root,'permanente-123',True,save=True)
        self.assertEqual(access_settings(self.root),('permanente-123',True))
        access_settings(self.root,'permanente-123',False,save=True)
        self.assertEqual(access_settings(self.root),('permanente-123',False))
        access_settings(self.root,'',True,save=True)
        self.assertEqual(access_settings(self.root),(None,True))
        with self.assertRaises(Problem):access_settings(self.root,'short',True,save=True)
    def test_corrupt_preferences_fail_closed(self):
        for data in ['broken','[]','{"requireCode":"false"}','{"customCode":42}']:
            (self.root/'host-settings.json').write_text(data)
            self.assertEqual(access_settings(self.root),(None,True))
    def test_traversal_and_windows_names(self):
        for path in ['../outside','/absolute']:
            with self.assertRaises(Problem):inside(self.root,path,False)
        for name in ['CON','NUL.txt','../bad','a/b','trail.','']:
            with self.assertRaises(Problem):safe_name(name)
    def test_nested_commands_cannot_bypass_allowlist(self):
        self.workspace.command({'op':'batch','commands':[{'op':'addTrack'}]})
        for command in [{'op':'batch','commands':[{'op':'relinkMedia','path':'secret'}]}, {'op':'batch','commands':[{'op':'batch','commands':[]}]}, {'op':'updateClip','patch':{'text':{'fontFile':'secret'}}}]:
            with self.assertRaises(Problem):self.workspace.command(command)
    def test_revision_prevents_stale_edits(self):
        (self.root/'projects'/'Test').mkdir()
        self.workspace.call({'op':'edit','project':'Test','revision':0,'command':{'op':'addTrack'}})
        with self.assertRaises(Problem) as error:
            self.workspace.call({'op':'edit','project':'Test','revision':0,'command':{'op':'addTrack'}})
        self.assertEqual(error.exception.status,409)
        self.assertEqual(sum(method=='edit.apply' for method,_ in self.engine.calls),1)
        self.assertIn('project.save',[method for method,_ in self.engine.calls])
    def test_export_observer_runs_without_http_client(self):
        (self.root/'projects'/'Test').mkdir()
        job=self.workspace.call({'op':'export','project':'Test'})
        for _ in range(30):
            if self.workspace.jobs[job['id']]['state']=='done':break
            time.sleep(.1)
        self.assertEqual(self.workspace.jobs[job['id']]['state'],'done')
        self.assertEqual(json.loads((self.root/'jobs.json').read_text())[job['id']]['state'],'done')
    def test_download_range(self):
        headers=self.pair();headers['Range']='bytes=2-4'
        (self.root/'exports'/'video.mp4').write_bytes(b'0123456789')
        self.workspace.jobs['test']={'state':'done','file':'video.mp4'}
        status,_,data=self.request('/download/test',headers=headers,method='GET')
        self.assertEqual((status,data),(206,b'234'))

if __name__=='__main__':unittest.main()
