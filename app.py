import os, json, sqlite3, secrets, hmac, hashlib, smtplib, ssl, urllib.request, urllib.parse
from email.message import EmailMessage
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from flask import Flask, request, jsonify, render_template, session, abort, send_from_directory
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

ROOT=Path(__file__).resolve().parent
app=Flask(__name__, template_folder='.')
app.secret_key=os.getenv('SECRET_KEY') or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=os.getenv('HTTPS_ONLY','0')=='1', MAX_CONTENT_LENGTH=8*1024*1024)
DB=os.getenv('DATABASE_PATH',str(ROOT/'orders.sqlite3'))
UPLOADS=Path(os.getenv('UPLOAD_DIR',str(ROOT/'uploads'))); UPLOADS.mkdir(parents=True,exist_ok=True)
CATALOG=json.loads((ROOT/'catalog.json').read_text())
PRODUCTS={p['id']:p for p in CATALOG}
METHODS=['paypal','venmo','zelle','cashapp','chime']
STATUSES=['New Order','Payment Pending','Paid','Designing','In Production','Ready','Completed','Cancelled']

def db():
    c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c

def init():
    with db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, created TEXT NOT NULL, customer TEXT NOT NULL, email TEXT NOT NULL, phone TEXT, address TEXT, method TEXT NOT NULL, status TEXT NOT NULL, payment_status TEXT NOT NULL, total_cents INTEGER NOT NULL, items TEXT NOT NULL, payment_reference TEXT, paypal_order_id TEXT UNIQUE)''')
        c.execute('''CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, created TEXT NOT NULL, message TEXT NOT NULL)''')
init()

def now(): return datetime.now(timezone.utc).isoformat()
def event(c,oid,message):c.execute('INSERT INTO events(order_id,created,message) VALUES(?,?,?)',(oid,now(),message))
def send_mail(subject,body,to):
    host=os.getenv('SMTP_HOST'); sender=os.getenv('SMTP_FROM'); user=os.getenv('SMTP_USER'); pwd=os.getenv('SMTP_PASSWORD')
    if not host or not sender or not to:return False
    msg=EmailMessage();msg['Subject']=subject;msg['From']=sender;msg['To']=to;msg.set_content(body)
    try:
        with smtplib.SMTP(host,int(os.getenv('SMTP_PORT','587')),timeout=12) as s:
            s.starttls(context=ssl.create_default_context())
            if user:s.login(user,pwd or '')
            s.send_message(msg)
        return True
    except Exception:
        app.logger.exception('Notification delivery failed');return False

def notify(oid,subject,body,customer_email=None):
    owner=os.getenv('OWNER_EMAIL')
    if owner:send_mail(subject,body,owner)
    if customer_email and customer_email!=owner:send_mail(subject,body,customer_email)

def admin_required(f):
    @wraps(f)
    def inner(*a,**kw):
        if not session.get('admin'):return jsonify(error='Admin sign-in required'),401
        return f(*a,**kw)
    return inner

@app.before_request
def reject_cross_origin_writes():
    if request.method in ('POST','PATCH','PUT','DELETE'):
        origin=request.headers.get('Origin')
        if origin and origin.rstrip('/')!=request.host_url.rstrip('/'):
            return jsonify(error='Cross-origin request rejected'),403

@app.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Cache-Control']='no-store' if request.path.startswith('/api/') else 'public, max-age=300'
    return response

@app.get('/')
def home():return render_template('index.html')
@app.get('/manifest.webmanifest')
def manifest():return jsonify(name="Xpress'd Custom Designs",short_name="Xpress'd",start_url='/',display='standalone',background_color='#f9f6f1',theme_color='#1c1816')
@app.get('/api/config')
def config():
    return jsonify(paypal_client_id=os.getenv('PAYPAL_CLIENT_ID',''),paypal_enabled=bool(os.getenv('PAYPAL_CLIENT_ID') and os.getenv('PAYPAL_CLIENT_SECRET')),handles={x:os.getenv('PAY_'+x.upper(),'') for x in METHODS},instructions={x:os.getenv('PAY_'+x.upper()+'_INSTRUCTIONS','') for x in METHODS})
@app.get('/api/products')
def products():return jsonify(CATALOG)

def order_view(row):
    d=dict(row);d['items']=json.loads(d['items']);return d

@app.post('/api/orders')
def create_order():
    v=request.get_json(silent=True) or {}
    customer=str(v.get('customer','')).strip()[:120];email=str(v.get('email','')).strip()[:180]
    method=str(v.get('method','')).lower()
    if not customer or '@' not in email or len(email)<5 or method not in METHODS:return jsonify(error='Please enter a name, email, and payment method'),400
    raw=v.get('items',[])
    if not isinstance(raw,list) or not 1<=len(raw)<=50:return jsonify(error='Cart must contain 1–50 items'),400
    items=[];total=0
    for it in raw:
        try:pid=int(it['id']);qty=int(it['qty'])
        except (KeyError,ValueError,TypeError):return jsonify(error='Invalid cart item'),400
        p=PRODUCTS.get(pid)
        if not p or not 1<=qty<=100:return jsonify(error='Invalid product or quantity'),400
        if p['price_cents'] is None:return jsonify(error=f'{p["name"]} requires a custom quote before checkout'),400
        subtotal=p['price_cents']*qty;total+=subtotal
        items.append({'id':pid,'name':p['name'],'qty':qty,'unit_cents':p['price_cents'],'subtotal_cents':subtotal,'customization':str(it.get('customization',''))[:1500],'photo':str(it.get('photo',''))[:200]})
    if total>5000000:return jsonify(error='Order exceeds online checkout limit'),400
    oid='XP-'+secrets.token_hex(5).upper()
    with db() as c:
        c.execute('INSERT INTO orders(id,created,customer,email,phone,address,method,status,payment_status,total_cents,items) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(oid,now(),customer,email,str(v.get('phone',''))[:80],str(v.get('address',''))[:500],method,'Payment Pending','Unpaid',total,json.dumps(items)))
        event(c,oid,'Order placed; payment pending')
    notify(oid,'New Xpress’d order '+oid,f'New order {oid}\nCustomer: {customer}\nAmount: ${total/100:.2f}\nMethod: {method}\nPayment pending.',email)
    return jsonify(id=oid,total_cents=total,payment_status='Unpaid'),201

@app.post('/api/upload')
def upload():
    f=request.files.get('file')
    if not f or not f.filename:return jsonify(error='Select an image'),400
    ext=Path(secure_filename(f.filename)).suffix.lower()
    if ext not in ('.jpg','.jpeg','.png','.webp'):return jsonify(error='Use JPG, PNG, or WEBP'),400
    head=f.stream.read(16);f.stream.seek(0)
    valid=(head.startswith(b'\xff\xd8\xff') if ext in ('.jpg','.jpeg') else head.startswith(b'\x89PNG\r\n\x1a\n') if ext=='.png' else head.startswith(b'RIFF') and head[8:12]==b'WEBP')
    if not valid:return jsonify(error='Invalid image file'),400
    filename=secrets.token_hex(16)+ext;f.save(UPLOADS/filename)
    return jsonify(photo=filename)

@app.post('/api/admin/login')
def login():
    v=request.get_json(silent=True) or {};password=v.get('password','')
    hashed=os.getenv('ADMIN_PASSWORD_HASH','')
    if not hashed:return jsonify(error='Set ADMIN_PASSWORD_HASH in .env before using dashboard'),503
    if not check_password_hash(hashed,password):return jsonify(error='Invalid password'),401
    session.clear();session['admin']=True;return jsonify(ok=True)
@app.post('/api/admin/logout')
def logout():session.clear();return jsonify(ok=True)
@app.get('/api/admin/orders')
@admin_required
def all_orders():
    with db() as c:
        rows=c.execute('SELECT * FROM orders ORDER BY created DESC LIMIT 500').fetchall()
        events=c.execute('SELECT * FROM events ORDER BY id DESC LIMIT 30').fetchall()
    return jsonify(orders=[order_view(r) for r in rows],events=[dict(e) for e in events])
@app.patch('/api/admin/orders/<oid>')
@admin_required
def update_order(oid):
    v=request.get_json(silent=True) or {};status=v.get('status');paid=v.get('payment_status')
    if status not in STATUSES or paid not in ('Unpaid','Pending Verification','Paid','Refunded'):return jsonify(error='Invalid status'),400
    with db() as c:
        row=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
        if not row:return jsonify(error='Order not found'),404
        if row['payment_status']=='Paid' and paid=='Unpaid':return jsonify(error='Paid orders require explicit refund handling'),400
        c.execute('UPDATE orders SET status=?,payment_status=? WHERE id=?',(status,paid,oid));event(c,oid,'Status: '+status+' / Payment: '+paid)
    notify(oid,'Xpress’d order update '+oid,f'Order {oid}\nStatus: {status}\nPayment: {paid}',row['email'])
    return jsonify(ok=True)
@app.get('/api/admin/uploads/<filename>')
@admin_required
def get_upload(filename):return send_from_directory(UPLOADS,filename)

# PayPal Orders API: create on server, capture on server, verify ID/currency/amount/status.
def paypal_request(path,method='GET',payload=None):
    client=os.getenv('PAYPAL_CLIENT_ID');secret=os.getenv('PAYPAL_CLIENT_SECRET')
    if not client or not secret:raise RuntimeError('PayPal credentials missing')
    base='https://api-m.sandbox.paypal.com' if os.getenv('PAYPAL_MODE','sandbox')=='sandbox' else 'https://api-m.paypal.com'
    import base64
    basic=base64.b64encode((client+':'+secret).encode()).decode()
    auth=urllib.request.Request(base+'/v1/oauth2/token',data=b'grant_type=client_credentials',headers={'Authorization':'Basic '+basic,'Content-Type':'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(auth,timeout=15) as r:token=json.load(r)['access_token']
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
    req=urllib.request.Request(base+path,data=json.dumps(payload).encode() if payload is not None else (b'' if method=='POST' else None),headers=headers,method=method)
    with urllib.request.urlopen(req,timeout=20) as r:return json.load(r)

@app.post('/api/paypal/create/<oid>')
def paypal_create(oid):
    with db() as c:row=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
    if not row or row['method']!='paypal' or row['payment_status']=='Paid':return jsonify(error='Invalid PayPal order'),400
    if row['paypal_order_id']:return jsonify(id=row['paypal_order_id'])
    try:
        data=paypal_request('/v2/checkout/orders','POST',{'intent':'CAPTURE','purchase_units':[{'reference_id':oid,'custom_id':oid,'amount':{'currency_code':'USD','value':f'{row["total_cents"]/100:.2f}'}}]})
    except Exception:app.logger.exception('PayPal create failed');return jsonify(error='PayPal unavailable; try again'),502
    with db() as c:c.execute('UPDATE orders SET paypal_order_id=? WHERE id=?',(data['id'],oid))
    return jsonify(id=data['id'])

@app.post('/api/paypal/capture/<oid>')
def paypal_capture(oid):
    with db() as c:row=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
    if not row or not row['paypal_order_id']:return jsonify(error='No PayPal order'),400
    if row['payment_status']=='Paid':return jsonify(ok=True)
    try:data=paypal_request('/v2/checkout/orders/'+urllib.parse.quote(row['paypal_order_id'])+'/capture','POST')
    except Exception:app.logger.exception('PayPal capture failed');return jsonify(error='Payment not confirmed; contact support'),502
    captures=[cap for pu in data.get('purchase_units',[]) for cap in pu.get('payments',{}).get('captures',[])]
    good=(data.get('id')==row['paypal_order_id'] and data.get('status')=='COMPLETED' and len(captures)==1 and captures[0].get('status')=='COMPLETED' and captures[0].get('amount',{}).get('currency_code')=='USD' and captures[0].get('amount',{}).get('value')==f'{row["total_cents"]/100:.2f}')
    if not good:return jsonify(error='Payment not verified; contact support'),409
    with db() as c:
        c.execute("UPDATE orders SET payment_status='Paid',status='Paid',payment_reference=? WHERE id=?",(captures[0]['id'],oid));event(c,oid,'PayPal payment verified')
    notify(oid,'Payment confirmed '+oid,f'Payment confirmed for order {oid}.',row['email'])
    return jsonify(ok=True)

@app.post('/api/orders/<oid>/reference')
def reference(oid):
    v=request.get_json(silent=True) or {};email=str(v.get('email','')).strip();ref=str(v.get('reference','')).strip()[:180]
    with db() as c:
        row=c.execute('SELECT * FROM orders WHERE id=? AND lower(email)=lower(?)',(oid,email)).fetchone()
        if not row or row['method']=='paypal' or not ref:return jsonify(error='Invalid order or reference'),400
        c.execute("UPDATE orders SET payment_reference=?,payment_status='Pending Verification' WHERE id=?",(ref,oid));event(c,oid,'Customer submitted payment reference; manual verification required')
    notify(oid,'Payment reference submitted '+oid,f'Order {oid}\nPayment reference: {ref}\nVerify in your payment account before marking paid.')
    return jsonify(ok=True)

if __name__=='__main__':app.run(host='127.0.0.1',port=int(os.getenv('PORT','5000')),debug=False)
