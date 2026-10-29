# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end smoke of the packaged host, real Rust engine and exported audio/video."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import http.cookiejar
import json
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import Request, build_opener, HTTPCookieProcessor

p=argparse.ArgumentParser();p.add_argument('--host',type=Path,required=True);p.add_argument('--ffmpeg',required=True);p.add_argument('--ffprobe',required=True);p.add_argument('--expect-no-renderer',action='store_true',help='expect a clear export error on hosted Windows runners without a usable renderer');args=p.parse_args()
with tempfile.TemporaryDirectory() as folder:
    root=Path(folder);source=root/'sample.mp4'
    subprocess.run([args.ffmpeg,'-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=24','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','3','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(source)],check=True,timeout=60)
    proc=subprocess.Popen([str(args.host.resolve()),'--no-gui','--bind','127.0.0.1','--port','0','--workspace',str(root/'workspace')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(proc.stdout.readline)
            try: config=json.loads(future.result(timeout=90))
            except Exception:
                proc.kill();raise
        client=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def request(path,data=None,headers=None):
            req=Request(config['local']+path,data=data,headers=headers or {})
            return client.open(req,timeout=180).read()
        def post(path,body):return json.loads(request(path,json.dumps(body).encode(),{'X-Concat-LAN':'1','Content-Type':'application/json'}))
        def api(op,**params):return post('/api',{'op':op,**params})
        post('/pair',{'code':config['code']})
        assert b'Concat' in request('/')
        state=api('create',name='LAN integration',width=320,height=180,fps=24)
        project=state['project']
        def mutate(op,**params):
            global state
            state=api(op,project=project,revision=state['revision'],**params)
            return state
        uploaded=json.loads(request('/upload',source.read_bytes(),{'X-Concat-LAN':'1','X-Filename':'sample.mp4'}))
        mutate('import',file=uploaded['file'])
        media=state['view']['project']['media'][0]
        mutate('edit',command={'op':'addClipAtFirstFree','mediaId':media['id'],'start':0})
        timeline=state['view']['project']['timelines'][0];clip=timeline['clips'][0]
        mutate('edit',command={'op':'batch','commands':[{'op':'updateClip','clipId':clip['id'],'patch':{'volume':.75}}, {'op':'trimClip','clipId':clip['id'],'edge':'end','delta':-.5}]})
        mutate('undo');mutate('redo')
        if args.expect_no_renderer:
            print('SKIP: preview pixels cannot be read because the hosted Windows runner suspends its DXGI device')
        else:
            picture=api('preview',project=project,time=.5)
            preview=root/'preview.png';preview.write_bytes(base64.b64decode(picture['png']))
            assert preview.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
        def check_picture(path):
            pixels=subprocess.check_output([args.ffmpeg,'-v','error','-i',str(path),'-frames:v','1','-vf','scale=32:18','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],timeout=30)
            assert len(pixels)==32*18*3, 'incomplete decoded frame'
            assert max(pixels)-min(pixels)>80 and sum(pixels)/len(pixels)>15, 'rendered frame is blank'
        if not args.expect_no_renderer:
            check_picture(preview)
        job=api('export',project=project)
        # No browser requests while the worker runs; its journal must reach done independently.
        journal=root/'workspace'/'jobs.json';deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            saved=json.loads(journal.read_text(encoding='utf-8'))[job['id']]
            if saved['state']!='running':break
            time.sleep(1)
        if args.expect_no_renderer:
            message=saved.get('error',{}).get('message','')
            assert saved['state']=='failed' and 'no GPU or software renderer is available' in message, saved
            print('PASS: the packaged host records the expected renderer-unavailable error on the hosted Windows runner')
        else:
            if saved['state'] != 'done':
                print((root/'workspace'/'engine.log').read_text(encoding='utf-8',errors='replace')[-12000:])
            assert saved['state']=='done',saved
            output=root/'download.mp4';output.write_bytes(request('/download/'+job['id']))
            probe=json.loads(subprocess.check_output([args.ffprobe,'-v','error','-show_streams','-show_format','-of','json',str(output)],timeout=30))
            assert {s['codec_type'] for s in probe['streams']} >= {'video','audio'},probe
            assert 2.3<float(probe['format']['duration'])<2.8,probe
            check_picture(output)
        # Reconnect in a fresh browser session to find the completed job and saved edit.
        client=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        post('/pair',{'code':config['code']})
        assert any(j['id']==job['id'] and j['state']==saved['state'] for j in api('info')['jobs'])
        assert api('open',project=project)['view']['project']['timelines'][0]['clips']
        print('PASS: packaged host, pairing, upload, edit/undo/redo, detached export state, and reconnect' + (' (hosted runner renderer unavailable)' if args.expect_no_renderer else ', audio/video download and PNG preview'))
    except Exception:
        log=root/'workspace'/'engine.log'
        if log.exists(): print(log.read_text(encoding='utf-8',errors='replace')[-16000:])
        raise
    finally:
        if proc.poll() is None:
            try:
                proc.stdin.write('stop\n');proc.stdin.flush();proc.wait(timeout=15)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                # Test-only process tree: a forced host exit must not leave its engine behind.
                subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
                proc.wait(timeout=10)
