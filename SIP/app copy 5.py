import os
import uuid
from datetime import datetime
from flask import Flask, request, render_template, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
from jinja2 import DictLoader  # 안전한 템플릿 상속 처리를 위한 라이브러리 추가
import clickhouse_connect

app = Flask(__name__)
# 세션 및 플래시 메시지를 위한 시크릿 키
app.secret_key = 'smart_factory_secret_key_123!'

# 로컬 스토리지(이미지 저장소) 설정
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 허용할 이미지 확장자
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'}

# --- ClickHouse 설정 (요청하신 접속 정보 반영) ---
CH_HOST = 'localhost'
CH_PORT = 8123
CH_USER = 'default'
CH_PASSWORD = 'clickhouse'

def get_ch_client():
    """ClickHouse 클라이언트 객체 생성 (실제 사용 시점에만 호출하도록 타임아웃 단축)"""
    return clickhouse_connect.get_client(
        host=CH_HOST, 
        port=CH_PORT, 
        username=CH_USER, 
        password=CH_PASSWORD,
        connect_timeout=3,       # 초기 대기 시간 단축하여 멈춤 현상 방지
        send_receive_timeout=10
    )

def init_db():
    """앱 시작 시 ClickHouse 테이블 자동 생성 및 기존 테이블 안전 마이그레이션"""
    try:
        print("🔗 ClickHouse 서버 연결 확인 중...")
        client = get_ch_client()
        # 1. 테이블 신규 생성 (설명 및 채번용 컬럼 추가)
        client.command('''
            CREATE TABLE IF NOT EXISTS image_data (
                id UUID,
                category String,
                title String,
                description String,
                serial_code String,
                filename String,
                file_url String,
                uploaded_at DateTime
            ) ENGINE = MergeTree()
            ORDER BY uploaded_at
        ''')
        
        # 2. 기존 사용 환경에서의 테이블 마이그레이션 (안전 장치 컬럼 추가)
        client.command("ALTER TABLE image_data ADD COLUMN IF NOT EXISTS description String")
        client.command("ALTER TABLE image_data ADD COLUMN IF NOT EXISTS serial_code String")
        
        print("✅ ClickHouse 데이터베이스 연결 및 테이블 확인 완료.")
        return True
    except Exception as e:
        print(f"⚠️ ClickHouse 연결 보류 (서버가 꺼져있거나 구동 중일 수 있음): {e}")
        return False

# --- HTML 템플릿 정의 (단일 파일 연동용) ---

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
        .img-card { height: 220px; object-fit: cover; width: 100%; border-bottom: 1px solid #ddd; }
        .card { transition: transform 0.2s; position: relative; }
        .card:hover { transform: scale(1.02); box-shadow: 0 4px 12px rgba(0,0,0,0.15); }
        .serial-badge { position: absolute; top: 10px; left: 10px; z-index: 10; font-size: 0.75rem; }
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
{% extends "base.html" %}
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
            <div class="col"><div class="card h-100 shadow-sm">
                <span class="badge bg-dark opacity-80 serial-badge shadow-sm">{{ img.serial_code }}</span>
                <img src="{{ img.file_url }}" class="card-img-top img-card" alt="{{ img.title }}">
                <div class="card-body">
                    <h5 class="card-title text-truncate" title="{{ img.title }}">{{ img.title }}</h5>
                    <p class="card-text text-muted small text-truncate-2" style="min-height: 40px;">
                        {{ img.description if img.description else '상세 설명이 등록되지 않았습니다.' }}
                    </p>
                    <p class="card-text"><small class="text-muted">업로드: {{ img.uploaded_at }}</small></p>
                </div>
            </div></div>
        {% else %}
            <p class="text-muted py-3">등록된 도면이 없습니다.</p>
        {% endfor %}
    </div>
  </div>
  
  <!-- 공장 전경 탭 -->
  <div class="tab-pane fade" id="factory" role="tabpanel">
    <div class="row row-cols-1 row-cols-md-3 g-4">
        {% for img in images if img.category == 'factory' %}
            <div class="col"><div class="card h-100 shadow-sm">
                <span class="badge bg-dark opacity-80 serial-badge shadow-sm">{{ img.serial_code }}</span>
                <img src="{{ img.file_url }}" class="card-img-top img-card" alt="{{ img.title }}">
                <div class="card-body">
                    <h5 class="card-title text-truncate" title="{{ img.title }}">{{ img.title }}</h5>
                    <p class="card-text text-muted small text-truncate-2" style="min-height: 40px;">
                        {{ img.description if img.description else '상세 설명이 등록되지 않았습니다.' }}
                    </p>
                    <p class="card-text"><small class="text-muted">업로드: {{ img.uploaded_at }}</small></p>
                </div>
            </div></div>
        {% else %}
            <p class="text-muted py-3">등록된 공장 전경이 없습니다.</p>
        {% endfor %}
    </div>
  </div>
  
  <!-- Vision 데이터 탭 -->
  <div class="tab-pane fade" id="vision" role="tabpanel">
    <div class="row row-cols-1 row-cols-md-3 g-4">
        {% for img in images if img.category == 'vision' %}
            <div class="col"><div class="card h-100 shadow-sm">
                <span class="badge bg-dark opacity-80 serial-badge shadow-sm">{{ img.serial_code }}</span>
                <img src="{{ img.file_url }}" class="card-img-top img-card" alt="{{ img.title }}">
                <div class="card-body">
                    <h5 class="card-title text-truncate" title="{{ img.title }}">{{ img.title }}</h5>
                    <p class="card-text text-muted small text-truncate-2" style="min-height: 40px;">
                        {{ img.description if img.description else '상세 설명이 등록되지 않았습니다.' }}
                    </p>
                    <p class="card-text"><small class="text-muted">업로드: {{ img.uploaded_at }}</small></p>
                </div>
            </div></div>
        {% else %}
            <p class="text-muted py-3">등록된 Vision 데이터가 없습니다.</p>
        {% endfor %}
    </div>
  </div>
</div>
{% endblock %}
'''

ADMIN_TEMPLATE = '''
{% extends "base.html" %}
{% block content %}
<div class="row">
    <!-- 업로드 폼 -->
    <div class="col-md-5 mb-4">
        <div class="card shadow-sm">
            <div class="card-header bg-primary text-white">💾 다중 이미지 일괄 업로드</div>
            <div class="card-body">
                <form action="{{ url_for('upload') }}" method="POST" enctype="multipart/form-data" id="uploadForm">
                    <div class="mb-3">
                        <label class="form-label fw-bold">카테고리</label>
                        <select name="category" class="form-select" required>
                            <option value="blueprint">도면</option>
                            <option value="factory">공장 전경</option>
                            <option value="vision">Vision 데이터</option>
                        </select>
                    </div>
                    
                    <div class="mb-3">
                        <label class="form-label fw-bold">파일 선택 (다중 선택 가능)</label>
                        <input type="file" name="files" id="fileInput" class="form-control" accept="image/*" multiple required>
                        <div class="form-text text-secondary">여러 장의 이미지를 동시에 선택하여 개별 메타데이터를 등록할 수 있습니다.</div>
                    </div>
                    
                    <!-- 선택된 파일들의 이름과 설명 입력이 동적으로 렌더링되는 구역 -->
                    <div id="dynamicFormFields" class="mb-3"></div>
                    
                    <button type="submit" class="btn btn-primary w-100" id="submitBtn" style="display: none;">🏭 일괄 업로드 및 자동 채번 실행</button>
                </form>
            </div>
        </div>
    </div>
    
    <!-- 파일 관리 리스트 -->
    <div class="col-md-7">
        <div class="card shadow-sm">
            <div class="card-header bg-secondary text-white">등록된 이미지 데이터 관리</div>
            <div class="card-body p-0">
                <div class="table-responsive">
                    <table class="table table-hover align-middle mb-0">
                        <thead class="table-light">
                            <tr>
                                <th>미리보기</th>
                                <th>관리 번호</th>
                                <th>분류</th>
                                <th>제목 및 설명</th>
                                <th>관리</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for img in images %}
                            <tr>
                                <td><img src="{{ img.file_url }}" height="50" width="70" class="rounded object-fit-cover shadow-sm"></td>
                                <td><span class="badge bg-dark">{{ img.serial_code if img.serial_code else 'N/A' }}</span></td>
                                <td>
                                    {% if img.category == 'blueprint' %}<span class="badge bg-info">도면</span>
                                    {% elif img.category == 'factory' %}<span class="badge bg-warning text-dark">공장 전경</span>
                                    {% elif img.category == 'vision' %}<span class="badge bg-success">Vision</span>
                                    {% endif %}
                                </td>
                                <td>
                                    <div class="fw-bold text-truncate" style="max-width: 180px;">{{ img.title }}</div>
                                    <div class="text-muted small text-truncate" style="max-width: 180px;" title="{{ img.description }}">{{ img.description if img.description else '설명 없음' }}</div>
                                </td>
                                <td>
                                    <form action="{{ url_for('delete', img_id=img.id) }}" method="POST" style="display:inline;" onsubmit="return confirm('정말 삭제하시겠습니까?');">
                                        <input type="hidden" name="filename" value="{{ img.filename }}">
                                        <button type="submit" class="btn btn-danger btn-sm">삭제</button>
                                    </form>
                                </td>
                            </tr>
                            {% else %}
                            <tr><td colspan="5" class="text-center py-5 text-muted">등록된 이미지가 없습니다.</td></tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
</div>

<script>
    // 다중 선택된 각 파일에 대해 동적으로 이름 및 설명 수정 인풋을 생성하는 JS
    document.getElementById('fileInput').addEventListener('change', function(e) {
        const container = document.getElementById('dynamicFormFields');
        const submitBtn = document.getElementById('submitBtn');
        container.innerHTML = ''; // 초기화
        
        const files = e.target.files;
        if (files.length > 0) {
            submitBtn.style.display = 'block';
            
            // 헤더 정보 추가
            const header = document.createElement('h6');
            header.className = 'mt-3 mb-2 text-primary fw-bold';
            header.innerText = '선택한 이미지 개별 설명 작성';
            container.appendChild(header);

            Array.from(files).forEach((file, index) => {
                // 확장자를 제거한 순수 파일명을 기본 제목값으로 사용
                const defaultTitle = file.name.substring(0, file.name.lastIndexOf('.')) || file.name;
                
                const card = document.createElement('div');
                card.className = 'card mb-3 bg-light border-light shadow-xs';
                card.innerHTML = `
                    <div class="card-header py-1 bg-secondary text-white d-flex justify-content-between align-items-center" style="font-size: 0.85rem;">
                        <span>파일 [#${index + 1}]</span>
                        <span class="text-truncate" style="max-width: 180px;">${file.name}</span>
                    </div>
                    <div class="card-body p-2">
                        <div class="mb-2">
                            <label class="form-label small mb-1 fw-bold">이미지 제목</label>
                            <input type="text" name="titles" class="form-control form-control-sm" value="${defaultTitle}" required>
                        </div>
                        <div>
                            <label class="form-label small mb-1 fw-bold">설명 (메모)</label>
                            <textarea name="descriptions" class="form-control form-control-sm" rows="2" placeholder="작업 내역, 장비 명칭 등 기입"></textarea>
                        </div>
                    </div>
                `;
                container.appendChild(card);
            });
        } else {
            submitBtn.style.display = 'none';
        }
    });
</script>
{% endblock %}
'''

LOGIN_TEMPLATE = '''
{% extends "base.html" %}
{% block content %}
<div class="row justify-content-center">
    <div class="col-md-4">
        <div class="card mt-5 shadow-sm">
            <div class="card-header text-center bg-dark text-white"><h5>관리자 로그인</h5></div>
            <div class="card-body">
                <form action="{{ url_for('login') }}" method="POST">
                    <div class="mb-3">
                        <label class="form-label">아이디</label>
                        <input type="text" name="username" class="form-control" placeholder="아이디를 입력하세요" required>
                    </div>
                    <div class="mb-3">
                        <label class="form-label">비밀번호</label>
                        <input type="password" name="password" class="form-control" placeholder="비밀번호를 입력하세요" required>
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

# --- Flask 템플릿 환경 설정 (DictLoader 적용) ---
app.jinja_loader = DictLoader({
    'base.html': BASE_TEMPLATE,
    'index.html': INDEX_TEMPLATE,
    'admin.html': ADMIN_TEMPLATE,
    'login.html': LOGIN_TEMPLATE
})


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# --- 라우팅 (URL 처리) ---

@app.route('/')
def index():
    """메인 화면: 탭별 이미지 조회"""
    try:
        init_db()
        client = get_ch_client()
        result = client.query('SELECT id, category, title, description, serial_code, filename, file_url, uploaded_at FROM image_data ORDER BY uploaded_at DESC')
        images = [
            {
                'id': row[0], 'category': row[1], 'title': row[2], 'description': row[3],
                'serial_code': row[4], 'filename': row[5], 'file_url': row[6], 'uploaded_at': row[7]
            }
            for row in result.result_rows
        ]
    except Exception as e:
        images = []
        flash(f'데이터베이스 연결 오류: {e}', 'danger')
        
    return render_template('index.html', images=images)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """관리자 로그인"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == 'admin' and password == '1234':
            session['is_admin'] = True
            flash('관리자로 로그인되었습니다.', 'success')
            return redirect(url_for('admin'))
        else:
            flash('아이디 또는 비밀번호가 틀렸습니다.', 'danger')
    return render_template('login.html')

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
        init_db()
        client = get_ch_client()
        result = client.query('SELECT id, category, title, description, serial_code, filename, file_url, uploaded_at FROM image_data ORDER BY uploaded_at DESC')
        images = [
            {
                'id': row[0], 'category': row[1], 'title': row[2], 'description': row[3],
                'serial_code': row[4], 'filename': row[5], 'file_url': row[6], 'uploaded_at': row[7]
            }
            for row in result.result_rows
        ]
    except Exception as e:
        images = []
    return render_template('admin.html', images=images)

@app.route('/upload', methods=['POST'])
def upload():
    """다중 파일 업로드 및 ClickHouse 메타데이터(채번/이름/설명) 일괄 적재"""
    if not session.get('is_admin'):
        return redirect(url_for('login'))

    if 'files' not in request.files:
        flash('파일 필드가 없습니다.', 'danger')
        return redirect(url_for('admin'))
        
    files = request.files.getlist('files')
    titles = request.form.getlist('titles')
    descriptions = request.form.getlist('descriptions')
    category = request.form.get('category')
    
    if not files or files[0].filename == '':
        flash('선택된 파일이 없습니다.', 'danger')
        return redirect(url_for('admin'))
        
    try:
        init_db()
        client = get_ch_client()
        
        # 오늘 등록된 기존 이미지 수 조회 (정확하고 투명한 채번을 위함)
        today_str = datetime.now().strftime('%Y%m%d')
        query_res = client.query("SELECT count() FROM image_data WHERE toDate(uploaded_at) = today()")
        today_count = query_res.result_rows[0][0]
        
        upload_records = []
        saved_filepaths = []
        
        for i, file in enumerate(files):
            if file and allowed_file(file.filename):
                # 1. 일련 번호 채번 생성 (예: FAC-20260610-0001)
                seq = today_count + 1 + i
                serial_code = f"FAC-{today_str}-{seq:04d}"
                
                # 2. 고유 파일명 생성 (채번 코드 + 고유 해시 혼합하여 유일성 보장)
                ext = file.filename.rsplit('.', 1)[1].lower()
                unique_filename = f"{category}_{today_str}_{seq:04d}_{uuid.uuid4().hex[:8]}.{ext}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                
                # 3. 로컬 저장소 저장
                file.save(filepath)
                saved_filepaths.append(filepath)
                
                # 4. 입력 폼의 제목과 설명 가져오기
                title = titles[i] if i < len(titles) else file.filename
                description = descriptions[i] if i < len(descriptions) else ""
                file_url = url_for('static', filename=f'uploads/{unique_filename}')
                img_id = uuid.uuid4()
                now = datetime.now()
                
                upload_records.append([img_id, category, title, description, serial_code, unique_filename, file_url, now])
        
        # ClickHouse 대용량 배치(Batch) Insert 처리로 성능 보존
        if upload_records:
            client.insert(
                'image_data', 
                upload_records,
                column_names=['id', 'category', 'title', 'description', 'serial_code', 'filename', 'file_url', 'uploaded_at']
            )
            flash(f'성공적으로 {len(upload_records)}개의 파일이 자동 채번되어 일괄 등록되었습니다.', 'success')
        else:
            flash('업로드 가능한 포맷의 파일이 선택되지 않았습니다.', 'danger')
            
    except Exception as e:
        flash(f'일괄 업로드 실패: {e}', 'danger')
        # DB 트랜잭션 실패 대비 로컬 파일 흔적 롤백
        for path in saved_filepaths:
            if os.path.exists(path):
                os.remove(path)
                
    return redirect(url_for('admin'))

@app.route('/delete/<uuid:img_id>', methods=['POST'])
def delete(img_id):
    """파일 및 ClickHouse 메타데이터 삭제"""
    if not session.get('is_admin'):
        return redirect(url_for('login'))
        
    filename = request.form.get('filename')
    
    try:
        init_db()
        client = get_ch_client()
        client.command(f"ALTER TABLE image_data DELETE WHERE id = '{img_id}'")
        
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(filepath):
            os.remove(filepath)
            
        flash('성공적으로 삭제되었습니다. (ClickHouse 비동기 삭제 방식에 의해 목록 갱신에 수 초가 소요될 수 있습니다.)', 'success')
    except Exception as e:
        flash(f'삭제 중 오류가 발생했습니다: {e}', 'danger')
        
    return redirect(url_for('admin'))

if __name__ == '__main__':
    print("🚀 Flask 웹 서버 초기화 중...")
    app.run(host='0.0.0.0', port=5000, debug=True)