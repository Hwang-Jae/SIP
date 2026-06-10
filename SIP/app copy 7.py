import os
import uuid
import json
import requests
from datetime import datetime
from flask import Flask, request, render_template, redirect, url_for, session, flash, jsonify
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

# --- Ollama 설정 ---
OLLAMA_API_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "gpt-oss:20b"  # 사용 중인 로컬 모델명으로 변경 가능 (예: gemma2, qwen2.5, mistral 등)

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

def get_filtered_images(args):
    """검색 필터 및 정렬 옵션을 적용하여 ClickHouse에서 이미지 데이터를 조회합니다."""
    init_db()
    client = get_ch_client()
    
    # 기본 쿼리 정의
    query = 'SELECT id, category, title, description, serial_code, filename, file_url, uploaded_at FROM image_data WHERE 1=1'
    params = {}
    
    # 1. 통합 키워드 검색 (제목, 설명, 관리번호 LIKE 검색)
    q = args.get('q', '').strip()
    if q:
        query += ' AND (title ILIKE %(q)s OR description ILIKE %(q)s OR serial_code ILIKE %(q)s)'
        params['q'] = f'%{q}%'
        
    # 2. 기간 검색 (시작일 ~ 종료일)
    start_date = args.get('start_date', '').strip()
    if start_date:
        query += ' AND toDate(uploaded_at) >= %(start_date)s'
        params['start_date'] = start_date
        
    end_date = args.get('end_date', '').strip()
    if end_date:
        query += ' AND toDate(uploaded_at) <= %(end_date)s'
        params['end_date'] = end_date
        
    # 3. 정렬 옵션 매핑
    sort_by = args.get('sort_by', 'uploaded_at_desc')
    sort_mapping = {
        'uploaded_at_desc': 'ORDER BY uploaded_at DESC',
        'uploaded_at_asc': 'ORDER BY uploaded_at ASC',
        'title_asc': 'ORDER BY title ASC',
        'title_desc': 'ORDER BY title DESC',
        'serial_code_asc': 'ORDER BY serial_code ASC',
        'serial_code_desc': 'ORDER BY serial_code DESC'
    }
    order_clause = sort_mapping.get(sort_by, 'ORDER BY uploaded_at DESC')
    query += f' {order_clause}'
    
    # 쿼리 실행 및 파싱
    result = client.query(query, parameters=params)
    return [
        {
            'id': row[0], 'category': row[1], 'title': row[2], 'description': row[3],
            'serial_code': row[4], 'filename': row[5], 'file_url': row[6], 'uploaded_at': row[7]
        }
        for row in result.result_rows
    ]

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
        
        /* 챗봇 인터페이스 스타일 */
        .chat-container { height: 500px; display: flex; flex-direction: column; }
        .chat-messages { flex: 1; overflow-y: auto; padding: 15px; border: 1px solid #dee2e6; border-radius: 8px; background-color: #f8f9fa; }
        .message { margin-bottom: 15px; display: flex; flex-direction: column; }
        .message.user { align-items: flex-end; }
        .message.bot { align-items: flex-start; }
        .message-content { max-width: 75%; padding: 10px 15px; border-radius: 15px; font-size: 0.95rem; }
        .message.user .message-content { background-color: #0d6efd; color: white; border-bottom-right-radius: 2px; }
        .message.bot .message-content { background-color: white; border: 1px solid #dee2e6; border-bottom-left-radius: 2px; }
        .chat-image-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; width: 100%; margin-top: 10px; }
        .chat-img-card { width: 100%; height: 100px; object-fit: cover; border-radius: 6px; cursor: pointer; border: 1px solid #ddd; transition: transform 0.2s; }
        .chat-img-card:hover { transform: scale(1.05); }
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

SEARCH_FORM_TEMPLATE = '''
<div class="card shadow-sm mb-4">
    <div class="card-body bg-white rounded">
        <form method="GET" action="{{ action_url }}" class="row g-3 align-items-end">
            <!-- 검색 키워드 -->
            <div class="col-md-4">
                <label class="form-label small fw-bold text-secondary">🔍 검색어 통합 검색</label>
                <input type="text" name="q" class="form-control" placeholder="제목, 관리번호, 설명 검색..." value="{{ params.q or '' }}">
            </div>
            
            <!-- 기간 필터 (시작일) -->
            <div class="col-md-2.5 col-sm-6 col-6">
                <label class="form-label small fw-bold text-secondary">📅 조회 시작일</label>
                <input type="date" name="start_date" class="form-control" value="{{ params.start_date or '' }}">
            </div>
            
            <!-- 기간 필터 (종료일) -->
            <div class="col-md-2.5 col-sm-6 col-6">
                <label class="form-label small fw-bold text-secondary">📅 조회 종료일</label>
                <input type="date" name="end_date" class="form-control" value="{{ params.end_date or '' }}">
            </div>
            
            <!-- 정렬 순서 -->
            <div class="col-md-2">
                <label class="form-label small fw-bold text-secondary">⇅ 정렬 기준</label>
                <select name="sort_by" class="form-select">
                    <option value="uploaded_at_desc" {% if params.sort_by == 'uploaded_at_desc' %}selected{% endif %}>최신 등록순</option>
                    <option value="uploaded_at_asc" {% if params.sort_by == 'uploaded_at_asc' %}selected{% endif %}>오래된 등록순</option>
                    <option value="title_asc" {% if params.sort_by == 'title_asc' %}selected{% endif %}>제목 (가나다순)</option>
                    <option value="title_desc" {% if params.sort_by == 'title_desc' %}selected{% endif %}>제목 (역순)</option>
                    <option value="serial_code_asc" {% if params.sort_by == 'serial_code_asc' %}selected{% endif %}>관리번호 (오름차순)</option>
                    <option value="serial_code_desc" {% if params.sort_by == 'serial_code_desc' %}selected{% endif %}>관리번호 (내림차순)</option>
                </select>
            </div>
            
            <!-- 제어 버튼들 -->
            <div class="col-md-1 text-end d-flex gap-2">
                <button type="submit" class="btn btn-dark w-100">조회</button>
                <a href="{{ action_url }}" class="btn btn-outline-secondary" title="검색 필터 초기화">🔄</a>
            </div>
        </form>
    </div>
</div>
'''

INDEX_TEMPLATE = '''
{% extends "base.html" %}
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-3">
    <h2>스마트 이미지 데이터 조회 및 AI 분석</h2>
</div>

<!-- 상단 탭: 데이터 브라우저 vs AI 지능형 챗봇 -->
<ul class="nav nav-pills mb-4 shadow-sm bg-white p-1 rounded" id="mainServiceTab" role="tablist">
  <li class="nav-item" role="presentation" style="flex: 1;">
    <button class="nav-link active w-100 fw-bold py-2.5" id="explorer-tab" data-bs-toggle="pill" data-bs-target="#explorerContent" type="button" role="tab">📁 데이터 탐색기</button>
  </li>
  <li class="nav-item" role="presentation" style="flex: 1;">
    <button class="nav-link w-100 fw-bold py-2.5" id="aicorner-tab" data-bs-toggle="pill" data-bs-target="#aicornerContent" type="button" role="tab">🤖 AI 증강 검색 챗봇</button>
  </li>
</ul>

<div class="tab-content" id="mainServiceTabContent">
  <!-- 탭 1: 기존 데이터 브라우저 -->
  <div class="tab-pane fade show active" id="explorerContent" role="tabpanel">
    <!-- 검색 폼 임포트 -->
    {% include "search_form.html" %}

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

    <div class="tab-content border border-top-0 bg-white p-4 rounded-bottom mb-4" id="myTabContent">
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
                <p class="text-muted py-3">해당 조건에 일치하는 도면 이미지가 없습니다.</p>
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
                <p class="text-muted py-3">해당 조건에 일치하는 공장 전경 이미지가 없습니다.</p>
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
                <p class="text-muted py-3">해당 조건에 일치하는 Vision 데이터 이미지가 없습니다.</p>
            {% endfor %}
        </div>
      </div>
    </div>
  </div>

  <!-- 탭 2: Ollama 기반 지능형 RAG AI 챗봇 -->
  <div class="tab-pane fade" id="aicornerContent" role="tabpanel">
    <div class="row">
        <!-- 챗봇 메인 -->
        <div class="col-lg-8 mb-4">
            <div class="card shadow-sm chat-container">
                <div class="card-header bg-dark text-white d-flex justify-content-between align-items-center">
                    <span class="fw-bold">🤖 Ollama AI 공장 스마트 어시스턴트</span>
                    <span class="badge bg-success">Ollama {{ model_name }} 연동 중</span>
                </div>
                <div class="chat-messages" id="chatMessages">
                    <div class="message bot">
                        <div class="message-content shadow-xs">
                            안녕하세요! 스마트 팩토리 이미지 AI 비서입니다. 😊 <br>
                            로컬 대형 언어 모델(Ollama)과 ClickHouse를 통해 지능형 시맨틱 이미지 검색을 지원합니다.<br><br>
                            <strong>💡 이런 질문을 해보세요:</strong>
                            <ul>
                                <li>"러시아 공장 전경 이미지 찾아줘"</li>
                                <li>"NG나 불량이 발생한 비전 데이터만 보여줄래?"</li>
                                <li>"도면 중에서 1층 소방 배관 관련 데이터 검색해줘"</li>
                            </ul>
                        </div>
                    </div>
                </div>
                <div class="card-footer bg-white border-top p-3">
                    <form id="chatForm" class="input-group">
                        <input type="text" id="chatInput" class="form-control py-2.5" placeholder="질문을 입력하세요... (예: 불량 발생한 비전 이미지를 보여줘)" required autocomplete="off">
                        <button type="submit" class="btn btn-dark px-4" id="chatSendBtn">전송</button>
                    </form>
                </div>
            </div>
        </div>
        
        <!-- 챗봇 연동 이미지 상세보기 상세 모달식 사이드바 -->
        <div class="col-lg-4 mb-4">
            <div class="card shadow-sm h-100">
                <div class="card-header bg-secondary text-white fw-bold">🔍 선택 이미지 상세 메타데이터</div>
                <div class="card-body text-center d-flex flex-column justify-content-center align-items-center" id="detailPanel">
                    <div class="text-muted py-5" id="detailPlaceholder">
                        <p class="fs-1">🖼️</p>
                        <p>챗봇 결과 이미지 또는 아래 격자 내의 이미지를 선택하시면 상세 메타데이터가 이곳에 표시됩니다.</p>
                    </div>
                    <div id="detailContent" class="w-100 text-start d-none">
                        <img id="detailImg" class="img-fluid rounded border mb-3 shadow-sm" style="max-height: 200px; object-fit: cover; width: 100%;">
                        <h5 class="fw-bold mb-1" id="detailTitle"></h5>
                        <p class="badge bg-dark mb-3" id="detailSerial"></p>
                        <hr class="my-2">
                        <p class="mb-2"><strong>카테고리:</strong> <span id="detailCategory" class="badge"></span></p>
                        <p class="mb-2"><strong>업로드 일시:</strong> <span id="detailDate" class="text-secondary small"></span></p>
                        <p class="mb-0"><strong>상세 설명:</strong></p>
                        <div class="p-2 bg-light rounded text-muted small border mt-1" id="detailDesc" style="min-height: 80px;"></div>
                    </div>
                </div>
            </div>
        </div>
    </div>
  </div>
</div>

<!-- 이미지 클릭 시 상세보기를 위한 공통 JS -->
<script>
    function showImageDetail(url, title, serial, category, desc, date) {
        document.getElementById('detailPlaceholder').classList.add(' d-none');
        document.getElementById('detailPlaceholder').style.display = 'none';
        
        const content = document.getElementById('detailContent');
        content.classList.remove('d-none');
        
        document.getElementById('detailImg').src = url;
        document.getElementById('detailTitle').innerText = title;
        document.getElementById('detailSerial').innerText = serial;
        document.getElementById('detailDate').innerText = date;
        document.getElementById('detailDesc').innerText = desc || '등록된 상세 설명이 없습니다.';
        
        const catBadge = document.getElementById('detailCategory');
        catBadge.className = 'badge';
        if (category === 'blueprint') {
            catBadge.innerText = '도면';
            catBadge.classList.add('bg-info');
        } else if (category === 'factory') {
            catBadge.innerText = '공장 전경';
            catBadge.classList.add('bg-warning', 'text-dark');
        } else {
            catBadge.innerText = 'Vision 데이터';
            catBadge.classList.add('bg-success');
        }
    }

    // 챗봇 연동 및 AJAX 스크립트
    document.getElementById('chatForm').addEventListener('submit', async function(e) {
        e.preventDefault();
        const inputField = document.getElementById('chatInput');
        const userText = inputField.value.strip ? inputField.value.strip() : inputField.value;
        if (!userText) return;

        // 1. 사용자 메시지 추가
        appendMessage('user', userText);
        inputField.value = '';

        // 전송 버튼 비활성화 및 로딩 표시
        const sendBtn = document.getElementById('chatSendBtn');
        sendBtn.disabled = true;
        sendBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span>';

        // 대기 봇 메세지 추가
        const botMsgDiv = appendMessage('bot', '<span class="spinner-grow spinner-grow-sm text-secondary"></span> 인공지능이 이미지를 분석하고 검색 쿼리를 도출하는 중입니다...');

        try {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: userText })
            });
            const data = await response.json();

            // 대기 메세지 업데이트
            if (data.success) {
                let htmlContent = `<div>${data.reply.replace(/\\n/g, '<br>')}</div>`;
                
                // 검색된 이미지가 있을 경우 격자 UI 생성
                if (data.images && data.images.length > 0) {
                    htmlContent += `<div class="mt-2 fw-bold text-dark small">📍 찾은 스마트 팩토리 이미지 (${data.images.length}건):</div>`;
                    htmlContent += '<div class="chat-image-grid">';
                    data.images.forEach(img => {
                        htmlContent += `
                            <img src="${img.file_url}" class="chat-img-card" 
                                 onclick="showImageDetail('${img.file_url}', '${img.title.replace(/'/g, "\\'")}', '${img.serial_code}', '${img.category}', '${img.description.replace(/'/g, "\\'")}', '${img.uploaded_at}')" 
                                 title="${img.title} (${img.serial_code})">
                        `;
                    });
                    htmlContent += '</div>';
                } else {
                    htmlContent += '<div class="text-muted small mt-2">ℹ️ 조건에 정확히 부합하는 이미지가 수집되지 않았습니다. 다른 키워드로 검색해 보세요.</div>';
                }
                
                botMsgDiv.querySelector('.message-content').innerHTML = htmlContent;
            } else {
                botMsgDiv.querySelector('.message-content').innerHTML = `⚠️ 오류가 발생했습니다: ${data.error}`;
            }
        } catch (error) {
            botMsgDiv.querySelector('.message-content').innerHTML = '❌ Ollama 엔진에 연결할 수 없습니다. 로컬 Ollama 서버가 켜져 있는지 확인해 주세요.';
        } finally {
            sendBtn.disabled = false;
            sendBtn.innerHTML = '전송';
            // 자동 스크롤
            const chatMessages = document.getElementById('chatMessages');
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }
    });

    function appendMessage(sender, text) {
        const chatMessages = document.getElementById('chatMessages');
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${sender}`;
        
        messageDiv.innerHTML = `
            <div class="message-content shadow-xs">
                ${text}
            </div>
        `;
        chatMessages.appendChild(messageDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        return messageDiv;
    }
</script>
{% endblock %}
'''

ADMIN_TEMPLATE = '''
{% extends "base.html" %}
{% block content %}
<div class="d-flex justify-content-between align-items-center mb-4">
    <h2>스마트 이미지 관리자 패널</h2>
</div>

<!-- 통합 검색 폼 적용 -->
{% include "search_form.html" %}

<div class="row">
    <!-- 업로드 폼 -->
    <div class="col-lg-5 mb-4">
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
    <div class="col-lg-7">
        <div class="card shadow-sm">
            <div class="card-header bg-secondary text-white">등록된 이미지 데이터 관리 리스트</div>
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
                            <tr><td colspan="5" class="text-center py-5 text-muted">검색 조건과 일치하거나 등록된 이미지가 없습니다.</td></tr>
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
    'login.html': LOGIN_TEMPLATE,
    'search_form.html': SEARCH_FORM_TEMPLATE  # 공통 검색 폼 컴포넌트 추가
})


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# --- 라우팅 (URL 처리) ---

@app.route('/')
def index():
    """메인 화면: 검색 및 정렬 필터링이 적용된 탭별 이미지 조회"""
    params = {
        'q': request.args.get('q', ''),
        'start_date': request.args.get('start_date', ''),
        'end_date': request.args.get('end_date', ''),
        'sort_by': request.args.get('sort_by', 'uploaded_at_desc')
    }
    
    images = get_filtered_images(params)
    return render_template('index.html', images=images, params=params, action_url=url_for('index'), model_name=OLLAMA_MODEL)

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
    """관리자 대시보드: 검색 및 정렬 필터링이 적용된 이미지 관리 리스트"""
    if not session.get('is_admin'):
        flash('관리자 권한이 필요합니다.', 'warning')
        return redirect(url_for('login'))
        
    params = {
        'q': request.args.get('q', ''),
        'start_date': request.args.get('start_date', ''),
        'end_date': request.args.get('end_date', ''),
        'sort_by': request.args.get('sort_by', 'uploaded_at_desc')
    }
    
    images = get_filtered_images(params)
    return render_template('admin.html', images=images, params=params, action_url=url_for('admin'))

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
        
        # 오늘 등록된 기존 이미지 수 조회
        today_str = datetime.now().strftime('%Y%m%d')
        query_res = client.query("SELECT count() FROM image_data WHERE toDate(uploaded_at) = today()")
        today_count = query_res.result_rows[0][0]
        
        upload_records = []
        saved_filepaths = []
        
        for i, file in enumerate(files):
            if file and allowed_file(file.filename):
                seq = today_count + 1 + i
                serial_code = f"FAC-{today_str}-{seq:04d}"
                
                ext = file.filename.rsplit('.', 1)[1].lower()
                unique_filename = f"{category}_{today_str}_{seq:04d}_{uuid.uuid4().hex[:8]}.{ext}"
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                
                file.save(filepath)
                saved_filepaths.append(filepath)
                
                title = titles[i] if i < len(titles) else file.filename
                description = descriptions[i] if i < len(descriptions) else ""
                file_url = url_for('static', filename=f'uploads/{unique_filename}')
                img_id = uuid.uuid4()
                now = datetime.now()
                
                upload_records.append([img_id, category, title, description, serial_code, unique_filename, file_url, now])
        
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
            
        flash('성공적으로 삭제되었습니다.', 'success')
    except Exception as e:
        flash(f'삭제 중 오류가 발생했습니다: {e}', 'danger')
        
    return redirect(url_for('admin'))


# --- Ollama AI 챗봇 API 라우트 추가 ---

@app.route('/api/chat', methods=['POST'])
def chat():
    """사용자 자연어 쿼리를 Ollama를 이용해 분석하고 ClickHouse 데이터와 하이브리드 RAG 처리하여 반환합니다."""
    data = request.get_json()
    user_message = data.get('message', '').strip()
    
    if not user_message:
        return jsonify({'success': False, 'error': '메시지가 빈값입니다.'})
        
    # 1단계: Ollama 모델에 질의하여 자연어에서 카테고리와 검색 키워드를 추출하도록 프롬프트 설계
    # JSON 출력을 유도하여 정확한 키를 분석하게 합니다.
    system_prompt = (
        "You are an AI assistant for a Smart Factory Image Portal. "
        "Your task is to analyze the user's Korean search query and extract structured query parameters "
        "for searching the image database. "
        "You MUST respond ONLY with a raw JSON object with the exact keys: "
        "'category' (string, must be one of: 'blueprint', 'factory', 'vision', or 'all') "
        "and 'keywords' (array of strings, containing core search terms like '러시아', 'NG', '불량', '소방' 등)."
        "Do not include any explanation or markdown tags in your response. "
        "For example, if the user says '러시아 공장 전경 보여줘', "
        "respond with: {\"category\": \"factory\", \"keywords\": [\"러시아\"]}. "
        "If they say 'NG 발생한 비전 데이터', "
        "respond with: {\"category\": \"vision\", \"keywords\": [\"NG\", \"불량\"]}"
    )
    
    extracted_filter = {"category": "all", "keywords": []}
    
    try:
        # Ollama API 요청
        response = requests.post(OLLAMA_API_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": f"{system_prompt}\n\nUser Query: {user_message}\nJSON:",
            "stream": False,
            "options": {
                "temperature": 0.1  # 결정론적 응답을 보장하여 JSON 오류 방지
            }
        }, timeout=10)
        
        if response.status_code == 200:
            raw_response = response.json().get('response', '{}').strip()
            # 마크다운 백틱 코드 블럭이 섞여올 것에 대비하여 클렌징
            if "```" in raw_response:
                raw_response = raw_response.split("```")[1]
                if raw_response.startswith("json"):
                    raw_response = raw_response[4:]
            
            extracted_filter = json.loads(raw_response.strip())
    except Exception as e:
        print(f"Ollama 파싱 실패 또는 미기동 (기본 키워드로 직접 매칭 대체): {e}")
        # Ollama가 꺼져있을 경우 최소한의 수동 키워드 예측 적용
        words = user_message.split()
        keywords = [w for w in words if len(w) > 1]
        cat = "all"
        if "도면" in user_message or "설계" in user_message:
            cat = "blueprint"
        elif "전경" in user_message or "공장" in user_message:
            cat = "factory"
        elif "비전" in user_message or "vision" in user_message.lower() or "불량" in user_message or "ng" in user_message.lower():
            cat = "vision"
        extracted_filter = {"category": cat, "keywords": keywords}

    # 2단계: 추출된 메타정보를 활용하여 ClickHouse SQL 빌드 및 동적 수행
    category = extracted_filter.get('category', 'all')
    keywords = extracted_filter.get('keywords', [])
    
    try:
        init_db()
        client = get_ch_client()
        
        # ClickHouse 동적 하이브리드 검색 쿼리 작성
        query = "SELECT id, category, title, description, serial_code, filename, file_url, uploaded_at FROM image_data WHERE 1=1"
        params = {}
        
        if category in ['blueprint', 'factory', 'vision']:
            query += " AND category = %(category)s"
            params['category'] = category
            
        # 다중 키워드 시맨틱 유사 검색을 ClickHouse ILIKE로 체이닝
        for i, keyword in enumerate(keywords):
            param_name = f"kw_{i}"
            query += f" AND (title ILIKE %({param_name})s OR description ILIKE %({param_name})s OR serial_code ILIKE %({param_name})s)"
            params[param_name] = f"%{keyword}%"
            
        # 최신 파일 순으로 정렬 제한
        query += " ORDER BY uploaded_at DESC LIMIT 15"
        
        result = client.query(query, parameters=params)
        images = [
            {
                'id': row[0], 'category': row[1], 'title': row[2], 'description': row[3],
                'serial_code': row[4], 'filename': row[5], 'file_url': row[6], 'uploaded_at': row[7].strftime('%Y-%m-%d %H:%M:%S')
            }
            for row in result.result_rows
        ]
        
        # 3단계: 검색된 결과 셋을 기반으로 Ollama에 "답변 문장" 생성을 다시 요청(RAG 피드백)
        context_summary = ""
        for img in images[:5]:  # 상위 5개 컨텍스트 요약 전달
            context_summary += f"- 관리번호: {img['serial_code']}, 제목: {img['title']}, 설명: {img['description']}\\n"
            
        chatbot_prompt = (
            f"사용자는 스마트 팩토리 포털에서 다음 질문을 했습니다: '{user_message}'\\n\\n"
            f"검색 조건 결과로 총 {len(images)}건의 이미지 데이터가 수집되었습니다.\\n"
            f"다음은 수집된 이미지 목록의 메타데이터 컨텍스트입니다:\\n{context_summary}\\n"
            "이 결과를 요약하여 사용자가 요청한 이미지를 어떤 조건으로 조회했는지 친절하고 자연스러운 한국어로 설명해 주세요. "
            "사용자의 질문에 부합하는 분석(예: 'NG 비전 데이터만 2건 추출되었습니다', '러시아 공장 사진은 총 1건 확인되었습니다')을 적절히 포함해 주세요. "
            "만약 결과가 없다면 등록된 자료가 없다는 것을 알려주고 대안 키워드를 제시해 주세요."
        )
        
        reply_text = f"검색된 {len(images)}건의 스마트 팩토리 이미지 카탈로그를 보여드립니다."
        try:
            bot_response = requests.post(OLLAMA_API_URL, json={
                "model": OLLAMA_MODEL,
                "prompt": chatbot_prompt,
                "stream": False
            }, timeout=15)
            if bot_response.status_code == 200:
                reply_text = bot_response.json().get('response', reply_text)
        except Exception as api_err:
            print(f"Ollama 최종 답변 생성 지연/실패 (기본 텍스트 적용): {api_err}")
            reply_text = (
                f"사용자님의 '{user_message}' 질문에 일치하는 스마트 팩토리 데이터 분석 결과입니다. "
                f"총 {len(images)}건의 맞춤 이미지를 아래 리스트에 시각화해 두었습니다. "
                f"(추출 키워드: {', '.join(keywords) if keywords else '전체'})"
            )
            
        return jsonify({
            'success': True,
            'reply': reply_text,
            'images': images
        })
        
    except Exception as db_err:
        return jsonify({'success': False, 'error': f'ClickHouse 연동 및 데이터 분석 오류: {str(db_err)}'})

if __name__ == '__main__':
    print("🚀 Flask 웹 서버 및 AI 어시스턴트 서비스 기동 중...")
    app.run(host='0.0.0.0', port=5000, debug=True)