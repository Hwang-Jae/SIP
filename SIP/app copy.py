import os
import uuid
from datetime import datetime
from flask import Flask, request, render_template_string, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
from clickhouse_driver import Client

app = Flask(__name__)
# 세션 및 플래시 메시지를 위한 시크릿 키
app.secret_key = 'smart_factory_secret_key_123!'

# 로컬 스토리지(이미지 저장소) 설정
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 허용할 이미지 확장자
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'}

# --- ClickHouse 설정 ---
# 본인의 환경에 맞게 Host, Port, User, Password를 수정하세요.
CH_HOST = 'localhost'
CH_PORT = 9000
CH_USER = 'default'
CH_PASSWORD = ''
CH_DATABASE = 'default'

def get_ch_client():
    """ClickHouse 클라이언트 객체 생성"""
    return Client(host=CH_HOST, port=CH_PORT, user=CH_USER, password=CH_PASSWORD, database=CH_DATABASE)

def init_db():
    """앱 시작 시 ClickHouse 테이블 자동 생성"""
    try:
        client = get_ch_client()
        # image_data 테이블 생성 (메타데이터 저장용)
        client.execute('''
            CREATE TABLE IF NOT EXISTS image_data (
                id UUID,
                category String,
                title String,
                filename String,
                file_url String,
                uploaded_at DateTime
            ) ENGINE = MergeTree()
            ORDER BY uploaded_at
        ''')
        print("ClickHouse 데이터베이스 초기화 완료.")
    except Exception as e:
        print(f"ClickHouse 연결 오류 (DB가 켜져있는지 확인하세요): {e}")

# 앱 시작 전 DB 초기화 실행
init_db()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- HTML 템플릿 (단일 파일 구성을 위해 코드 내에 삽입) ---

BASE_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>스마트 팩토리 이미지 포털</title>
    <!-- Bootstrap CSS -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .img-card { height: 250px; object-fit: cover; width: 100%; border-bottom: 1px solid #ddd; }
        .card { transition: transform 0.2s; }
        .card:hover { transform: scale(1.02); box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
    </style>
</head>
<body class="bg-light">
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark mb-4">
        <div class="container">
            <a class="navbar-brand" href="{{ url_for('index') }}">🏭 스마트 팩토리 포털</a>
            <div class="d-flex">
                {% if session.get('is_admin') %}
                    <span class="navbar-text me-3 text-warning">관리자 모드</span>
                    <a href="{{ url_for('admin') }}" class="btn btn-outline-light btn-sm me-2">관리자 페이지</a>
                    <a href="{{ url_for('logout') }}" class="btn btn-danger btn-sm">로그아웃</a>
                {% else %}
                    <a href="{{ url_for('login') }}" class="btn btn-outline-light btn-sm">관리자 로그인</a>
                {% endif %}
            </div>
        </div>
    </nav>
    <div class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
                        {{ message }}
                        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        
        {% block content %}{% endblock %}
    </div>
    
    <!-- Bootstrap JS -->
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
'''

INDEX_TEMPLATE = '''
{% extends "base" %}
{% block content %}
<h2 class="mb-4">데이터 조회</h2>

<ul class="nav nav-tabs" id="myTab" role="tablist">
  <li class="nav-item" role="presentation">
    <button class="nav-link active" id="blueprint-tab" data-bs-toggle="tab" data-bs-target="#blueprint" type="button" role="tab">📐 도면</button>
  </li>
  <li class="nav-item" role="presentation">
    <button class="nav-link" id="factory-tab" data-bs-toggle="tab" data-bs-target="#factory" type="button" role="tab">🏭 공장 전경</button>
  </li>
  <li class="nav-item" role="presentation">
    <button class="nav-link" id="vision-tab" data-bs-toggle="tab" data-bs-target="#vision" type="button" role="tab">👁️ Vision 데이터</button>
  </li>
</ul>

<div class="tab-content border border-top-0 bg-white p-4 rounded-bottom" id="myTabContent">
  <!-- 도면 탭 -->
  <div class="tab-pane fade show active" id="blueprint" role="tabpanel">
    <div class="row row-cols-1 row-cols-md-3 g-4">
        {% for img in images if img.category == 'blueprint' %}
            <div class="col"><div class="card h-100">
                <img src="{{ img.file_url }}" class="card-img-top img-card" alt="{{ img.title }}">
                <div class="card-body">
                    <h5 class="card-title">{{ img.title }}</h5>
                    <p class="card-text"><small class="text-muted">업로드: {{ img.uploaded_at }}</small></p>
                </div>
            </div></div>
        {% else %}
            <p class="text-muted">등록된 도면이 없습니다.</p>
        {% endfor %}
    </div>
  </div>
  
  <!-- 공장 전경 탭 -->
  <div class="tab-pane fade" id="factory" role="tabpanel">
    <div class="row row-cols-1 row-cols-md-3 g-4">
        {% for img in images if img.category == 'factory' %}
            <div class="col"><div class="card h-100">
                <img src="{{ img.file_url }}" class="card-img-top img-card" alt="{{ img.title }}">
                <div class="card-body">
                    <h5 class="card-title">{{ img.title }}</h5>
                    <p class="card-text"><small class="text-muted">업로드: {{ img.uploaded_at }}</small></p>
                </div>
            </div></div>
        {% else %}
            <p class="text-muted">등록된 공장 전경이 없습니다.</p>
        {% endfor %}
    </div>
  </div>
  
  <!-- Vision 데이터 탭 -->
  <div class="tab-pane fade" id="vision" role="tabpanel">
    <div class="row row-cols-1 row-cols-md-3 g-4">
        {% for img in images if img.category == 'vision' %}
            <div class="col"><div class="card h-100">
                <img src="{{ img.file_url }}" class="card-img-top img-card" alt="{{ img.title }}">
                <div class="card-body">
                    <h5 class="card-title">{{ img.title }}</h5>
                    <p class="card-text"><small class="text-muted">업로드: {{ img.uploaded_at }}</small></p>
                </div>
            </div></div>
        {% else %}
            <p class="text-muted">등록된 Vision 데이터가 없습니다.</p>
        {% endfor %}
    </div>
  </div>
</div>
{% endblock %}
'''

ADMIN_TEMPLATE = '''
{% extends "base" %}
{% block content %}
<div class="row">
    <!-- 업로드 폼 -->
    <div class="col-md-4 mb-4">
        <div class="card">
            <div class="card-header bg-primary text-white">이미지 신규 업로드</div>
            <div class="card-body">
                <form action="{{ url_for('upload') }}" method="POST" enctype="multipart/form-data">
                    <div class="mb-3">
                        <label class="form-label">카테고리</label>
                        <select name="category" class="form-select" required>
                            <option value="blueprint">도면</option>
                            <option value="factory">공장 전경</option>
                            <option value="vision">Vision 데이터</option>
                        </select>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">이미지 제목/설명</label>
                        <input type="text" name="title" class="form-control" placeholder="예: 1공장 1층 도면" required>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">파일 선택</label>
                        <input type="file" name="file" class="form-control" accept="image/*" required>
                    </div>
                    <button type="submit" class="btn btn-primary w-100">업로드 실행</button>
                </form>
            </div>
        </div>
    </div>
    
    <!-- 파일 관리 리스트 -->
    <div class="col-md-8">
        <div class="card">
            <div class="card-header bg-secondary text-white">등록된 이미지 관리</div>
            <div class="card-body p-0">
                <table class="table table-hover mb-0">
                    <thead class="table-light">
                        <tr>
                            <th>미리보기</th>
                            <th>분류</th>
                            <th>제목</th>
                            <th>파일명</th>
                            <th>업로드 일시</th>
                            <th>관리</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for img in images %}
                        <tr>
                            <td><img src="{{ img.file_url }}" height="40" class="rounded"></td>
                            <td>
                                {% if img.category == 'blueprint' %}도면
                                {% elif img.category == 'factory' %}공장 전경
                                {% elif img.category == 'vision' %}Vision
                                {% endif %}
                            </td>
                            <td>{{ img.title }}</td>
                            <td><small>{{ img.filename }}</small></td>
                            <td><small>{{ img.uploaded_at }}</small></td>
                            <td>
                                <form action="{{ url_for('delete', img_id=img.id) }}" method="POST" style="display:inline;" onsubmit="return confirm('정말 삭제하시겠습니까?');">
                                    <input type="hidden" name="filename" value="{{ img.filename }}">
                                    <button type="submit" class="btn btn-danger btn-sm">삭제</button>
                                </form>
                            </td>
                        </tr>
                        {% else %}
                        <tr><td colspan="6" class="text-center py-4 text-muted">등록된 이미지가 없습니다.</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>
{% endblock %}
'''

LOGIN_TEMPLATE = '''
{% extends "base" %}
{% block content %}
<div class="row justify-content-center">
    <div class="col-md-4">
        <div class="card mt-5">
            <div class="card-header text-center"><h5>관리자 로그인</h5></div>
            <div class="card-body">
                <form action="{{ url_for('login') }}" method="POST">
                    <div class="mb-3">
                        <label>아이디</label>
                        <input type="text" name="username" class="form-control" required>
                    </div>
                    <div class="mb-3">
                        <label>비밀번호</label>
                        <input type="password" name="password" class="form-control" required>
                    </div>
                    <button type="submit" class="btn btn-dark w-100">로그인</button>
                    <p class="text-muted text-center mt-3 mb-0"><small>테스트 계정: admin / 1234</small></p>
                </form>
            </div>
        </div>
    </div>
</div>
{% endblock %}
'''

# Jinja2 상속 시스템 우회 (단일 파일에서 처리하기 위함)
def render_page(template_content, **kwargs):
    merged_template = template_content.replace('{% extends "base" %}', BASE_TEMPLATE)
    # block content 내용 추출/병합 로직 간소화
    content_block = merged_template.split('{% block content %}')[1].split('{% endblock %}')[0]
    final_html = BASE_TEMPLATE.replace('{% block content %}{% endblock %}', content_block)
    return render_template_string(final_html, **kwargs)


# --- 라우팅 (URL 처리) ---

@app.route('/')
def index():
    """메인 화면: 탭별 이미지 조회"""
    try:
        client = get_ch_client()
        # ClickHouse에서 이미지 메타데이터 조회
        result = client.execute('SELECT id, category, title, filename, file_url, uploaded_at FROM image_data ORDER BY uploaded_at DESC')
        images = [
            {'id': row[0], 'category': row[1], 'title': row[2], 'filename': row[3], 'file_url': row[4], 'uploaded_at': row[5]}
            for row in result
        ]
    except Exception as e:
        images = []
        flash(f'데이터베이스 연결 오류: {e}', 'danger')
        
    return render_page(INDEX_TEMPLATE, images=images)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """관리자 로그인"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        # 하드코딩된 테스트용 관리자 계정 (실무에서는 DB 연동 필요)
        if username == 'admin' and password == '1234':
            session['is_admin'] = True
            flash('관리자로 로그인되었습니다.', 'success')
            return redirect(url_for('admin'))
        else:
            flash('아이디 또는 비밀번호가 틀렸습니다.', 'danger')
    return render_page(LOGIN_TEMPLATE)

@app.route('/logout')
def logout():
    session.pop('is_admin', None)
    flash('로그아웃 되었습니다.', 'info')
    return redirect(url_for('index'))

@app.route('/admin')
def admin():
    """관리자 대시보드"""
    if not session.get('is_admin'):
        flash('관리자 권한이 필요합니다.', 'warning')
        return redirect(url_for('login'))
        
    try:
        client = get_ch_client()
        result = client.execute('SELECT id, category, title, filename, file_url, uploaded_at FROM image_data ORDER BY uploaded_at DESC')
        images = [
            {'id': row[0], 'category': row[1], 'title': row[2], 'filename': row[3], 'file_url': row[4], 'uploaded_at': row[5]}
            for row in result
        ]
    except Exception as e:
        images = []
    return render_page(ADMIN_TEMPLATE, images=images)

@app.route('/upload', methods=['POST'])
def upload():
    """파일 업로드 및 ClickHouse 메타데이터 적재"""
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    if 'file' not in request.files:
        flash('파일이 없습니다.', 'danger')
        return redirect(url_for('admin'))
        
    file = request.files['file']
    category = request.form.get('category')
    title = request.form.get('title')
    
    if file.filename == '':
        flash('선택된 파일이 없습니다.', 'danger')
        return redirect(url_for('admin'))
        
    if file and allowed_file(file.filename):
        # 1. 파일 이름 충돌 방지를 위해 UUID 추가
        ext = file.filename.rsplit('.', 1)[1].lower()
        unique_filename = f"{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        
        # 2. 로컬 파일 시스템에 저장 (S3라면 이 부분에서 S3 업로드 로직으로 변경)
        file.save(filepath)
        
        # 3. 화면에 표시할 URL (Flask static 폴더 경로)
        file_url = url_for('static', filename=f'uploads/{unique_filename}')
        
        # 4. ClickHouse에 메타데이터 저장
        try:
            client = get_ch_client()
            img_id = uuid.uuid4()
            now = datetime.now()
            
            client.execute(
                'INSERT INTO image_data (id, category, title, filename, file_url, uploaded_at) VALUES',
                [(img_id, category, title, unique_filename, file_url, now)]
            )
            flash('성공적으로 업로드되었습니다.', 'success')
        except Exception as e:
            flash(f'DB 저장 실패: {e}', 'danger')
            # 롤백: DB 저장 실패 시 로컬 파일 삭제
            os.remove(filepath)
            
    else:
        flash('지원하지 않는 파일 형식입니다.', 'danger')
        
    return redirect(url_for('admin'))

@app.route('/delete/<uuid:img_id>', methods=['POST'])
def delete(img_id):
    """파일 및 ClickHouse 메타데이터 삭제"""
    if not session.get('is_admin'):
        return redirect(url_for('login'))
        
    filename = request.form.get('filename')
    
    try:
        # 1. ClickHouse에서 데이터 삭제 (ClickHouse의 Mutation 방식)
        client = get_ch_client()
        client.execute(f"ALTER TABLE image_data DELETE WHERE id = '{img_id}'")
        
        # 2. 로컬 스토리지에서 파일 삭제
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(filepath):
            os.remove(filepath)
            
        flash('성공적으로 삭제되었습니다. (ClickHouse 비동기 삭제 방식에 의해 목록 갱신에 수 초가 소요될 수 있습니다.)', 'success')
    except Exception as e:
        flash(f'삭제 중 오류가 발생했습니다: {e}', 'danger')
        
    return redirect(url_for('admin'))

if __name__ == '__main__':
    # 0.0.0.0으로 실행하여 외부망에서도 접근 가능하도록 설정
    app.run(host='0.0.0.0', port=5000, debug=True)