import os
import io
import json
import random
import uuid
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, session, flash
import requests
import firebase_admin
from firebase_admin import credentials, firestore, auth
from PIL import Image, ImageDraw, ImageFont
import qrcode
import barcode
from barcode.writer import ImageWriter
import cloudinary
import cloudinary.uploader

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'super_secret_key')

# --- НАСТРОЙКИ ---
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
    if cred: firebase_admin.initialize_app(cred)

db = firestore.client()
FIREBASE_API_KEY = os.environ.get('FIREBASE_API_KEY', 'LOCAL')

cloudinary.config(
  cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME'),
  api_key = os.environ.get('CLOUDINARY_API_KEY'),
  api_secret = os.environ.get('CLOUDINARY_API_SECRET')
)

# --- ГЕНЕРАТОР БИЛЕТА (НОВЫЙ ШТРИХ-КОД) ---
def create_ticket_image(ticket_data, tr_id, broadcast_link=None):
    """
    tr_id - это ID покупки (транзакции), который пойдет в штрих-код
    """
    width, height = 650, 280
    img = Image.new('RGB', (width, height), color='white')
    draw = ImageDraw.Draw(img)
    primary_color = "#4B0082" 
    
    # Шрифты
    try:
        font_path = os.path.join(os.path.dirname(__file__), 'font.ttf')
        font_header = ImageFont.truetype(font_path, 28)
        font_text = ImageFont.truetype(font_path, 18)
        font_nums = ImageFont.truetype(font_path, 24)
        font_small = ImageFont.truetype(font_path, 12)
        # Шрифт для вертикального текста ID
        font_id = ImageFont.truetype(font_path, 14) 
    except:
        font_header = ImageFont.load_default()
        font_text = ImageFont.load_default()
        font_nums = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_id = ImageFont.load_default()

    # Дизайн
    draw.rectangle([(0, 0), (width, 60)], fill=primary_color)
    draw.text((20, 15), "HOMELOTO 7/49", font=font_header, fill="white")
    
    full_ticket_id = f"{ticket_data['draw_id']}-{ticket_data['ticket_number']}"
    draw.text((450, 20), f"#{full_ticket_id}", font=font_header, fill="white")
    
    date_text = str(ticket_data.get('draw_date', '---')).replace('T', ' ')
    draw.text((20, 70), f"Тираж: {ticket_data['draw_id']}", font=font_text, fill="black")
    draw.text((150, 70), f"Дата: {date_text}", font=font_text, fill="black")
    draw.text((20, 100), f"Цена: 100 руб", font=font_text, fill="black")
    
    # Числа
    numbers = ticket_data['numbers']
    start_x, start_y, gap = 30, 160, 65
    for i, num in enumerate(numbers):
        x = start_x + (i * gap)
        y = start_y
        draw.ellipse([x, y, x+50, y+50], outline=primary_color, width=3)
        if hasattr(draw, 'textlength'):
             txt_w = draw.textlength(str(num), font=font_nums)
             txt_x = x + (50 - txt_w) / 2
        else:
             txt_x = x + 15
        draw.text((txt_x, y + 12), str(num), font=font_nums, fill="black")

    # --- ШТРИХ-КОД (ID ПОКУПКИ) ---
    try:
        rv = io.BytesIO()
        Code128 = barcode.get_barcode_class('code128')
        # Кодируем ID ПОКУПКИ (tr_id), а не номер билета!
        my_barcode = Code128(tr_id, writer=ImageWriter())
        my_barcode.write(rv, options={'text_distance': 1, 'module_height': 8, 'write_text': False})
        rv.seek(0)
        
        # Сам штрих-код
        bc_img = Image.open(rv).rotate(90, expand=True)
        bc_img.thumbnail((50, 200)) # Чуть уже
        img.paste(bc_img, (580, 70))
        
        # --- ВЕРТИКАЛЬНЫЙ ТЕКСТ ID (Рядом со штрихом) ---
        # Создаем временную картинку для текста
        txt_img = Image.new('RGBA', (200, 30), (255, 255, 255, 0))
        txt_draw = ImageDraw.Draw(txt_img)
        txt_draw.text((0, 0), f"TR: {tr_id}", font=font_id, fill="black")
        # Поворачиваем
        txt_rotated = txt_img.rotate(90, expand=True)
        # Вставляем левее штрих-кода
        img.paste(txt_rotated, (550, 70), txt_rotated)

    except Exception as e:
        print(f"Barcode Error: {e}")
        draw.rectangle([(580, 70), (620, 200)], outline="#eee")

    # QR
    if broadcast_link:
        try:
            qr = qrcode.make(broadcast_link).resize((80, 80))
            img.paste(qr, (450, 70))
            draw.text((450, 155), "Live", font=font_small, fill="black")
        except: pass

    # Загрузка
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    
    try:
        res = cloudinary.uploader.upload(img_byte_arr, folder="homeloto_tickets")
        return res['secure_url']
    except Exception as e:
        print(f"Cloudinary Error: {e}")
        return "https://via.placeholder.com/650x280?text=Error+Cloudinary"

def create_receipt_image(transaction_id, items, total, date_str, address_text=""):
    width, height = 300, 450 + (len(items) * 20)
    img = Image.new('RGB', (width, height), color='white')
    draw = ImageDraw.Draw(img)
    try: font = ImageFont.truetype(os.path.join(os.path.dirname(__file__), 'font.ttf'), 14)
    except: font = ImageFont.load_default()
    
    y = 20
    draw.text((80, y), "HOMELOTO CHECK", font=font, fill="black"); y += 30
    if address_text:
        for i in range(0, len(address_text), 30):
            draw.text((20, y), address_text[i:i+30], font=font, fill="black"); y += 15
        y += 15
    draw.text((20, y), f"Date: {date_str}", font=font, fill="black"); y += 20
    draw.text((20, y), f"ID: {transaction_id}", font=font, fill="black"); y += 30
    
    for item in items:
        draw.text((20, y), f"#{item['num']} (T-{item['draw']}) 100r", font=font, fill="black"); y += 20
    draw.text((20, y+20), f"TOTAL: {total} RUB", font=font, fill="black")
    
    qr = qrcode.make(f"CHECK:{transaction_id}").resize((100, 100))
    img.paste(qr, (100, y+50))
    
    buf = io.BytesIO()
    img.save(buf, format='PNG'); buf.seek(0)
    try: return cloudinary.uploader.upload(buf, folder="homeloto_receipts")['secure_url']
    except: return ""

# --- МАРШРУТЫ ---
@app.route('/')
def index():
    if 'user_id' in session: return render_template('index.html', role=session.get('role'), email=session.get('email'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']; password = request.form['password']
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}"
        try:
            r = requests.post(url, json={"email": email, "password": password, "returnSecureToken": True}).json()
            if 'error' in r: return render_template('login.html', error="Ошибка входа")
            uid = r['localId']
            u = db.collection('users').document(uid).get()
            session['user_id'] = uid; session['email'] = email; session['role'] = u.to_dict().get('role', 'none') if u.exists else 'none'
            return redirect(url_for('index'))
        except: return render_template('login.html', error="Ошибка")
    return render_template('login.html')

@app.route('/register', methods=['POST'])
def register():
    try:
        u = auth.create_user(email=request.form['email'], password=request.form['password'])
        db.collection('users').document(u.uid).set({'email': request.form['email'], 'role': 'none'})
        return render_template('login.html', error="Успешно! Ждите активации.")
    except: return render_template('login.html', error="Ошибка регистрации")

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/organizer')
def organizer_panel():
    if session.get('role') not in ['org', 'admin']: return redirect(url_for('index'))
    dl = [{'id': d.id, **d.to_dict()} for d in db.collection('draws').stream()]
    dl.sort(key=lambda x: x['id'], reverse=True)
    return render_template('organizer.html', email=session.get('email'), draws=dl)

@app.route('/create_draw', methods=['POST'])
def create_draw():
    did = request.form['draw_id']
    if db.collection('draws').document(did).get().exists: return redirect(url_for('organizer_panel'))
    cnt = int(request.form['ticket_count'])
    db.collection('draws').document(did).set({
        'date': request.form['draw_date'], 'jackpot': int(request.form['jackpot']),
        'total_tickets': cnt, 'broadcast_link': request.form.get('broadcast_link', ''),
        'status': 'open', 'winning_numbers': []
    })
    batch = db.batch()
    for i in range(1, cnt+1):
        nums = sorted(random.sample(range(1, 50), 7))
        batch.set(db.collection('tickets').document(f"{did}-{i:03d}"), {
            'draw_id': did, 'ticket_number': f"{i:03d}", 'numbers': nums,
            'status': 'available', 'price': 100, 'win_amount': 0, 'draw_date': request.form['draw_date']
        })
    batch.commit()
    return redirect(url_for('organizer_panel'))

@app.route('/cashier')
def cashier_panel():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    draws = [{'id': d.id, **d.to_dict()} for d in db.collection('draws').where('status', '==', 'open').stream()]
    sel = request.args.get('draw_id'); tkts = []
    if sel:
        res = db.collection('tickets').where('draw_id', '==', sel).where('status', '==', 'available').stream()
        tkts = sorted([{'id': t.id, **t.to_dict()} for t in res], key=lambda x: x['ticket_number'])
    return render_template('cashier.html', email=session.get('email'), draws=draws, current_draw=sel, tickets=tkts)

@app.route('/buy_tickets', methods=['POST'])
def buy_tickets():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    ids = request.form.getlist('ticket_ids')
    if not ids: return redirect(url_for('cashier_panel'))
    
    # 1. Генерируем КОРОТКИЙ цифровой ID транзакции (для штрих-кода)
    # Пример: 202310251430 + случайное число (16 цифр)
    tr_id = datetime.now().strftime("%Y%m%d%H%M%S") + str(random.randint(10, 99))
    now = datetime.now()
    
    # 2. Обновляем билеты
    batch = db.batch()
    sold_data = []
    for tid in ids:
        d = db.collection('tickets').document(tid).get().to_dict(); d['id'] = tid
        sold_data.append(d)
        batch.update(db.collection('tickets').document(tid), {
            'status': 'sold', 
            'purchase_date': now, 
            'transaction_id': tr_id, # Сохраняем ID транзакции в билет
            'payment_method': request.form['payment_method'], 
            'sold_by': session.get('email')
        })
    batch.commit()
    
    # 3. Генерируем картинки
    draw_info = db.collection('draws').document(request.form.get('draw_id')).get().to_dict()
    # Передаем tr_id в генератор билета для штрих-кода!
    imgs = [create_ticket_image(t, tr_id, draw_info.get('broadcast_link')) for t in sold_data]
    
    cfg = db.collection('config').document('main').get()
    addr = cfg.to_dict().get('shop_address', '') if cfg.exists else ''
    rec_url = create_receipt_image(tr_id, [{'num': t['ticket_number'], 'draw': t['draw_id']} for t in sold_data], len(ids)*100, now.strftime("%Y-%m-%d %H:%M"), addr)
    
    # 4. СОХРАНЯЕМ ТРАНЗАКЦИЮ В ИСТОРИЮ (Для Этапа 2)
    # Создаем документ в новой коллекции transactions
    db.collection('transactions').document(tr_id).set({
        'id': tr_id,
        'date': now,
        'amount': len(ids)*100,
        'seller': session.get('email'),
        'tickets': [t['id'] for t in sold_data], # Список ID билетов
        'ticket_urls': imgs, # Сохраняем ссылки на билеты!
        'receipt_url': rec_url # Сохраняем ссылку на чек!
    })

    return render_template('print_view.html', tickets_imgs=imgs, receipt_img=rec_url)

@app.route('/play_draw/<draw_id>')
def play_draw_page(draw_id): return render_template('play_draw.html', draw_id=draw_id)

@app.route('/run_draw_logic', methods=['POST'])
def run_draw_logic():
    did = request.form['draw_id']; wn = sorted([int(request.form[f'n{i}']) for i in range(1, 8)])
    dr = db.collection('draws').document(did); jp = dr.get().to_dict().get('jackpot', 10000)
    batch = db.batch(); wins = 0
    for t in db.collection('tickets').where('draw_id', '==', did).where('status', '==', 'sold').stream():
        mt = len(set(t.to_dict()['numbers']).intersection(set(wn)))
        pz = {2:100, 3:500, 4:2000, 5:10000, 6:50000, 7:jp}.get(mt, 0)
        if pz > 0: wins += 1
        batch.update(db.collection('tickets').document(t.id), {'matches_count': mt, 'win_amount': pz, 'status': 'checked'})
    batch.update(dr, {'status': 'closed', 'winning_numbers': wn}); batch.commit()
    flash(f'Победителей: {wins}', 'success'); return redirect(url_for('organizer_panel'))

@app.route('/draw_details/<draw_id>')
def draw_details(draw_id):
    if session.get('role') not in ['org', 'admin']: return redirect(url_for('index'))
    dr = db.collection('draws').document(draw_id).get().to_dict(); dr['id'] = draw_id
    tkts = []
    for t in db.collection('tickets').where('draw_id', '==', draw_id).where('win_amount', '>', 0).stream():
        d = t.to_dict(); d['id'] = t.id
        if request.args.get('matches') and request.args.get('matches') != 'all':
            if str(d.get('matches_count')) != request.args.get('matches'): continue
        tkts.append(d)
    tkts.sort(key=lambda x: x['win_amount'], reverse=True)
    return render_template('draw_details.html', draw=dr, tickets=tkts)

@app.route('/check_ticket_page')
def check_ticket_page(): return render_template('check_ticket.html')

@app.route('/check_ticket', methods=['POST'])
def check_ticket():
    tid = request.form['ticket_full_id'].strip()
    doc = db.collection('tickets').document(tid).get()
    if not doc.exists: return render_template('check_ticket.html', message="Не найден!", searched_id=tid)
    return render_template('check_ticket.html', ticket={**doc.to_dict(), 'id': tid}, searched_id=tid)

@app.route('/payout', methods=['POST'])
def payout():
    db.collection('tickets').document(request.form['ticket_id']).update({'status': 'paid'})
    return redirect(url_for('check_ticket_page'))

@app.route('/settings')
def settings():
    cfg = db.collection('config').document('main').get()
    return render_template('settings.html', address=cfg.to_dict().get('shop_address', '') if cfg.exists else '')

@app.route('/save_settings', methods=['POST'])
def save_settings():
    db.collection('config').document('main').set({'shop_address': request.form['shop_address']}, merge=True)
    return redirect(url_for('settings'))

if __name__ == '__main__':
    app.run(debug=True)
