# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Concat Plus contributors
"""LAN companion. Rendering and editing run in the bundled Rust engine on the PC."""
from __future__ import annotations
import argparse
from concurrent.futures import Future
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit, parse_qs, unquote
import webbrowser

MAX_JSON = 1024 * 1024
MAX_UPLOAD = 2 * 1024**3
MEDIA = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v', '.mp3', '.wav', '.flac', '.aac', '.m4a', '.ogg', '.png', '.jpg', '.jpeg', '.webp'}
OPS = {'addClip', 'addClipAtFirstFree', 'addClipsInSequence', 'addTextClip', 'addLayerClip', 'moveClips', 'trimClip', 'splitClips', 'removeClips', 'updateClip', 'setClipSpeed', 'setClipTransform', 'addTrack', 'removeTrack', 'setTrackFlag', 'selectTimeline', 'addTimeline', 'renameTimeline'}

class Problem(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def inside(root: Path, relative: str, exists=True) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise Problem('Ruta no válida')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise Problem('La ruta sale de la carpeta compartida', 403)
    if exists and not path.exists():
        raise Problem('El archivo ya no existe', 404)
    return path


def safe_name(name):
    if not isinstance(name, str) or not re.fullmatch(r'[\w \-().]{1,80}', name, re.UNICODE) or name.strip(' .') != name:
        raise Problem('Usa un nombre de 1 a 80 letras, números, espacios, guiones o paréntesis')
    if name.split('.')[0].upper() in {'CON','PRN','AUX','NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}:
        raise Problem('Nombre reservado por Windows')
    return name


class Engine:
    """Dedicated stdout reader: export events must never fill a pipe when a phone sleeps."""
    def __init__(self, executable: Path, log: Path):
        self.pending = {}
        self.lock = threading.Lock()
        self.counter = 0
        self.failed = False
        self.log = open(log, 'ab')
        self.process = subprocess.Popen([str(executable), 'api'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(32 * 1024 * 1024)
                if not line:
                    break
                data = json.loads(line)
                with self.lock:
                    future = self.pending.pop(data.get('id'), None)
                if future is not None:
                    future.set_result(data)
        except Exception:
            pass
        finally:
            with self.lock:
                self.failed = True
                for future in self.pending.values():
                    future.set_exception(Problem('El motor de la PC se cerró. Revisa engine.log.', 503))
                self.pending.clear()

    def call(self, method, **params):
        with self.lock:
            if self.failed or self.process.poll() is not None:
                raise Problem('El motor no está ejecutándose', 503)
            self.counter += 1
            request_id = self.counter
            future = Future()
            self.pending[request_id] = future
            try:
                self.process.stdin.write((json.dumps({'jsonrpc':'2.0','id':request_id,'method':method,'params':params})+'\n').encode())
                self.process.stdin.flush()
            except Exception as error:
                self.pending.pop(request_id, None)
                raise Problem('Se perdió la conexión con el motor', 503) from error
        try:
            response = future.result(timeout=180)
        except TimeoutError as error:
            with self.lock:
                self.pending.pop(request_id, None)
            # Do not replay a timed-out edit: it may already have been committed.
            raise Problem('La PC sigue ocupada. Actualiza el proyecto antes de repetir la operación.', 504) from error
        if 'error' in response:
            raise Problem(response['error'].get('message', 'El motor rechazó la operación'), 422)
        return response['result']

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.reader.join(timeout=2)
        self.log.close()


class Workspace:
    def __init__(self, root, engine):
        self.root = Path(root).resolve()
        self.engine = engine
        self.lock = threading.RLock()
        self.revisions = {}
        self.jobs = {}
        self.stop = threading.Event()
        for name in ('projects','uploads','library','exports'):
            (self.root/name).mkdir(parents=True, exist_ok=True)
        self.journal = self.root/'jobs.json'
        if self.journal.exists():
            for key, job in json.loads(self.journal.read_text(encoding='utf-8')).items():
                if job['state'] == 'running':
                    job.update(state='failed', error={'message':'La PC o el host se reinició; vuelve a exportar.'})
                self.jobs[key] = job
        self.observer = threading.Thread(target=self._observe, daemon=True)
        self.observer.start()

    def _persist(self):
        temporary = self.journal.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.jobs, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.journal)

    def _observe(self):
        while not self.stop.wait(1):
            with self.lock:
                for key, job in list(self.jobs.items()):
                    if job['state'] != 'running':
                        continue
                    try:
                        result = self.engine.call('export.status', job=job['engineJob'])
                        self.jobs[key] = {**job, **result, 'id':key}
                        if result['state'] != 'running':
                            self._persist()
                    except Exception as error:
                        self.jobs[key].update(state='failed', error={'message':str(error)})
                        self._persist()

    def project(self, relative):
        path = inside(self.root/'projects', relative)
        if not path.is_dir():
            raise Problem('No es una carpeta de proyecto')
        return str(path)

    def state(self, project, view=None):
        if view is None:
            view = self.engine.call('project.get', path=self.project(project))
        return {'project':project, 'revision':self.revisions.get(project,0), 'view':view}

    def command(self, command):
        if isinstance(command, dict) and command.get('op') == 'batch':
            commands = command.get('commands')
            if not isinstance(commands, list) or not 1 <= len(commands) <= 32:
                raise Problem('Lote de edición no válido')
            if any(not isinstance(c, dict) or c.get('op') == 'batch' for c in commands):
                raise Problem('No se permiten lotes anidados')
            return {'op': 'batch', 'commands': [self.command(c) for c in commands]}
        if not isinstance(command, dict) or command.get('op') not in OPS:
            raise Problem('Esta operación no está disponible en el editor LAN')
        # No file-bearing commands or opaque future fields cross this boundary.
        def check(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {'path','file','fontFile','background','item','crop','mask'}:
                        raise Problem('Campo no admitido en la edición remota')
                    check(child)
            elif isinstance(value, list):
                for child in value: check(child)
        check(command)
        return command

    def call(self, request):
        with self.lock:
            op = request.get('op')
            if op == 'info':
                return {'name':'Concat Plus LAN', 'engine':self.engine.call('version'), 'maxUpload':MAX_UPLOAD,
                        'projects':[p.name for p in sorted((self.root/'projects').iterdir()) if p.is_dir() and not p.is_symlink()],
                        'jobs':list(self.jobs.values())}
            if op == 'files':
                result=[]
                for folder in ('uploads','library'):
                    for p in (self.root/folder).rglob('*'):
                        if p.is_file() and p.suffix.lower() in MEDIA and p.resolve().is_relative_to(self.root):
                            result.append({'file':p.relative_to(self.root).as_posix(),'name':p.name,'size':p.stat().st_size})
                        if len(result)>=2000: break
                return result
            if op == 'catalogue':
                return self.engine.call('catalogue.list',kind='effect')
            if op == 'create':
                name=safe_name(request.get('name'))
                width=int(request.get('width',1920)); height=int(request.get('height',1080)); fps=int(request.get('fps',30))
                if not (16<=width<=3840 and 16<=height<=3840 and 1<=fps<=60):
                    raise Problem('Tamaño o frecuencia fuera de rango')
                view=self.engine.call('project.create',location=str(self.root/'projects'),name=name,
                    video={'width':width,'height':height,'rateNum':fps,'rateDen':1})
                return self.state(name,view)
            if op in {'status','cancel'}:
                key=request.get('job'); job=self.jobs.get(key)
                if job is None: raise Problem('Exportación desconocida',404)
                if op=='cancel' and job['state']=='running':
                    self.engine.call('export.cancel',job=job['engineJob'])
                return job
            project=request.get('project'); path=self.project(project)
            if op=='open':
                return self.state(project,self.engine.call('project.open',path=path))
            if op=='state': return self.state(project)
            if op=='preview':
                state=self.state(project); video=state['view']['settings']
                width=640; height=max(16,round(width*video['height']/video['width']))
                if height>1280: height=1280; width=max(16,round(height*video['width']/video['height']))
                return self.engine.call('preview.frame',path=path,time=max(0,float(request.get('time',0))),width=width,height=height)
            if op=='export':
                preview=request.get('preview') is True
                key=secrets.token_hex(12); output=self.root/'exports'/f'{key}.mp4'
                options={'codec':'h264','crf':28 if preview else 20,'preset':'ultrafast' if preview else 'medium'}
                if preview:
                    view=self.state(project)['view']['settings']; w=640; h=max(2,round(w*view['height']/view['width']/2)*2)
                    if h>1280: h=1280; w=max(2,round(h*view['width']/view['height']/2)*2)
                    options.update(width=w,height=h,rateNum=15,rateDen=1)
                started=self.engine.call('export.run',path=path,output=str(output),**options)
                job={'id':key,'engineJob':started['job'],'state':'running','fraction':0,'project':project,'preview':preview,'file':output.name}
                self.jobs[key]=job;self._persist();return job
            if op not in {'edit','undo','redo','import','save'}:
                raise Problem('Operación desconocida')
            if request.get('revision') != self.revisions.get(project,0):
                raise Problem('El proyecto cambió en otra sesión. Actualiza antes de editar.',409)
            if op=='edit':
                view=self.engine.call('edit.apply',path=path,command=self.command(request.get('command')))
            elif op=='import':
                file=inside(self.root,request.get('file'))
                if not any(file.is_relative_to(self.root/f) for f in ('uploads','library')) or file.suffix.lower() not in MEDIA:
                    raise Problem('Archivo no admitido',403)
                view=self.engine.call('media.import',path=path,file=str(file))
            elif op in {'undo','redo'}:
                view=self.engine.call('edit.'+op,path=path)
            else: view=None
            self.revisions[project]=self.revisions.get(project,0)+1
            self.engine.call('project.save',path=path)
            return self.state(project,view)

    def close(self):
        self.stop.set(); self.observer.join(timeout=3)
        self.engine.close()


class LANServer(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
    def __init__(self, address, workspace, web, hosts, secure=False, custom_code=None, require_code=True):
        self.workspace=workspace; self.web=Path(web); self.hosts=set(hosts)
        self.secure=secure; self.token=secrets.token_urlsafe(32)
        self.require_code=require_code
        self.custom_code=custom_code
        self.code=custom_code or f'{secrets.randbelow(100000000):08d}'
        self.code_until=float('inf') if custom_code else time.monotonic()+600
        self.attempts=[]; self.pair_lock=threading.Lock(); self.seats=threading.BoundedSemaphore(16)
        super().__init__(address,Handler)

    def process_request(self, request, client_address):
        if not self.seats.acquire(blocking=False):
            request.close();return
        try: super().process_request(request,client_address)
        except BaseException: self.seats.release();raise

    def process_request_thread(self, request, client_address):
        try: super().process_request_thread(request,client_address)
        finally: self.seats.release()


class Handler(BaseHTTPRequestHandler):
    server_version='ConcatPlusLAN/0.3'
    def setup(self):
        self.request.settimeout(30)
        super().setup()

    def log_message(self,*args): pass  # Never put pairing codes or cookies into logs.

    def send(self,status,body,kind='application/json',headers=None):
        data=json.dumps(body,ensure_ascii=False).encode() if kind=='application/json' else body
        self.send_response(status)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key,value in (headers or {}).items(): self.send_header(key,value)
        self.end_headers();self.wfile.write(data)

    def guard(self, auth=True, mutation=False):
        host=self.headers.get('Host','')
        try: hostname=urlsplit('//'+host).hostname
        except ValueError: raise Problem('Host no válido',403)
        if hostname not in self.server.hosts: raise Problem('Host no permitido',403)
        origin=self.headers.get('Origin')
        expected=('https' if self.server.secure else 'http')+'://'+host
        if origin and origin != expected: raise Problem('Origen no permitido',403)
        if mutation and self.headers.get('X-Concat-LAN')!='1': raise Problem('Solicitud no permitida',403)
        if auth and self.server.require_code:
            cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''))
            value=cookie.get('concat_session')
            if not value or not secrets.compare_digest(value.value,self.server.token):
                raise Problem('Conecta con el código que muestra la PC',401)

    def body(self,limit=MAX_JSON):
        if self.headers.get('Transfer-Encoding'): raise Problem('Transferencia no admitida')
        try: length=int(self.headers.get('Content-Length','-1'))
        except ValueError: raise Problem('Tamaño no válido')
        if length<0 or length>limit: raise Problem('Archivo o petición demasiado grande',413)
        return length

    def json_body(self):
        data=self.rfile.read(self.body())
        try:
            value=json.loads(data)
            if not isinstance(value,dict): raise ValueError()
            return value
        except (ValueError,UnicodeError): raise Problem('JSON no válido')

    def do_POST(self):
        try:
            path=urlsplit(self.path).path
            self.guard(auth=path!='/pair',mutation=True)
            if path=='/pair':
                data=self.json_body()
                with self.server.pair_lock:
                    now=time.monotonic(); peer=self.client_address[0]
                    self.server.attempts=[(t,ip) for t,ip in self.server.attempts if now-t<60]
                    if len(self.server.attempts)>=20 or sum(ip==peer for _,ip in self.server.attempts)>=5:
                        raise Problem('Demasiados intentos. Espera un minuto.',429)
                    self.server.attempts.append((now,peer))
                    if now>self.server.code_until or not secrets.compare_digest(str(data.get('code','')).encode('utf-8'),self.server.code.encode('utf-8')):
                        raise Problem('Código incorrecto o vencido. Reinicia el host para generar otro.',401)
                    # Codes expire after ten minutes; the session lasts until the host stops.
                    cookie='concat_session='+self.server.token+'; HttpOnly; SameSite=Strict; Path=/'
                    if self.server.secure: cookie+='; Secure'
                    self.send(200,{'connected':True},headers={'Set-Cookie':cookie});return
            if path=='/api':
                result=self.server.workspace.call(self.json_body());self.send(200,result);return
            if path=='/upload':
                length=self.body(MAX_UPLOAD)
                name=unquote(self.headers.get('X-Filename',''))
                ext=Path(name).suffix.lower()
                if ext not in MEDIA: raise Problem('Formato de archivo no admitido')
                folder=self.server.workspace.root/'uploads'
                if shutil.disk_usage(folder).free<length+512*1024**2: raise Problem('No queda espacio suficiente en la PC',507)
                dest=folder/(secrets.token_hex(8)+'-'+re.sub(r'[^\w()-]','_',Path(name).stem)[:80]+ext)
                temporary=dest.with_suffix(dest.suffix+'.part')
                try:
                    with temporary.open('xb') as out:
                        remaining=length
                        while remaining:
                            chunk=self.rfile.read(min(1024*1024,remaining))
                            if not chunk: raise Problem('La subida se interrumpió')
                            out.write(chunk);remaining-=len(chunk)
                    temporary.replace(dest)
                finally: temporary.unlink(missing_ok=True)
                self.send(201,{'file':dest.relative_to(self.server.workspace.root).as_posix()});return
            raise Problem('No encontrado',404)
        except Problem as error: self.send(error.status,{'error':str(error)})
        except (ConnectionError,TimeoutError): self.close_connection=True
        except Exception: self.send(500,{'error':'La PC no pudo completar la operación. Revisa el registro del host.'})

    def do_GET(self):
        try:
            path=urlsplit(self.path).path
            assets={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
            self.guard(auth=path not in assets)
            if path in assets:
                file=self.server.web/assets[path]
                self.send(200,file.read_bytes(),{'/':'text/html; charset=utf-8','/app.js':'text/javascript; charset=utf-8','/style.css':'text/css; charset=utf-8'}[path]);return
            if path.startswith('/download/'):
                key=path.removeprefix('/download/')
                with self.server.workspace.lock: job=self.server.workspace.jobs.get(key,{}).copy()
                if job.get('state')!='done': raise Problem('El video aún no está disponible',404)
                file=inside(self.server.workspace.root/'exports',job['file'])
                size=file.stat().st_size; start=0;end=size-1; status=200
                range_header=self.headers.get('Range')
                if range_header:
                    match=re.fullmatch(r'bytes=(\d+)-(\d*)',range_header)
                    if not match: raise Problem('Rango no admitido',416)
                    start=int(match[1]);end=min(int(match[2]) if match[2] else end,end)
                    if start>end: raise Problem('Rango fuera del archivo',416)
                    status=206
                self.send_response(status);self.send_header('Content-Type','video/mp4')
                self.send_header('Accept-Ranges','bytes');self.send_header('Cache-Control','no-store')
                self.send_header('Content-Length',str(end-start+1))
                if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                if parse_qs(urlsplit(self.path).query).get('save'):
                    self.send_header('Content-Disposition','attachment; filename="ConcatPlus-export.mp4"')
                self.end_headers()
                with file.open('rb') as src:
                    src.seek(start);remaining=end-start+1
                    while remaining:
                        chunk=src.read(min(1024*1024,remaining))
                        if not chunk:break
                        self.wfile.write(chunk);remaining-=len(chunk)
                return
            raise Problem('No encontrado',404)
        except Problem as error:self.send(error.status,{'error':str(error)})
        except (ConnectionError,TimeoutError):self.close_connection=True
        except Exception:self.send(500,{'error':'No se pudo leer el archivo'})


def local_addresses():
    addresses={'127.0.0.1'}
    try:
        for item in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET):
            ip=ipaddress.ip_address(item[4][0])
            if ip.is_private and not ip.is_link_local:addresses.add(str(ip))
    except OSError:pass
    return sorted(addresses)


def access_settings(workspace, custom_code=None, require_code=True, *, save=False):
    """Read/write local access preferences. Blank code requests a fresh random code."""
    path = Path(workspace) / 'host-settings.json'
    if not save:
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value, dict): raise ValueError()
            code = value.get('customCode', '')
            required = value.get('requireCode', True)
            if not isinstance(required, bool) or not isinstance(code, str): raise ValueError()
            if code and not 8 <= len(code) <= 64: raise ValueError()
            return code or None, required
        except (OSError, ValueError):
            return None, True
    if not isinstance(require_code, bool) or (custom_code is not None and not isinstance(custom_code, str)):
        raise Problem('Configuración de acceso no válida')
    code = (custom_code or '').strip()
    if code and not 8 <= len(code) <= 64:
        raise Problem('Usa de 8 a 64 caracteres para el código personalizado')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'requireCode': require_code, 'customCode': code}, ensure_ascii=False), encoding='utf-8')
    if os.name != 'nt': temporary.chmod(0o600)
    temporary.replace(path)
    return code or None, require_code


def configure(workspace):
    """Local-only settings; never expose access settings through the LAN editor."""
    import tkinter as tk
    from tkinter import messagebox
    saved_code, saved_required = access_settings(workspace)
    result=[]
    window=tk.Tk();window.title('Concat Plus · Iniciar host LAN');window.geometry('590x400')
    tk.Label(window,text='Tu PC, tu editor remoto',font=('Segoe UI',20,'bold')).pack(pady=20)
    required=tk.BooleanVar(value=saved_required)
    tk.Checkbutton(window,text='Requerir código para conectarse',variable=required).pack()
    tk.Label(window,text='Código personalizado (8–64 caracteres). Vacío = aleatorio.').pack(pady=12)
    code=tk.Entry(window,width=42,show='•');code.insert(0,saved_code or '');code.pack()
    tk.Label(window,text='El código personalizado se guarda en esta PC.\nSin código, cualquiera en esta LAN puede editar y descargar los archivos compartidos.\nUsa solamente una red privada de confianza.',wraplength=540,justify='center').pack(pady=20)
    def start():
        value=code.get().strip()
        if value and not 8<=len(value)<=64:
            messagebox.showerror('Código','Usa de 8 a 64 caracteres.');return
        if not required.get() and not messagebox.askyesno('Acceso abierto en la LAN','¿Permitir acceso sin código a los archivos compartidos y a la edición desde esta red?'):return
        try: selected = access_settings(workspace, value, required.get(), save=True)
        except (OSError, Problem) as error:
            messagebox.showerror('No se pudo guardar', str(error));return
        result.append(selected);window.destroy()
    tk.Button(window,text='Iniciar host LAN',command=start,width=25).pack(pady=8)
    window.mainloop()
    return result[0] if result else None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    base=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent
    parser.add_argument('--engine',type=Path,default=base/'concat-cli.exe')
    parser.add_argument('--workspace',type=Path,default=Path.home()/'Documents'/'ConcatPlusLAN')
    parser.add_argument('--port',type=int,default=7421)
    parser.add_argument('--bind',default='0.0.0.0')
    parser.add_argument('--cert',type=Path);parser.add_argument('--key',type=Path)
    parser.add_argument('--no-gui',action='store_true')
    parser.add_argument('--no-code',action='store_true',help='Permitir acceso sin código desde la LAN')
    args=parser.parse_args()
    if not args.engine.is_file():parser.error('No se encuentra concat-cli.exe junto al host')
    args.workspace.mkdir(parents=True,exist_ok=True)
    if bool(args.cert) != bool(args.key):parser.error('Indica ambos archivos: --cert y --key')
    custom_code, require_code = access_settings(args.workspace)
    custom_code=os.environ.get('CONCAT_LAN_CODE') or custom_code
    if args.no_code:require_code=False
    if not args.no_gui:
        selected=configure(args.workspace)
        if selected is None:return
        custom_code,require_code=selected
    if custom_code and not 8<=len(custom_code)<=64:parser.error('El código personalizado debe tener de 8 a 64 caracteres')
    engine=Engine(args.engine.resolve(),args.workspace/'engine.log')
    try: engine.call('version')
    except Exception:engine.close();raise
    workspace=Workspace(args.workspace,engine)
    web=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'web'
    addresses=local_addresses();secure=bool(args.cert and args.key)
    server=LANServer((args.bind,args.port),workspace,web,addresses+['localhost'],secure,custom_code,require_code)
    if secure:
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(args.cert,args.key)
        server.socket=context.wrap_socket(server.socket,server_side=True)
    scheme='https' if secure else 'http'
    urls=[f'{scheme}://{ip}:{server.server_port}' for ip in addresses if ip!='127.0.0.1']
    local=f'{scheme}://127.0.0.1:{server.server_port}'
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        if args.no_gui:
            print(json.dumps({'urls':urls,'local':local,'code':server.code,'workspace':str(args.workspace)},ensure_ascii=False),flush=True)
            stopped = threading.Event()
            def read_console():
                if sys.stdin is not None:
                    for line in sys.stdin:
                        if line.strip().lower() == 'stop':
                            stopped.set();break
            threading.Thread(target=read_console, daemon=True).start()
            while not stopped.wait(1):pass
        else:
            import tkinter as tk
            from tkinter import messagebox
            app=tk.Tk();app.title('Concat Plus · PC para edición LAN');app.geometry('620x440')
            tk.Label(app,text='Concat Plus LAN',font=('Segoe UI',22,'bold')).pack(pady=16)
            tk.Label(app,text='Abre una dirección de esta PC desde el teléfono, en la misma red.').pack()
            urls_box=tk.Text(app,height=3,width=65);urls_box.insert('1.0','\n'.join(urls) or 'No se detectó una IPv4 de LAN. Revisa la conexión Wi-Fi.');urls_box.configure(state='disabled');urls_box.pack(pady=10)
            tk.Label(app,text=('Código personalizado' if custom_code else 'Código de conexión (válido durante 10 minutos)') if require_code else 'Acceso sin código: cualquier dispositivo de la LAN').pack()
            tk.Label(app,text=server.code if require_code else 'SIN CÓDIGO',font=('Consolas',26,'bold')).pack()
            tk.Label(app,text='Red local de confianza. No abras este puerto en Internet.\nLa PC debe permanecer encendida; cerrar esta ventana detiene el host.',wraplength=570).pack(pady=14)
            tk.Button(app,text='Abrir editor en esta PC',command=lambda:webbrowser.open(local)).pack()
            tk.Label(app,text=f'Proyectos y videos: {args.workspace}',wraplength=570).pack(pady=12)
            def close():
                if messagebox.askyesno('Detener host','Se interrumpirán las exportaciones que sigan activas. ¿Cerrar?'):app.destroy()
            app.protocol('WM_DELETE_WINDOW',close);app.mainloop()
    except KeyboardInterrupt:pass
    finally:
        server.shutdown();server.server_close();workspace.close()

if __name__=='__main__':
    main()
