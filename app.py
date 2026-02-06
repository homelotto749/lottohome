import os
import io
import json
import random
import uuid
from datetime import datetime

# Веб-сервер
from flask import Flask, render_template, request, redirect, url_for, session, flash
import requests

# База данных
import firebase_admin
from firebase_admin import credentials, firestore, auth

# Графика и QR
from PIL import Image, ImageDraw, ImageFont
import qrcode

# Штрих-коды (Оставляем!)
import barcode
from barcode.writer import ImageWriter

# Облако
import cloudinary
import cloudinary.uploader

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'super_secret_homeloto_key')

# ==========================================
# 1. НАСТРОЙКИ
# ==========================================

# Firebase
if not firebase_admin._apps:
    if 'FIREBASE_CRED_JSON' in os.environ:
        cred_dict = json.loads(os.environ['FIREBASE_CRED_JSON'])
        cred = credentials.Certificate(cred_dict)
    else:
        cred_path = "cred.json"
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
        else:
            cred = None
            print("CRITICAL: Ключ Firebase не найден!")

    if cred:
        firebase_admin.initialize_app(cred)

db = firestore.client()
FIREBASE_API_KEY = os.environ.get('FIREBASE_API_KEY', 'LOCAL_KEY')

# Cloudinary
cloudinary.config(
  cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME'),
  api_key = os.environ.get('CLOUDINARY_API_KEY'),
  api_secret = os.environ.get('CLOUDINARY_API_SECRET')
)

# ==========================================
# 2. ГЕНЕРАЦИЯ КАРТИНОК (ХУДОЖНИК)
# ==========================================

def create_ticket_image(ticket_data, broadcast_link=None):
    print(f"--- НАЧИНАЮ РИСОВАТЬ БИЛЕТ {ticket_data['ticket_number']} ---")
    
    try:
        # 1. Рисуем простую картинку (Красный фон, как в тесте)
        # Размер поменьше, чтобы летело быстро
        img = Image.new('RGB', (400, 200), color='#4B0082') # Фиолетовый фон
        draw = ImageDraw.Draw(img)
        
        # Используем встроенный дефолтный шрифт (мелкий, но надежный)
        font = ImageFont.load_default()
        
        # Пишем текст
        text = f"HOMELOTO\nTICKET #{ticket_data['ticket_number']}\nDraw: {ticket_data['draw_id']}"
        draw.text((10, 10), text, fill="white", font=font)
        
        # Рисуем числа
        nums = str(ticket_data['numbers'])
        draw.text((10, 80), f"Numbers: {nums}", fill="white", font=font)

        # 2. Сохраняем в память
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        
        # 3. Загружаем
        print("--- ОТПРАВЛЯЮ В CLOUDINARY ---")
        res = cloudinary.uploader.upload(img_byte_arr, folder="homeloto_tickets")
        
        # 4. ЛОГИРУЕМ ССЫЛКУ (Смотри в Logs на Render!)
        url = res['secure_url']
        print(f"!!! УСПЕХ !!! ССЫЛКА ПОЛУЧЕНА: {url}")
        
        return url
        
    except Exception as e:
        print(f"!!! ОШИБКА ПРИ СОЗДАНИИ БИЛЕТА: {e} !!!")
        return "https://via.placeholder.com/400x200?text=ERROR"

def create_receipt_image(transaction_id, items, total, date_str, address_text=""):
    """Рисует чек"""
    width, height = 300, 450 + (len(items) * 20)
    img = Image.new('RGB', (width, height), color='white')
    draw = ImageDraw.Draw(img)
    
    try:
        font = ImageFont.truetype("font.ttf", 14)
        font_bold = ImageFont.truetype("font.ttf", 16)
    except:
        font = ImageFont.load_default()
        font_bold = ImageFont.load_default()
    
    y = 20
    draw.text((80, y), "HOMELOTO CHECK", font=font_bold, fill="black"); y += 30
    
    if address_text:
        addr_lines = [address_text[i:i+30] for i in range(0, len(address_text), 30)]
        for line in addr_lines:
            draw.text((20, y), line, font=font, fill="black"); y += 15
        y += 15

    draw.text((20, y), f"Date: {date_str}", font=font, fill="black"); y += 20
    draw.text((20, y), f"ID: {transaction_id[:8]}...", font=font, fill="black"); y += 30
    
    for item in items:
        text = f"#{item['num']} (T-{item['draw']})"
        draw.text((20, y), text, font=font, fill="black")
        draw.text((220, y), "100р", font=font, fill="black")
        y += 20
        
    draw.text((20, y+20), f"ИТОГО: {total} РУБ", font=font_bold, fill="black")
    
    qr = qrcode.make(f"CHECK:{transaction_id}")
    qr = qr.resize((100, 100))
    img.paste(qr, (100, y+50))

    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    try:
        res = cloudinary.uploader.upload(img_byte_arr, folder="homeloto_receipts")
        return res['secure_url']
    except: return ""

# ==========================================
# 3. МАРШРУТЫ (ROUTES)
# ==========================================

@app.route('/')
def index():
    if 'user_id' in session:
        return render_template('index.html', role=session.get('role'), email=session.get('email'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}"
        try:
            r = requests.post(url, json={"email": email, "password": password, "returnSecureToken": True})
            data = r.json()
            if 'error' in data: return render_template('login.html', error="Неверный email или пароль")
            uid = data['localId']
            u_doc = db.collection('users').document(uid).get()
            role = u_doc.to_dict().get('role', 'none') if u_doc.exists else 'none'
            session['user_id'] = uid; session['role'] = role; session['email'] = email
            return redirect(url_for('index'))
        except Exception as e: return render_template('login.html', error=str(e))
    return render_template('login.html')

@app.route('/register', methods=['POST'])
def register():
    try:
        user = auth.create_user(email=request.form['email'], password=request.form['password'])
        db.collection('users').document(user.uid).set({'email': request.form['email'], 'role': 'none'})
        return render_template('login.html', error="Регистрация успешна! Ждите активации.")
    except Exception as e: return render_template('login.html', error=str(e))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- ОРГАНИЗАТОР ---
@app.route('/organizer')
def organizer_panel():
    if session.get('role') not in ['org', 'admin']: return redirect(url_for('index'))
    dl = [{'id': d.id, **d.to_dict()} for d in db.collection('draws').stream()]
    dl.sort(key=lambda x: x['id'], reverse=True)
    return render_template('organizer.html', email=session.get('email'), draws=dl)

@app.route('/create_draw', methods=['POST'])
def create_draw():
    if session.get('role') not in ['org', 'admin']: return redirect(url_for('index'))
    
    did = request.form['draw_id']
    if db.collection('draws').document(did).get().exists:
        flash(f'Тираж {did} уже существует!', 'error'); return redirect(url_for('organizer_panel'))
    
    try:
        cnt = int(request.form['ticket_count'])
        jackpot = int(request.form['jackpot'])
        link = request.form.get('broadcast_link', '')
    except: return redirect(url_for('organizer_panel'))

    db.collection('draws').document(did).set({
        'date': request.form['draw_date'],
        'jackpot': jackpot,
        'total_tickets': cnt,
        'broadcast_link': link,
        'status': 'open',
        'winning_numbers': []
    })

    batch = db.batch()
    for i in range(1, cnt+1):
        nums = sorted(random.sample(range(1, 50), 7))
        full_id = f"{did}-{i:03d}"
        batch.set(db.collection('tickets').document(full_id), {
            'draw_id': did, 'ticket_number': f"{i:03d}", 'numbers': nums,
            'status': 'available', 'price': 100, 'win_amount': 0,
            'draw_date': request.form['draw_date']
        })
    batch.commit()
    flash('Тираж создан!', 'success'); return redirect(url_for('organizer_panel'))

# --- КАССИР ---
@app.route('/cashier')
def cashier_panel():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    draws = [{'id': d.id, **d.to_dict()} for d in db.collection('draws').where('status', '==', 'open').stream()]
    sel = request.args.get('draw_id')
    tkts = []
    if sel:
        res = db.collection('tickets').where('draw_id', '==', sel).where('status', '==', 'available').stream()
        tkts = sorted([{'id': t.id, **t.to_dict()} for t in res], key=lambda x: x['ticket_number'])
    return render_template('cashier.html', email=session.get('email'), draws=draws, current_draw=sel, tickets=tkts)

@app.route('/buy_tickets', methods=['POST'])
def buy_tickets():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    
    ids = request.form.getlist('ticket_ids')
    draw_id = request.form.get('draw_id')
    if not ids: return redirect(url_for('cashier_panel', draw_id=draw_id))
    
    tr_id = str(uuid.uuid4()); now = datetime.now()
    batch = db.batch(); sold_data = []
    
    for tid in ids:
        d = db.collection('tickets').document(tid).get().to_dict(); d['id'] = tid
        sold_data.append(d)
        batch.update(db.collection('tickets').document(tid), {
            'status': 'sold', 'purchase_date': now, 'transaction_id': tr_id,
            'payment_method': request.form['payment_method'], 'sold_by': session.get('email')
        })
    batch.commit()
    
    draw_info = db.collection('draws').document(draw_id).get().to_dict()
    broadcast_link = draw_info.get('broadcast_link')

    imgs = [create_ticket_image(t, broadcast_link) for t in sold_data]
    
    cfg = db.collection('config').document('main').get()
    addr = cfg.to_dict().get('shop_address', '') if cfg.exists else ''
    
    rec_url = create_receipt_image(tr_id, [{'num': t['ticket_number'], 'draw': t['draw_id']} for t in sold_data], len(ids)*100, now.strftime("%Y-%m-%d %H:%M"), addr)
    
    return render_template('print_view.html', tickets_imgs=imgs, receipt_img=rec_url)

# --- РОЗЫГРЫШ ---
@app.route('/play_draw/<draw_id>')
def play_draw_page(draw_id):
    if session.get('role') not in ['org', 'admin']: return redirect(url_for('index'))
    return render_template('play_draw.html', draw_id=draw_id)

@app.route('/run_draw_logic', methods=['POST'])
def run_draw_logic():
    if session.get('role') not in ['org', 'admin']: return redirect(url_for('index'))
    did = request.form['draw_id']
    try:
        wn = sorted([int(request.form[f'n{i}']) for i in range(1, 8)])
        if len(set(wn)) != 7: raise ValueError
    except: return redirect(url_for('play_draw_page', draw_id=did))
    
    dr = db.collection('draws').document(did)
    jp = dr.get().to_dict().get('jackpot', 10000)
    sold = db.collection('tickets').where('draw_id', '==', did).where('status', '==', 'sold').stream()
    
    batch = db.batch(); wins = 0
    for t in sold:
        mt = len(set(t.to_dict()['numbers']).intersection(set(wn)))
        pz = {2:100, 3:500, 4:2000, 5:10000, 6:50000, 7:jp}.get(mt, 0)
        if pz > 0: wins += 1
        batch.update(db.collection('tickets').document(t.id), {'matches_count': mt, 'win_amount': pz, 'status': 'checked'})
    batch.update(dr, {'status': 'closed', 'winning_numbers': wn})
    batch.commit()
    flash(f'Тираж завершен! Победителей: {wins}', 'success')
    return redirect(url_for('organizer_panel'))

# --- СТАТИСТИКА ---
@app.route('/draw_details/<draw_id>')
def draw_details(draw_id):
    if session.get('role') not in ['org', 'admin']: return redirect(url_for('index'))
    dr = db.collection('draws').document(draw_id).get().to_dict(); dr['id'] = draw_id
    tkts = []
    stream = db.collection('tickets').where('draw_id', '==', draw_id).where('win_amount', '>', 0).stream()
    matches_filter = request.args.get('matches')
    for t in stream:
        d = t.to_dict(); d['id'] = t.id
        if matches_filter and matches_filter != 'all':
            if str(d.get('matches_count')) != matches_filter: continue
        tkts.append(d)
    tkts.sort(key=lambda x: x['win_amount'], reverse=True)
    return render_template('draw_details.html', draw=dr, tickets=tkts)

@app.route('/check_ticket_page')
def check_ticket_page():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    return render_template('check_ticket.html')

@app.route('/check_ticket', methods=['POST'])
def check_ticket():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    tid = request.form['ticket_full_id'].strip()
    doc = db.collection('tickets').document(tid).get()
    if not doc.exists: return render_template('check_ticket.html', message="Билет не найден!", searched_id=tid)
    d = doc.to_dict(); d['id'] = tid
    if d['status'] in ['available', 'sold']: return render_template('check_ticket.html', message="Розыгрыш еще не проводился!", searched_id=tid)
    return render_template('check_ticket.html', ticket=d, searched_id=tid)

@app.route('/payout', methods=['POST'])
def payout():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    ref = db.collection('tickets').document(request.form['ticket_id'])
    if ref.get().to_dict().get('status') != 'paid':
        ref.update({'status': 'paid', 'paid_at': datetime.now(), 'paid_by': session.get('email')})
        flash('Выплачено!', 'success')
    return redirect(url_for('check_ticket_page'))

@app.route('/settings')
def settings():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    cfg = db.collection('config').document('main').get()
    return render_template('settings.html', address=cfg.to_dict().get('shop_address', '') if cfg.exists else '')

@app.route('/save_settings', methods=['POST'])
def save_settings():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    db.collection('config').document('main').set({'shop_address': request.form['shop_address']}, merge=True)
    flash('Сохранено', 'success'); return redirect(url_for('settings'))

if __name__ == '__main__':
    app.run(debug=True)



