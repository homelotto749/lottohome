import os
import io
import json
import random
import uuid
from datetime import datetime

# Веб-сервер и логика
from flask import Flask, render_template, request, redirect, url_for, session, flash
import requests

# База данных Firebase
import firebase_admin
from firebase_admin import credentials, firestore, auth

# Графика и QR
from PIL import Image, ImageDraw, ImageFont
import qrcode

# Облако для картинок
import cloudinary
import cloudinary.uploader

app = Flask(__name__)
# Секретный ключ для сессий (на Render возьмется из переменных, либо дефолтный)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'super_secret_homeloto_key')

# ==========================================
# 1. НАСТРОЙКИ И ПОДКЛЮЧЕНИЯ
# ==========================================

# --- Подключение к Firebase ---
if not firebase_admin._apps:
    # Вариант А: Запуск на Render (ключ берем из Секретов)
    if 'FIREBASE_CRED_JSON' in os.environ:
        cred_dict = json.loads(os.environ['FIREBASE_CRED_JSON'])
        cred = credentials.Certificate(cred_dict)
    # Вариант Б: Запуск на компьютере (ключ берем из файла)
    else:
        cred_path = "cred.json"
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
        else:
            cred = None
            print("CRITICAL WARNING: Ключ Firebase не найден!")

    if cred:
        firebase_admin.initialize_app(cred)

db = firestore.client()

# API Key для входа пользователей (берем из Секретов или вставляем локально)
FIREBASE_API_KEY = os.environ.get('FIREBASE_API_KEY', 'ТВОЙ_API_KEY_ИЗ_FIREBASE_CONSOLE')

# --- Подключение к Cloudinary (для хранения картинок) ---
cloudinary.config(
  cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME'),
  api_key = os.environ.get('CLOUDINARY_API_KEY'),
  api_secret = os.environ.get('CLOUDINARY_API_SECRET')
)

# ==========================================
# 2. ФУНКЦИИ-ХУДОЖНИКИ (Генерация картинок)
# ==========================================

def create_ticket_image(ticket_data):
    """Рисует красивый билет HOMELOTO и отправляет в облако"""
    width, height = 500, 250
    img = Image.new('RGB', (width, height), color='white')
    draw = ImageDraw.Draw(img)
    
    # Фирменный стиль HOMELOTO (Темно-фиолетовый)
    primary_color = "#4B0082" 
    
    # Шапка
    draw.rectangle([(0, 0), (width, 60)], fill=primary_color)
    
    # Шрифты (попытка загрузить Arial, иначе стандартный)
    try:
        font_header = ImageFont.truetype("arial.ttf", 30)
        font_text = ImageFont.truetype("arial.ttf", 18)
        font_nums = ImageFont.truetype("arial.ttf", 22)
    except:
        font_header = ImageFont.load_default()
        font_text = ImageFont.load_default()
        font_nums = ImageFont.load_default()

    # Тексты
    draw.text((20, 15), "HOMELOTO 7/49", font=font_header, fill="white")
    draw.text((350, 20), f"#{ticket_data['ticket_number']}", font=font_text, fill="white")
    
    draw.text((20, 70), f"Тираж: {ticket_data['draw_id']}", font=font_text, fill="black")
    draw.text((20, 95), f"Цена: {ticket_data.get('price', 100)} руб", font=font_text, fill="black")
    
    # Рисуем числа в кружочках
    numbers = ticket_data['numbers']
    start_x, start_y, gap = 30, 150, 65
    for i, num in enumerate(numbers):
        x = start_x + (i * gap)
        y = start_y
        # Круг
        draw.ellipse([x, y, x+50, y+50], outline=primary_color, width=3)
        # Число (центрирование)
        offset = 15 if num < 10 else 10
        draw.text((x + offset, y + 12), str(num), font=font_nums, fill="black")

    # Сохраняем в оперативную память (RAM)
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)

    # Загружаем в Cloudinary
    try:
        res = cloudinary.uploader.upload(img_byte_arr, folder="homeloto_tickets")
        return res['secure_url'] # Возвращаем ссылку на картинку
    except Exception as e:
        print(f"Ошибка загрузки картинки: {e}")
        return "https://via.placeholder.com/500x250?text=Error+Upload"

def create_receipt_image(transaction_id, items, total, date_str, address_text=""):
    """Рисует чек с QR-кодом и отправляет в облако"""
    width, height = 300, 450 + (len(items) * 20)
    img = Image.new('RGB', (width, height), color='white')
    draw = ImageDraw.Draw(img)
    
    try:
        font = ImageFont.load_default()
        font_bold = ImageFont.load_default()
    except: pass
    
    y = 20
    draw.text((80, y), "HOMELOTO CHECK", font=font_bold, fill="black"); y += 30
    
    # Печать адреса (с переносом строк)
    if address_text:
        addr_lines = [address_text[i:i+30] for i in range(0, len(address_text), 30)]
        for line in addr_lines:
            draw.text((20, y), line, font=font, fill="black"); y += 15
        y += 15

    draw.text((20, y), f"Date: {date_str}", font=font, fill="black"); y += 20
    draw.text((20, y), f"ID: {transaction_id[:8]}...", font=font, fill="black"); y += 30
    
    # Список билетов в чеке
    for item in items:
        text = f"#{item['num']} (Draw {item['draw']})"
        draw.text((20, y), text, font=font, fill="black")
        draw.text((220, y), "100 rub", font=font, fill="black")
        y += 20
        
    draw.text((20, y+20), f"TOTAL: {total} RUB", font=font_bold, fill="black")
    
    # QR Код
    qr = qrcode.make(f"CHECK:{transaction_id}")
    qr = qr.resize((100, 100))
    img.paste(qr, (100, y+50))
    
    # Загрузка в облако
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    try:
        res = cloudinary.uploader.upload(img_byte_arr, folder="homeloto_receipts")
        return res['secure_url']
    except Exception as e:
        return ""

# ==========================================
# 3. МАРШРУТЫ САЙТА (ROUTES)
# ==========================================

@app.route('/')
def index():
    if 'user_id' in session:
        return render_template('index.html', role=session.get('role'), email=session.get('email'))
    return redirect(url_for('login'))

# --- АВТОРИЗАЦИЯ ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        # Проверка пароля через Google Identity Toolkit
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}"
        payload = {"email": email, "password": password, "returnSecureToken": True}
        
        try:
            r = requests.post(url, json=payload)
            data = r.json()
            
            if 'error' in data:
                return render_template('login.html', error="Неверный email или пароль")
            
            user_id = data['localId']
            # Узнаем роль из базы
            u_doc = db.collection('users').document(user_id).get()
            role = u_doc.to_dict().get('role', 'none') if u_doc.exists else 'none'
            
            session['user_id'] = user_id
            session['role'] = role
            session['email'] = email
            return redirect(url_for('index'))
            
        except Exception as e:
            return render_template('login.html', error=f"Ошибка входа: {e}")
            
    return render_template('login.html')

@app.route('/register', methods=['POST'])
def register():
    try:
        email = request.form['email']
        password = request.form['password']
        user = auth.create_user(email=email, password=password)
        # Создаем запись с ролью 'none'
        db.collection('users').document(user.uid).set({'email': email, 'role': 'none'})
        return render_template('login.html', error="Регистрация успешна! Ждите активации администратором.")
    except Exception as e:
        return render_template('login.html', error=f"Ошибка регистрации: {e}")

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- ПАНЕЛЬ ОРГАНИЗАТОРА ---
@app.route('/organizer')
def organizer_panel():
    if session.get('role') not in ['org', 'admin']: return redirect(url_for('index'))
    
    draws_ref = db.collection('draws').stream()
    draws_list = [{'id': d.id, **d.to_dict()} for d in draws_ref]
    draws_list.sort(key=lambda x: x['id'], reverse=True)
    
    return render_template('organizer.html', email=session.get('email'), draws=draws_list)

@app.route('/create_draw', methods=['POST'])
def create_draw():
    if session.get('role') not in ['org', 'admin']: return redirect(url_for('index'))
    
    draw_id = request.form['draw_id']
    if db.collection('draws').document(draw_id).get().exists:
        flash(f'Тираж {draw_id} уже существует!', 'error')
        return redirect(url_for('organizer_panel'))
    
    try:
        cnt = int(request.form['ticket_count'])
        jackpot = int(request.form['jackpot'])
    except: return redirect(url_for('organizer_panel'))

    # Создаем тираж
    db.collection('draws').document(draw_id).set({
        'date': request.form['draw_date'],
        'jackpot': jackpot,
        'total_tickets': cnt,
        'status': 'open',
        'winning_numbers': []
    })

    # Генерируем билеты
    batch = db.batch()
    for i in range(1, cnt+1):
        nums = sorted(random.sample(range(1, 50), 7))
        full_id = f"{draw_id}-{i:03d}"
        batch.set(db.collection('tickets').document(full_id), {
            'draw_id': draw_id, 'ticket_number': f"{i:03d}", 'numbers': nums,
            'status': 'available', 'price': 100, 'win_amount': 0
        })
    batch.commit()
    
    flash('Тираж создан!', 'success')
    return redirect(url_for('organizer_panel'))

# --- ПАНЕЛЬ КАССИРА (ПРОДАЖА) ---
@app.route('/cashier')
def cashier_panel():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    
    draws = [{'id': d.id, **d.to_dict()} for d in db.collection('draws').where('status', '==', 'open').stream()]
    sel_draw = request.args.get('draw_id')
    tickets = []
    
    if sel_draw:
        # Берем только доступные билеты
        res = db.collection('tickets').where('draw_id', '==', sel_draw).where('status', '==', 'available').stream()
        tickets = sorted([{'id': t.id, **t.to_dict()} for t in res], key=lambda x: x['ticket_number'])
        
    return render_template('cashier.html', email=session.get('email'), draws=draws, current_draw=sel_draw, tickets=tickets)

@app.route('/buy_tickets', methods=['POST'])
def buy_tickets():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    
    ids = request.form.getlist('ticket_ids')
    if not ids: return redirect(url_for('cashier_panel', draw_id=request.form['draw_id']))
    
    tr_id = str(uuid.uuid4())
    now = datetime.now()
    batch = db.batch()
    sold_data = []
    
    for tid in ids:
        d = db.collection('tickets').document(tid).get().to_dict()
        d['id'] = tid
        sold_data.append(d)
        
        batch.update(db.collection('tickets').document(tid), {
            'status': 'sold', 'purchase_date': now, 'transaction_id': tr_id,
            'payment_method': request.form['payment_method'], 'sold_by': session.get('email')
        })
    batch.commit()
    
    # Генерация картинок (В ОБЛАКО)
    imgs = [create_ticket_image(t) for t in sold_data]
    
    # Адрес магазина для чека
    cfg = db.collection('config').document('main').get()
    addr = cfg.to_dict().get('shop_address', '') if cfg.exists else ''
    
    rec_url = create_receipt_image(tr_id, [{'num': t['ticket_number'], 'draw': t['draw_id']} for t in sold_data], len(ids)*100, now.strftime("%Y-%m-%d %H:%M"), addr)
    
    return render_template('print_view.html', tickets_imgs=imgs, receipt_img=rec_url)

# --- ПРОВЕДЕНИЕ РОЗЫГРЫША ---
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
    
    # Ищем ТОЛЬКО проданные билеты (оптимизировано)
    sold = db.collection('tickets').where('draw_id', '==', did).where('status', '==', 'sold').stream()
    
    batch = db.batch()
    wins = 0
    
    for t in sold:
        mt = len(set(t.to_dict()['numbers']).intersection(set(wn)))
        prize = {2:100, 3:500, 4:2000, 5:10000, 6:50000, 7:jp}.get(mt, 0)
        
        if prize > 0: wins += 1
        
        batch.update(db.collection('tickets').document(t.id), {
            'matches_count': mt, 'win_amount': prize, 'status': 'checked'
        })
    
    batch.update(dr, {'status': 'closed', 'winning_numbers': wn})
    batch.commit()
    
    flash(f'Тираж завершен! Победителей: {wins}', 'success')
    return redirect(url_for('organizer_panel'))

# --- СТАТИСТИКА (Безопасная фильтрация) ---
@app.route('/draw_details/<draw_id>')
def draw_details(draw_id):
    if session.get('role') not in ['org', 'admin']: return redirect(url_for('index'))
    
    dr = db.collection('draws').document(draw_id).get().to_dict()
    dr['id'] = draw_id
    
    # Получаем победителей
    tkts = []
    # Запрос без сложного индекса
    stream = db.collection('tickets').where('draw_id', '==', draw_id).where('win_amount', '>', 0).stream()
    
    target_matches = request.args.get('matches')
    
    for t in stream:
        d = t.to_dict()
        d['id'] = t.id
        
        # Фильтрация в Python
        if target_matches and target_matches != 'all':
            if str(d.get('matches_count')) != target_matches: continue
            
        tkts.append(d)
        
    tkts.sort(key=lambda x: x['win_amount'], reverse=True)
    return render_template('draw_details.html', draw=dr, tickets=tkts)

# --- ПРОВЕРКА И ВЫПЛАТА ---
@app.route('/check_ticket_page')
def check_ticket_page():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    return render_template('check_ticket.html')

@app.route('/check_ticket', methods=['POST'])
def check_ticket():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    
    tid = request.form['ticket_full_id'].strip()
    doc = db.collection('tickets').document(tid).get()
    
    if not doc.exists:
        return render_template('check_ticket.html', message="Билет не найден!", searched_id=tid)
    
    d = doc.to_dict()
    d['id'] = tid
    if d['status'] in ['available', 'sold']:
        return render_template('check_ticket.html', message="Розыгрыш еще не проводился!", searched_id=tid)
        
    return render_template('check_ticket.html', ticket=d, searched_id=tid)

@app.route('/payout', methods=['POST'])
def payout():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    
    ref = db.collection('tickets').document(request.form['ticket_id'])
    if ref.get().to_dict().get('status') != 'paid':
        ref.update({'status': 'paid', 'paid_at': datetime.now(), 'paid_by': session.get('email')})
        flash('Выплачено!', 'success')
    else:
        flash('Уже выплачено!', 'error')
        
    return redirect(url_for('check_ticket_page'))

# --- НАСТРОЙКИ ---
@app.route('/settings')
def settings():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    cfg = db.collection('config').document('main').get()
    addr = cfg.to_dict().get('shop_address', '') if cfg.exists else ''
    return render_template('settings.html', address=addr)

@app.route('/save_settings', methods=['POST'])
def save_settings():
    if session.get('role') not in ['cass', 'admin']: return redirect(url_for('index'))
    db.collection('config').document('main').set({'shop_address': request.form['shop_address']}, merge=True)
    flash('Настройки сохранены', 'success')
    return redirect(url_for('settings'))

if __name__ == '__main__':
    app.run(debug=True)