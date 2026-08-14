import os,json,re,hashlib,shutil,subprocess,threading,time
import websocket
from flask import Flask,Response,jsonify,render_template_string,request
HOST=os.getenv('CAMERA_HOST','37.202.152.217'); USER=os.getenv('CAMERA_USERNAME','admin'); PASS=os.getenv('CAMERA_PASSWORD',''); WEB=int(os.getenv('LOCAL_PORT','5050'))
PRESETS={
'Camera 1 / Main':(8001,'/media/flv/video1'),'Camera 1 / Sub':(8001,'/media/flv/video2'),'Camera 1 / Third':(8001,'/media/flv/video3'),
'Camera 2 / Main':(8002,'/media/flv/video1'),'Camera 2 / Sub':(8002,'/media/flv/video2'),'Camera 2 / Third':(8002,'/media/flv/video3'),
'Camera 2 / Main media2':(8002,'/media2/flv/video1'),'Camera 2 / Sub media2':(8002,'/media2/flv/video2'),'Camera 2 / Third media2':(8002,'/media2/flv/video3'),
'Camera 3 / Main':(8003,'/media3/flv/video1'),'Camera 3 / Sub':(8003,'/media3/flv/video2'),'Camera 3 / Third':(8003,'/media3/flv/video3')}
app=Flask(__name__); proc=None; ws=None; stop=threading.Event(); clients=[]; lock=threading.Lock(); state={'connected':False,'authenticated':False,'streaming':False,'ffmpeg':'stopped','packets':0,'bytes':0,'frames':0,'last_error':'','port':8001,'path':'/media/flv/video1'}
def md5(x):return hashlib.md5(x.encode()).hexdigest()
def digest(ch,uri):
 d=json.loads(ch);s=d.get('detail','');g=lambda n:re.search(rf'{n}="?([^,\s"]+)',s,re.I).group(1);r,n,q=g('realm'),g('nonce'),g('qop');nc='00000001';cn=os.urandom(12).hex();a1=md5(f'{USER}:{r}:{PASS}');a2=md5(f'GET:{uri}');resp=md5(f'{a1}:{n}:{nc}:{cn}:{q}:{a2}');return f'Digest username="{USER}", realm="{r}", nonce="{n}", algorithm="MD5", uri="{uri}", response="{resp}", qop="{q}", nc={nc}, cnonce="{cn}"'
def connect(p,path):
 u=f'ws://{HOST}:{p}{path}';x=websocket.create_connection(u,origin=f'http://{HOST}:{p}',timeout=10,compression=None);ch=x.recv();x.close();a=digest(ch,u);return websocket.create_connection(u,origin=f'http://{HOST}:{p}',timeout=20,compression=None,header=['Pragma: no-cache','Cache-Control: no-cache','User-Agent: Mozilla/5.0','Cookie: langInfo_=1; noShowTip=1; Authorization='+a])
def broadcast(x):
 with lock:
  for c in list(clients):
   try:c.push(x)
   except:clients.remove(c)
def ffmpeg():
 global proc
 try:
  proc=subprocess.Popen(['ffmpeg','-hide_banner','-loglevel','warning','-fflags','+genpts','-f','flv','-i','pipe:0','-an','-vf','fps=20,scale=1280:-2:force_original_aspect_ratio=decrease','-c:v','mjpeg','-q:v','8','-f','mpjpeg','-boundary_tag','frame','pipe:1'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0);state['ffmpeg']='running'
  def err():
   while proc and proc.poll() is None:
    z=proc.stderr.readline()
    if z:state['last_error']=z.decode('utf8','replace').strip()[-1200:]
  threading.Thread(target=err,daemon=True).start()
  while proc and proc.poll() is None:
   z=proc.stdout.read(32768)
   if z:state['frames']+=z.count(b'--frame');broadcast(z)
 except Exception as e:state['ffmpeg']='error';state['last_error']=repr(e)
def start(p,path):
 global ws,proc
 stop.set();time.sleep(.25);stop.clear();state.update(connected=False,authenticated=False,streaming=False,ffmpeg='starting',packets=0,bytes=0,frames=0,last_error='',port=p,path=path)
 def run():
  global ws
  try:
   threading.Thread(target=ffmpeg,daemon=True).start();ws=connect(p,path);state.update(connected=True,authenticated=True,streaming=True)
   while not stop.is_set():
    z=ws.recv()
    if isinstance(z,str):
     if '"errorCode":401' in z:raise RuntimeError(z)
     continue
    if not z:continue
    state['packets']+=1;state['bytes']+=len(z)
    if proc and proc.stdin:proc.stdin.write(z);proc.stdin.flush()
  except Exception as e:state['streaming']=False;state['last_error']=repr(e)
 threading.Thread(target=run,daemon=True).start()
class Client:
 def __init__(self):self.cv=threading.Condition();self.latest=None
 def push(self,x):
  with self.cv:self.latest=x;self.cv.notify()
 def gen(self):
  while True:
   with self.cv:
    if self.latest is None:self.cv.wait(10)
    x=self.latest;self.latest=None
   if x:yield x
PAGE='''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Uniview Live</title><style>body{background:#111;color:#eee;font-family:Arial;margin:10px}.group{margin:8px 0;padding:8px;background:#1b1b1b;border-radius:8px}.group b{display:block;margin-bottom:6px}button{padding:10px;margin:3px;background:#222;color:#eee;border:1px solid #777;border-radius:6px}button.active{background:#555}img{width:100%;max-width:1280px;background:#000;margin-top:10px}pre{background:#222;padding:8px;white-space:pre-wrap}</style></head><body><h2>Uniview Live</h2><div id="buttons"></div><img id="v" src="/live.mjpg"><pre id="s">loading...</pre><script>const P={{presets|tojson}},b=document.getElementById('buttons');let cam={};for(const [n,v] of Object.entries(P)){let k=n.split(' / ')[0];(cam[k]??=[]).push([n,v])}for(const [k,a] of Object.entries(cam)){let d=document.createElement('div');d.className='group';d.innerHTML='<b>'+k+'</b>';for(const [n,v] of a){let x=document.createElement('button');x.textContent=n.split(' / ')[1];x.onclick=()=>go(n,v,x);d.appendChild(x)}b.appendChild(d)}async function go(n,v,x){document.querySelectorAll('button').forEach(q=>q.classList.remove('active'));x.classList.add('active');await fetch('/switch?port='+v[0]+'&path='+encodeURIComponent(v[1]));document.getElementById('v').src='/live.mjpg?t='+Date.now()}setInterval(async()=>{try{document.getElementById('s').textContent=JSON.stringify(await (await fetch('/status')).json(),null,2)}catch(e){}},1000)</script></body></html>'''
@app.get('/')
def index():return render_template_string(PAGE,presets=PRESETS)
@app.get('/status')
def status():
 with lock:r=dict(state);r['clients']=len(clients);return jsonify(r)
@app.get('/switch')
def switch():
 p=int(request.args['port']);path=request.args['path'];start(p,path);return jsonify(ok=True,port=p,path=path)
@app.get('/live.mjpg')
def live():
 c=Client();clients.append(c)
 def g():
  try:yield from c.gen()
  finally:
   if c in clients:clients.remove(c)
 return Response(g(),mimetype='multipart/x-mixed-replace; boundary=frame',headers={'Cache-Control':'no-cache,no-store','Pragma':'no-cache'})
if __name__=='__main__':start(8001,'/media/flv/video1');app.run(host='0.0.0.0',port=WEB,threaded=True,debug=False)
