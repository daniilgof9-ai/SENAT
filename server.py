from flask import Flask, send_file, request, jsonify
from flask_socketio import SocketIO, join_room, leave_room, send, emit
import json
import os
from datetime import datetime, timedelta
import base64
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = 'senator_secret_key_2026'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB
socketio = SocketIO(app, cors_allowed_origins="*")

# ============ МАРШРУТ ============
@app.route('/')
def index():
    return send_file('templates/index.html')

# ============ ПАПКИ ДЛЯ ФАЙЛОВ ============
UPLOAD_FOLDER = 'uploads'
AVATAR_FOLDER = 'avatars'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(AVATAR_FOLDER, exist_ok=True)

# ============ БАЗЫ ДАННЫХ ============
USERS_FILE = 'users.json'
FRIENDS_FILE = 'friends.json'
SESSIONS_FILE = 'sessions.json'
MESSAGES_FILE = 'messages.json'
BLOCKED_FILE = 'blocked.json'
BANNED_FILE = 'banned.json'
GROUPS_FILE = 'groups.json'

def load_json(file, default):
    if os.path.exists(file):
        try:
            with open(file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return default
    return default

def save_json(file, data):
    with open(file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# Загружаем все данные
users_db = load_json(USERS_FILE, {})
friends_db = load_json(FRIENDS_FILE, {})
sessions_db = load_json(SESSIONS_FILE, {})
messages_db = load_json(MESSAGES_FILE, {"general": [], "private": {}, "groups": {}})
blocked_db = load_json(BLOCKED_FILE, {})
banned_db = load_json(BANNED_FILE, {})
groups_db = load_json(GROUPS_FILE, {})

online_users = {}
admins = ["SENATOR"]  # Только SENATOR админ
user_last_seen = {}

# Инициализация
for username in users_db:
    if username not in friends_db:
        friends_db[username] = {"friends": [], "pending_in": [], "pending_out": []}
    if username not in blocked_db:
        blocked_db[username] = []
    if 'last_seen' not in users_db[username]:
        users_db[username]['last_seen'] = datetime.now().isoformat()
    if 'avatar' not in users_db[username]:
        users_db[username]['avatar'] = '👤'
save_json(FRIENDS_FILE, friends_db)
save_json(BLOCKED_FILE, blocked_db)
save_json(USERS_FILE, users_db)

# ============ ВСПОМОГАТЕЛЬНЫЕ ============
def broadcast_user_list():
    user_list = []
    for sid, u in online_users.items():
        if u not in banned_db:
            user_list.append({
                'username': u, 
                'display_name': users_db[u].get('display_name', u), 
                'is_admin': users_db[u].get('is_admin', False),
                'avatar': users_db[u].get('avatar', '👤'),
                'last_seen': users_db[u].get('last_seen', '')
            })
    emit('user_list', user_list, broadcast=True)

def get_all_users(current_user):
    users = []
    for username in users_db:
        if username != current_user and username not in banned_db:
            users.append({
                'username': username,
                'display_name': users_db[username].get('display_name', username),
                'avatar': users_db[username].get('avatar', '👤'),
                'is_friend': username in friends_db.get(current_user, {}).get('friends', []),
                'is_blocked': username in blocked_db.get(current_user, []),
                'pending_out': username in friends_db.get(current_user, {}).get('pending_out', []),
                'pending_in': username in friends_db.get(current_user, {}).get('pending_in', []),
                'last_seen': users_db[username].get('last_seen', '')
            })
    return users

def update_last_seen(username):
    if username in users_db:
        users_db[username]['last_seen'] = datetime.now().isoformat()
        save_json(USERS_FILE, users_db)

# ============ ЗАГРУЗКА ФАЙЛОВ ============
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No filename'}), 400
    
    filename = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)
    
    return jsonify({'url': f'/uploads/{filename}'})

@app.route('/upload_avatar', methods=['POST'])
def upload_avatar():
    data = request.json
    username = data.get('username')
    image_data = data.get('image')
    
    if not username or not image_data:
        return jsonify({'error': 'No data'}), 400
    
    users_db[username]['avatar'] = image_data
    save_json(USERS_FILE, users_db)
    
    for sid, user in online_users.items():
        emit('avatar_updated', {'username': username, 'avatar': image_data}, room=sid)
    
    return jsonify({'success': True})

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_file(os.path.join(UPLOAD_FOLDER, filename))

# ============ РЕГИСТРАЦИЯ ============
@socketio.on('register')
def handle_register(data):
    username = data['username'].strip()
    password = data.get('password', '').strip()
    display_name = data.get('display_name', username).strip()
    avatar = data.get('avatar', '👤')
    
    if not username or not password:
        emit('register_error', {'msg': '❌ Заполните все поля!'})
        return
    
    if len(username) < 3:
        emit('register_error', {'msg': '❌ Имя должно быть минимум 3 символа'})
        return
    
    if username in users_db:
        emit('register_error', {'msg': '❌ Это имя уже занято!'})
        return
    
    users_db[username] = {
        "password": password,
        "display_name": display_name,
        "avatar": avatar,
        "created": datetime.now().isoformat(),
        "last_seen": datetime.now().isoformat(),
        "is_admin": username in admins
    }
    save_json(USERS_FILE, users_db)
    
    friends_db[username] = {"friends": [], "pending_in": [], "pending_out": []}
    blocked_db[username] = []
    save_json(FRIENDS_FILE, friends_db)
    save_json(BLOCKED_FILE, blocked_db)
    
    emit('register_success', {'username': username})

# ============ ВХОД ============
@socketio.on('login')
def handle_login(data):
    username = data['username'].strip()
    password = data.get('password', '').strip()
    remember = data.get('remember', False)
    
    if username in banned_db:
        emit('login_error', {'msg': '❌ Вы заблокированы. Напишите @SENATOR_DANIIL'})
        return
    
    if username not in users_db:
        emit('login_error', {'msg': '❌ Пользователь не найден'})
        return
    
    if users_db[username]['password'] != password:
        emit('login_error', {'msg': '❌ Неверный пароль'})
        return
    
    for sid, user in online_users.items():
        if user == username:
            emit('login_error', {'msg': '❌ Уже в сети'})
            return
    
    online_users[request.sid] = username
    update_last_seen(username)
    
    if remember:
        sessions_db[request.sid] = username
        save_json(SESSIONS_FILE, sessions_db)
    
    join_room('general')
    
    emit('history', messages_db['general'][-100:])
    
    emit('login_success', {
        'username': username,
        'display_name': users_db[username].get('display_name', username),
        'avatar': users_db[username]['avatar'],
        'is_admin': users_db[username].get('is_admin', False),
        'friends': friends_db.get(username, {}).get('friends', []),
        'pending_in': friends_db.get(username, {}).get('pending_in', []),
        'blocked': blocked_db.get(username, [])
    })
    
    send({
        'username': '🔵 Система',
        'msg': f'✨ {users_db[username]["display_name"]} (@{username}) присоединился',
        'time': datetime.now().strftime('%H:%M'),
        'type': 'system'
    }, room='general')
    
    broadcast_user_list()
    emit('all_users', get_all_users(username), room=request.sid)

# ============ АВТОВХОД ============
@socketio.on('auto_login')
def handle_auto_login():
    if request.sid in sessions_db:
        username = sessions_db[request.sid]
        if username in users_db and username not in banned_db:
            online_users[request.sid] = username
            update_last_seen(username)
            join_room('general')
            
            emit('history', messages_db['general'][-100:])
            
            emit('login_success', {
                'username': username,
                'display_name': users_db[username].get('display_name', username),
                'avatar': users_db[username]['avatar'],
                'is_admin': users_db[username].get('is_admin', False),
                'friends': friends_db.get(username, {}).get('friends', []),
                'pending_in': friends_db.get(username, {}).get('pending_in', []),
                'blocked': blocked_db.get(username, [])
            })
            
            send({
                'username': '🔵 Система',
                'msg': f'✨ С возвращением, {users_db[username]["display_name"]} (@{username})!',
                'time': datetime.now().strftime('%H:%M'),
                'type': 'system'
            }, room='general')
            
            broadcast_user_list()
            emit('all_users', get_all_users(username), room=request.sid)
            return True
    return False

# ============ ПОИСК ============
@socketio.on('search_users')
def handle_search(data):
    if request.sid not in online_users:
        return
    
    query = data.get('query', '').strip().lower()
    current_user = online_users[request.sid]
    
    if len(query) < 1:
        emit('search_results', get_all_users(current_user))
        return
    
    results = []
    for user in get_all_users(current_user):
        if query in user['username'].lower() or query in user['display_name'].lower():
            results.append(user)
    
    emit('search_results', results[:20])

# ============ ЗАЯВКИ В ДРУЗЬЯ ============
@socketio.on('send_friend_request')
def handle_friend_request(data):
    if request.sid not in online_users:
        return
    
    from_user = online_users[request.sid]
    to_user = data.get('to')
    
    if to_user not in users_db:
        emit('friend_error', {'msg': '❌ Пользователь не найден'})
        return
    
    if to_user == from_user:
        emit('friend_error', {'msg': '❌ Нельзя добавить себя'})
        return
    
    if to_user in blocked_db.get(from_user, []):
        emit('friend_error', {'msg': '❌ Пользователь заблокирован'})
        return
    
    if to_user in friends_db[from_user]['friends']:
        emit('friend_error', {'msg': '❌ Уже в друзьях'})
        return
    
    if to_user in friends_db[from_user]['pending_out']:
        emit('friend_error', {'msg': '❌ Заявка уже отправлена'})
        return
    
    friends_db[from_user]['pending_out'].append(to_user)
    friends_db[to_user]['pending_in'].append(from_user)
    save_json(FRIENDS_FILE, friends_db)
    
    emit('friend_request_sent', {'to': to_user})
    
    for sid, user in online_users.items():
        if user == to_user:
            emit('friend_request_received', {
                'from': from_user,
                'display_name': users_db[from_user]['display_name'],
                'avatar': users_db[from_user]['avatar']
            }, room=sid)
            break

@socketio.on('accept_friend_request')
def handle_accept_friend(data):
    if request.sid not in online_users:
        return
    
    current_user = online_users[request.sid]
    from_user = data.get('from')
    
    if from_user not in friends_db[current_user]['pending_in']:
        return
    
    friends_db[current_user]['pending_in'].remove(from_user)
    friends_db[from_user]['pending_out'].remove(current_user)
    friends_db[current_user]['friends'].append(from_user)
    friends_db[from_user]['friends'].append(current_user)
    
    save_json(FRIENDS_FILE, friends_db)
    
    emit('friend_request_accepted', {'username': from_user}, room=request.sid)
    emit('friends_updated', {
        'friends': friends_db[current_user]['friends'],
        'pending_in': friends_db[current_user]['pending_in']
    }, room=request.sid)
    
    for sid, user in online_users.items():
        if user == from_user:
            emit('friend_request_accepted', {'username': current_user}, room=sid)
            emit('friends_updated', {
                'friends': friends_db[from_user]['friends'],
                'pending_in': friends_db[from_user]['pending_in']
            }, room=sid)
            break

@socketio.on('reject_friend_request')
def handle_reject_friend(data):
    if request.sid not in online_users:
        return
    
    current_user = online_users[request.sid]
    from_user = data.get('from')
    
    if from_user in friends_db[current_user]['pending_in']:
        friends_db[current_user]['pending_in'].remove(from_user)
        friends_db[from_user]['pending_out'].remove(current_user)
        save_json(FRIENDS_FILE, friends_db)
        emit('friend_request_rejected', {'username': from_user}, room=request.sid)
        
        for sid, user in online_users.items():
            if user == from_user:
                emit('friend_request_rejected', {'username': current_user}, room=sid)
                break

# ============ ГРУППЫ ============
@socketio.on('create_group')
def handle_create_group(data):
    if request.sid not in online_users:
        return
    
    creator = online_users[request.sid]
    group_name = data.get('name', f"Группа @{creator}")
    
    group_id = f"group_{int(datetime.now().timestamp())}_{creator}"
    groups_db[group_id] = {
        'id': group_id,
        'name': group_name,
        'creator': creator,
        'admins': [creator],
        'members': [creator],
        'created': datetime.now().isoformat(),
        'avatar': '👥'
    }
    save_json(GROUPS_FILE, groups_db)
    
    if 'groups' not in messages_db:
        messages_db['groups'] = {}
    messages_db['groups'][group_id] = []
    save_json(MESSAGES_FILE, messages_db)
    
    emit('group_created', {'id': group_id, 'name': group_name})

@socketio.on('get_groups')
def handle_get_groups():
    if request.sid not in online_users:
        return
    
    username = online_users[request.sid]
    user_groups = []
    for gid, group in groups_db.items():
        if username in group['members']:
            user_groups.append({
                'id': gid, 
                'name': group['name'], 
                'avatar': group.get('avatar', '👥'),
                'members': group['members'],
                'admins': group['admins'],
                'creator': group['creator']
            })
    emit('groups_list', user_groups)

@socketio.on('add_to_group')
def handle_add_to_group(data):
    if request.sid not in online_users:
        return
    
    username = online_users[request.sid]
    group_id = data.get('group_id')
    user_to_add = data.get('username')
    
    if group_id not in groups_db:
        return
    
    group = groups_db[group_id]
    
    if username not in group['admins'] and username != group['creator']:
        emit('group_error', {'msg': '❌ Нет прав'})
        return
    
    if user_to_add not in group['members']:
        group['members'].append(user_to_add)
        save_json(GROUPS_FILE, groups_db)
        emit('group_member_added', {'group_id': group_id, 'username': user_to_add}, room=group_id)

@socketio.on('remove_from_group')
def handle_remove_from_group(data):
    if request.sid not in online_users:
        return
    
    username = online_users[request.sid]
    group_id = data.get('group_id')
    user_to_remove = data.get('username')
    
    if group_id not in groups_db:
        return
    
    group = groups_db[group_id]
    
    if username not in group['admins'] and username != group['creator']:
        emit('group_error', {'msg': '❌ Нет прав'})
        return
    
    if user_to_remove in group['members'] and user_to_remove != group['creator']:
        group['members'].remove(user_to_remove)
        if user_to_remove in group['admins']:
            group['admins'].remove(user_to_remove)
        save_json(GROUPS_FILE, groups_db)
        emit('group_member_removed', {'group_id': group_id, 'username': user_to_remove}, room=group_id)

@socketio.on('update_group')
def handle_update_group(data):
    if request.sid not in online_users:
        return
    
    username = online_users[request.sid]
    group_id = data.get('group_id')
    new_name = data.get('name')
    new_avatar = data.get('avatar')
    
    if group_id not in groups_db:
        return
    
    group = groups_db[group_id]
    
    if username not in group['admins'] and username != group['creator']:
        emit('group_error', {'msg': '❌ Нет прав'})
        return
    
    if new_name:
        group['name'] = new_name
    if new_avatar:
        group['avatar'] = new_avatar
    
    save_json(GROUPS_FILE, groups_db)
    emit('group_updated', {'group_id': group_id, 'name': group['name'], 'avatar': group['avatar']}, room=group_id)

@socketio.on('delete_group')
def handle_delete_group(data):
    if request.sid not in online_users:
        return
    
    username = online_users[request.sid]
    group_id = data.get('group_id')
    
    if group_id not in groups_db:
        return
    
    group = groups_db[group_id]
    
    if username != group['creator']:
        emit('group_error', {'msg': '❌ Только создатель может удалить группу'})
        return
    
    del groups_db[group_id]
    if 'groups' in messages_db and group_id in messages_db['groups']:
        del messages_db['groups'][group_id]
    
    save_json(GROUPS_FILE, groups_db)
    save_json(MESSAGES_FILE, messages_db)
    emit('group_deleted', {'group_id': group_id}, room=group_id)

# ============ БЛОКИРОВКА ============
@socketio.on('block_user')
def handle_block_user(data):
    if request.sid not in online_users:
        return
    
    current_user = online_users[request.sid]
    user_to_block = data.get('username')
    
    if user_to_block not in users_db:
        return
    
    if user_to_block not in blocked_db[current_user]:
        blocked_db[current_user].append(user_to_block)
        save_json(BLOCKED_FILE, blocked_db)
        
        if user_to_block in friends_db[current_user]['friends']:
            friends_db[current_user]['friends'].remove(user_to_block)
            friends_db[user_to_block]['friends'].remove(current_user)
            save_json(FRIENDS_FILE, friends_db)
        
        emit('user_blocked', {'username': user_to_block})

@socketio.on('unblock_user')
def handle_unblock_user(data):
    if request.sid not in online_users:
        return
    
    current_user = online_users[request.sid]
    user_to_unblock = data.get('username')
    
    if user_to_unblock in blocked_db[current_user]:
        blocked_db[current_user].remove(user_to_unblock)
        save_json(BLOCKED_FILE, blocked_db)
        emit('user_unblocked', {'username': user_to_unblock})

# ============ БАН (только для SENATOR) ============
@socketio.on('ban_user')
def handle_ban_user(data):
    if request.sid not in online_users:
        return
    
    admin_user = online_users[request.sid]
    if admin_user not in admins:
        emit('ban_error', {'msg': '❌ Недостаточно прав'})
        return
    
    user_to_ban = data.get('username')
    reason = data.get('reason', 'Нарушение правил')
    
    if user_to_ban in admins:
        emit('ban_error', {'msg': '❌ Нельзя забанить администратора'})
        return
    
    banned_db[user_to_ban] = {
        'reason': reason,
        'banned_by': admin_user,
        'time': datetime.now().isoformat()
    }
    save_json(BANNED_FILE, banned_db)
    
    for sid, user in list(online_users.items()):
        if user == user_to_ban:
            emit('banned', {'reason': reason, 'contact': '@SENATOR_DANIIL'}, room=sid)
            online_users.pop(sid, None)
            leave_room(sid, 'general')
    
    emit('user_banned', {'username': user_to_ban}, broadcast=True)

# ============ УДАЛЕНИЕ СООБЩЕНИЙ (админ может удалять любые) ============
@socketio.on('delete_message')
def handle_delete_message(data):
    if request.sid not in online_users:
        return
    
    username = online_users[request.sid]
    message_id = data.get('id')
    room = data.get('room')
    is_admin = users_db[username].get('is_admin', False)
    
    if room.startswith('private_'):
        if 'private' in messages_db and room in messages_db['private']:
            for i, msg in enumerate(messages_db['private'][room]):
                if msg['id'] == message_id:
                    # Админ может удалить любое, обычный пользователь - только своё
                    if is_admin or msg['username'] == username:
                        messages_db['private'][room].pop(i)
                        save_json(MESSAGES_FILE, messages_db)
                        emit('message_deleted', {'id': message_id, 'room': room}, room=room)
                    break
    elif room.startswith('group_'):
        if 'groups' in messages_db and room in messages_db['groups']:
            for i, msg in enumerate(messages_db['groups'][room]):
                if msg['id'] == message_id:
                    if is_admin or msg['username'] == username:
                        messages_db['groups'][room].pop(i)
                        save_json(MESSAGES_FILE, messages_db)
                        emit('message_deleted', {'id': message_id, 'room': room}, room=room)
                    break
    else:
        if room in messages_db:
            for i, msg in enumerate(messages_db[room]):
                if msg['id'] == message_id:
                    if is_admin or msg['username'] == username:
                        messages_db[room].pop(i)
                        save_json(MESSAGES_FILE, messages_db)
                        emit('message_deleted', {'id': message_id, 'room': room}, room=room)
                    break

# ============ ОЧИСТКА ЧАТА (ИСПРАВЛЕНО) ============
clear_requests = {}

@socketio.on('request_clear_chat')
def handle_request_clear_chat(data):
    if request.sid not in online_users:
        return
    
    user1 = online_users[request.sid]
    user2 = data.get('with_user')
    
    if not user2:
        return
    
    chat_id = f"private_{min(user1, user2)}_{max(user1, user2)}"
    request_id = f"{min(user1, user2)}_{max(user1, user2)}"
    
    if request_id not in clear_requests:
        # Первый запрос
        clear_requests[request_id] = [user1]
        # Отправляем запрос второму пользователю
        for sid, user in online_users.items():
            if user == user2:
                emit('clear_chat_requested', {'from': user1, 'chat': chat_id}, room=sid)
                break
    else:
        # Второй пользователь согласился
        if user2 in clear_requests[request_id] or user1 in clear_requests[request_id]:
            if chat_id in messages_db.get('private', {}):
                messages_db['private'][chat_id] = []
                save_json(MESSAGES_FILE, messages_db)
            del clear_requests[request_id]
            emit('chat_cleared', {'chat': chat_id}, room=chat_id)

# ============ РЕДАКТИРОВАНИЕ СООБЩЕНИЯ ============
@socketio.on('edit_message')
def handle_edit_message(data):
    if request.sid not in online_users:
        return
    
    username = online_users[request.sid]
    message_id = data.get('id')
    new_text = data.get('new_text')[:500]
    room = data.get('room')
    
    if room.startswith('private_'):
        if 'private' in messages_db and room in messages_db['private']:
            for msg in messages_db['private'][room]:
                if msg['id'] == message_id and msg['username'] == username:
                    msg_time = datetime.fromtimestamp(msg['id'])
                    if datetime.now() - msg_time < timedelta(minutes=5):
                        msg['msg'] = new_text
                        msg['edited'] = True
                        msg['edit_time'] = datetime.now().strftime('%H:%M')
                        save_json(MESSAGES_FILE, messages_db)
                        emit('message_edited', {
                            'id': message_id,
                            'new_text': new_text,
                            'room': room,
                            'edit_time': msg['edit_time']
                        }, room=room)
                    break
    elif room.startswith('group_'):
        if 'groups' in messages_db and room in messages_db['groups']:
            for msg in messages_db['groups'][room]:
                if msg['id'] == message_id and msg['username'] == username:
                    msg_time = datetime.fromtimestamp(msg['id'])
                    if datetime.now() - msg_time < timedelta(minutes=5):
                        msg['msg'] = new_text
                        msg['edited'] = True
                        msg['edit_time'] = datetime.now().strftime('%H:%M')
                        save_json(MESSAGES_FILE, messages_db)
                        emit('message_edited', {
                            'id': message_id,
                            'new_text': new_text,
                            'room': room,
                            'edit_time': msg['edit_time']
                        }, room=room)
                    break
    else:
        if room in messages_db:
            for msg in messages_db[room]:
                if msg['id'] == message_id and msg['username'] == username:
                    msg_time = datetime.fromtimestamp(msg['id'])
                    if datetime.now() - msg_time < timedelta(minutes=5):
                        msg['msg'] = new_text
                        msg['edited'] = True
                        msg['edit_time'] = datetime.now().strftime('%H:%M')
                        save_json(MESSAGES_FILE, messages_db)
                        emit('message_edited', {
                            'id': message_id,
                            'new_text': new_text,
                            'room': room,
                            'edit_time': msg['edit_time']
                        }, room=room)
                    break

# ============ СОХРАНЕНИЕ ФАЙЛА ============
@socketio.on('save_file')
def handle_save_file(data):
    if request.sid not in online_users:
        return
    
    file_data = data.get('file_data')
    filename = data.get('filename', 'file')
    
    if file_data and file_data.startswith('data:'):
        # Отправляем файл для скачивания
        emit('file_saved', {'file_data': file_data, 'filename': filename}, room=request.sid)

# ============ СООБЩЕНИЯ ============
@socketio.on('message')
def handle_message(data):
    if request.sid not in online_users:
        return
    
    username = online_users[request.sid]
    msg = data['msg']
    room = data.get('room', 'general')
    reply_to = data.get('reply_to')
    
    if username in banned_db:
        emit('banned', {'reason': banned_db[username].get('reason', '')}, room=request.sid)
        return
    
    if msg and (msg.startswith('data:image') or msg.startswith('data:video') or 
                msg.startswith('data:audio') or msg.startswith('data:application')):
        if len(msg) > 70 * 1024 * 1024:
            emit('message_error', {'msg': '❌ Файл слишком большой'})
            return
    
    msg_data = {
        'id': datetime.now().timestamp(),
        'username': username,
        'display_name': users_db[username]['display_name'],
        'msg': msg,
        'time': datetime.now().strftime('%H:%M'),
        'room': room,
        'avatar': users_db[username]['avatar'],
        'is_admin': users_db[username].get('is_admin', False),
        'reply_to': reply_to,
        'edited': False
    }
    
    if room.startswith('private_'):
        parts = room.replace('private_', '').split('_')
        user1, user2 = parts[0], parts[1]
        
        if (username == user1 and user2 in blocked_db.get(user1, [])) or \
           (username == user2 and user1 in blocked_db.get(user2, [])):
            emit('message_error', {'msg': '❌ Пользователь заблокирован'})
            return
        
        if 'private' not in messages_db:
            messages_db['private'] = {}
        if room not in messages_db['private']:
            messages_db['private'][room] = []
        
        messages_db['private'][room].append(msg_data)
        if len(messages_db['private'][room]) > 100:
            messages_db['private'][room].pop(0)
        
        send(msg_data, room=room)
    elif room.startswith('group_'):
        if 'groups' not in messages_db:
            messages_db['groups'] = {}
        if room not in messages_db['groups']:
            messages_db['groups'][room] = []
        
        messages_db['groups'][room].append(msg_data)
        if len(messages_db['groups'][room]) > 100:
            messages_db['groups'][room].pop(0)
        
        send(msg_data, room=room)
    else:
        if room not in messages_db:
            messages_db[room] = []
        
        messages_db[room].append(msg_data)
        if len(messages_db[room]) > 100:
            messages_db[room].pop(0)
        
        send(msg_data, room=room)
    
    save_json(MESSAGES_FILE, messages_db)

# ============ РЕДАКТИРОВАНИЕ ПРОФИЛЯ ============
@socketio.on('update_profile')
def handle_update_profile(data):
    if request.sid not in online_users:
        return
    
    username = online_users[request.sid]
    new_avatar = data.get('avatar')
    new_display_name = data.get('display_name')
    
    if new_avatar:
        users_db[username]['avatar'] = new_avatar
    if new_display_name:
        users_db[username]['display_name'] = new_display_name
    
    save_json(USERS_FILE, users_db)
    
    for sid, user in online_users.items():
        emit('profile_updated', {
            'username': username,
            'avatar': users_db[username]['avatar'],
            'display_name': users_db[username]['display_name']
        }, room=sid)

# ============ ПЕРЕКЛЮЧЕНИЕ КОМНАТ ============
@socketio.on('join_room')
def handle_join_room(data):
    if request.sid not in online_users:
        return
    
    username = online_users[request.sid]
    new_room = data['room']
    old_room = data.get('old_room', 'general')
    
    if old_room != new_room:
        leave_room(old_room)
        join_room(new_room)
        
        if new_room.startswith('private_'):
            if 'private' in messages_db and new_room in messages_db['private']:
                emit('history', messages_db['private'][new_room][-100:])
        elif new_room.startswith('group_'):
            if 'groups' in messages_db and new_room in messages_db['groups']:
                emit('history', messages_db['groups'][new_room][-100:])
        else:
            emit('history', messages_db.get(new_room, [])[-100:])

# ============ ПОЛУЧИТЬ ИСТОРИЮ ============
@socketio.on('get_history')
def handle_get_history(data):
    room = data.get('room', 'general')
    if room.startswith('private_'):
        if 'private' in messages_db and room in messages_db['private']:
            emit('history', messages_db['private'][room][-100:])
    elif room.startswith('group_'):
        if 'groups' in messages_db and room in messages_db['groups']:
            emit('history', messages_db['groups'][room][-100:])
    else:
        emit('history', messages_db.get(room, [])[-100:])

# ============ ДИСКОННЕКТ ============
@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in online_users:
        username = online_users[request.sid]
        update_last_seen(username)
        del online_users[request.sid]
        
        send({
            'username': '🔵 Система',
            'msg': f'👋 {users_db[username]["display_name"]} (@{username}) покинул чат',
            'time': datetime.now().strftime('%H:%M'),
            'type': 'system'
        }, room='general')
        
        broadcast_user_list()

# ============ ЗАПУСК ============
if __name__ == '__main__':
    print('=' * 60)
    print('🚀 SENAT MESSENGER v10.0 - НОВЫЕ ФИЧИ')
    print('=' * 60)
    print(f'📊 Пользователей: {len(users_db)}')
    print(f'👥 Групп: {len(groups_db)}')
    print(f'👑 Админ: SENATOR')
    print('=' * 60)
    print('✅ НОВОЕ:')
    print('   - Админ может удалять любые сообщения')
    print('   - Кнопка "Сохранить" для файлов')
    print('   - Очистка чата ИСПРАВЛЕНА')
    print('=' * 60)
    print('📱 Сервер на http://localhost:5000')
    print('=' * 60)
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)