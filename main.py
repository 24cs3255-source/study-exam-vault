from flask import Blueprint, render_template, request, jsonify, send_from_directory, current_app, abort, session, flash, redirect
from models import QuestionPaper, AdminNotification, DiscussionPost, StudentAIChat, Student
from extensions import db, csrf, get_upload_path
import os
import urllib.request
import urllib.error
import json
import pdfplumber
import io

main_bp = Blueprint('main', __name__)

# ── Canonical class choices (single source of truth) ──────────────────────────
CLASS_CHOICES = ['B.Sc', 'B.Com', 'B.A', 'B.E', 'B.Tech', 'BCA', 'BBA', 'MBA', 'MCA', 'M.Sc']


# ── Landing page ────────────────────────────────────────────────────────────────
@main_bp.route('/')
def landing():
    return render_template('landing.html')


# ── Home page (Explore Portal) ──────────────────────────────────────────────────
@main_bp.route('/explore')
def index():
    departments = db.session.query(QuestionPaper.department).distinct().order_by(QuestionPaper.department).all()
    semesters   = db.session.query(QuestionPaper.semester).distinct().order_by(QuestionPaper.semester).all()
    exam_types  = db.session.query(QuestionPaper.exam_type).distinct().order_by(QuestionPaper.exam_type).all()
    years       = db.session.query(QuestionPaper.year).distinct().order_by(QuestionPaper.year.desc()).all()
    class_names = (
        db.session.query(QuestionPaper.class_name)
        .filter(QuestionPaper.class_name.isnot(None))
        .distinct()
        .order_by(QuestionPaper.class_name)
        .all()
    )

    return render_template(
        'index.html',
        departments=[d[0] for d in departments],
        semesters=[s[0] for s in semesters],
        exam_types=[e[0] for e in exam_types],
        years=[y[0] for y in years],
        class_names=[c[0] for c in class_names],
    )


# ── AJAX search / filter API ───────────────────────────────────────────────────
@main_bp.route('/api/papers')
def api_papers():
    q          = request.args.get('q', '').strip()
    department = request.args.get('department', '')
    semester   = request.args.get('semester', '')
    class_name = request.args.get('class_name', '')

    query = QuestionPaper.query.filter(
        db.or_(
            QuestionPaper.status == 'approved',
            QuestionPaper.status.is_(None)
        )
    )

    if q:
        like = f'%{q}%'
        query = query.filter(
            db.or_(
                QuestionPaper.subject_name.ilike(like),
                QuestionPaper.subject_code.ilike(like),
            )
        )
    if department and department != 'all':
        # Also fetch papers stored as 'All Departments' (e.g. Tamil, English)
        # so common subjects are returned without duplicating rows in the DB.
        query = query.filter(
            db.or_(
                QuestionPaper.department == department,
                QuestionPaper.department == 'All Departments',
            )
        )
    if semester:
        query = query.filter(QuestionPaper.semester == semester)
    if class_name:
        query = query.filter(QuestionPaper.class_name == class_name)

    papers = query.order_by(QuestionPaper.year.desc(), QuestionPaper.subject_name).all()
    return jsonify([p.to_dict() for p in papers])


@main_bp.route('/api/papers/bookmarks', methods=['POST'])
@csrf.exempt
def api_papers_bookmarks():
    data = request.get_json() or {}
    ids = data.get('ids', [])
    if not ids:
        return jsonify([])
    papers = QuestionPaper.query.filter(
        QuestionPaper.id.in_(ids),
        db.or_(
            QuestionPaper.status == 'approved',
            QuestionPaper.status.is_(None)
        )
    ).all()
    return jsonify([p.to_dict() for p in papers])


# ── Serve / download PDFs ──────────────────────────────────────────────────────
@main_bp.route('/view/<int:paper_id>')
def view_paper(paper_id):
    paper = QuestionPaper.query.get_or_404(paper_id)
    file_path = get_upload_path(paper.filename)
    if not os.path.isfile(file_path):
        abort(404)
    return send_from_directory(
        os.path.dirname(file_path),
        os.path.basename(file_path),
        mimetype='application/pdf',
        as_attachment=False,
    )


@main_bp.route('/download/<int:paper_id>')
def download_paper(paper_id):
    paper = QuestionPaper.query.get_or_404(paper_id)
    file_path = get_upload_path(paper.filename)
    if not os.path.isfile(file_path):
        abort(404)
    try:
        paper.download_count = (paper.download_count or 0) + 1
        db.session.commit()
    except Exception:
        db.session.rollback()

    # Log download if student is logged in
    student_id = session.get('student_id')
    if student_id:
        try:
            from models import StudentDownload
            existing = StudentDownload.query.filter_by(student_id=student_id, paper_id=paper_id).first()
            if not existing:
                dl = StudentDownload(student_id=student_id, paper_id=paper_id)
                db.session.add(dl)
                db.session.commit()
        except Exception:
            db.session.rollback()

    return send_from_directory(
        os.path.dirname(file_path),
        os.path.basename(file_path),
        as_attachment=True,
        download_name=paper.original_name or paper.filename,
    )


# ── All papers page ────────────────────────────────────────────────────────────
@main_bp.route('/papers')
def papers():
    from sqlalchemy import func

    all_papers   = QuestionPaper.query.order_by(QuestionPaper.uploaded_at.desc()).all()
    total_papers = len(all_papers)
    total_depts  = db.session.query(func.count(QuestionPaper.department.distinct())).scalar() or 0
    year_min     = db.session.query(func.min(QuestionPaper.year)).scalar()
    year_max     = db.session.query(func.max(QuestionPaper.year)).scalar()
    year_range   = f"{year_min} – {year_max}" if year_min and year_max else "N/A"

    return render_template(
        'papers.html',
        papers=all_papers,
        total_papers=total_papers,
        total_depts=total_depts,
        year_range=year_range,
    )



# ─────────────────────────────────────────────────────────────────────────────
#  AI Study Assistant Integration (Gemini API)
# ─────────────────────────────────────────────────────────────────────────────

def get_gemini_api_key() -> str:
    for key_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        api_key = os.environ.get(key_name)
        if api_key and api_key.strip():
            return api_key.strip()
    return ""


def query_gemini(prompt: str) -> str:
    api_key = get_gemini_api_key()
    if not api_key:
        raise ValueError("API Key is missing")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={api_key}"
    data = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            text = res_data['candidates'][0]['content']['parts'][0]['text']
            return text
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode("utf-8")
        print(f"Gemini API HTTP Error: {error_msg}")
        raise ValueError(f"HTTP Error {e.code}: {e.reason}")
    except Exception as e:
        print(f"Gemini API Error: {e}")
        raise e


def query_gemini_with_file(prompt: str, file_path: str) -> str:
    api_key = get_gemini_api_key()
    if not api_key:
        raise ValueError("API Key is missing")
        
    from google import genai
        
    client = genai.Client(api_key=api_key)
    
    print(f"Uploading scanned PDF to Gemini Files API: {file_path}")
    uploaded_file = client.files.upload(file=file_path)
    
    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=[uploaded_file, prompt]
        )
        return response.text
    finally:
        try:
            client.files.delete(name=uploaded_file.name)
            print(f"Deleted uploaded file {uploaded_file.name} from Gemini API storage.")
        except Exception as ex:
            print(f"Error cleaning up uploaded Gemini file: {ex}")


def get_mock_ai_response(action: str, paper: QuestionPaper, message: str, rate_limited: bool = False) -> str:
    notice_str = (
        "> ⚠️ **Notice**: Live AI is currently rate-limited or temporarily exhausted (Gemini free tier has a limit of 20 requests per day). Showing cached/simulated revision guide."
        if rate_limited else
        "> ⚠️ **Notice**: Running in preview mode. Set the `GEMINI_API_KEY` environment variable in your `.env` file for real AI generation."
    )
    
    if action == 'revision':
        return f"""# 📚 AI Revision Guide: {paper.subject_name} ({paper.subject_code})
        
{notice_str}

## 1. Key Concepts & Core Topics
Based on the subject code **{paper.subject_code}** and subject **{paper.subject_name}**, here are the high-yield units:
- **Unit I: Fundamentals**: Core definitions, basic terms, and architectures.
- **Unit II: Design Principles**: Methods, models, and paradigms.
- **Unit III: Advanced Applications**: Performance analysis and integrations.

## 2. Expected Important Questions
1. Detail the architectural differences and major components.
2. Explain the workflow of key processes and algorithms with diagrams.
3. Discuss security, optimization, and future trends in this domain.

## 3. High-Yield Revision Tips
- Practice writing key definitions exactly as per textbook standards.
- Prepare schematics/flowcharts; drawing clean diagrams scores high in semester evaluations.
- Focus on past 3-year questions as patterns repeat up to 60%.
"""
    elif action == 'answers':
        return f"""# ✍️ Model Answers: {paper.subject_name}
        
{notice_str}

### Question 1: Explain the primary components of {paper.subject_name} systems.
**Answer:**
{paper.subject_name} systems are composed of three primary layers:
1. **Presentation Layer**: The user interface that handles request presentation.
2. **Business Logic Layer**: The core logic executing computations.
3. **Data Access Layer**: The database connector managing persistence.

### Question 2: Elaborate on the significance of the code {paper.subject_code}.
**Answer:**
The subject code **{paper.subject_code}** refers to the curriculum design guidelines. It signifies:
- **Regulation compliance**: Adapts to modernized syllabus requirements.
- **Scope**: Combines theoretical foundation with laboratory execution.
"""
    elif action == 'quiz':
        return f"""# 📝 Interactive Mock Quiz: {paper.subject_name}
        
{notice_str}

### Q1: What is the main objective of {paper.subject_name}?
- [ ] A) Reducing database storage size
- [ ] B) Providing systematic design and analysis of the subject domain
- [ ] C) Visualizing simple user interfaces only
- [ ] D) Running compilers and loaders

<details><summary><b>Reveal Answer</b></summary>
<b>Correct Option: B</b>
<br><i>Explanation:</i> {paper.subject_name} provides the formal foundations and practical methodologies for resolving problem statements in this discipline.
</details>

### Q2: Which semester does subject {paper.subject_code} belong to?
- [ ] A) 1st Semester
- [ ] B) 3rd Semester
- [ ] C) {paper.semester} Semester
- [ ] D) 8th Semester

<details><summary><b>Reveal Answer</b></summary>
<b>Correct Option: C</b>
<br><i>Explanation:</i> According to current college records, {paper.subject_code} is listed under the {paper.semester} Semester curriculum.
</details>
"""
    elif action == 'two_marks':
        return f"""# 🎯 2-Mark Question Answers: {paper.subject_name} ({paper.subject_code})
        
{notice_str}

### Q1: What is the main purpose of {paper.subject_name}?
**Answer:** The primary purpose of {paper.subject_name} is to establish structured models, methodologies, and analysis techniques for solving problem statements in the computing/academic domain.

### Q2: State the significance of the code {paper.subject_code} in the curriculum.
**Answer:** The curriculum code **{paper.subject_code}** represents the regulated academic course syllabus, ensuring students align with standard guidelines and practical assessments.

### Q3: Define a system bottleneck in the context of this subject.
**Answer:** A bottleneck is a point of congestion in a system that occurs when workloads arrive too quickly for the production/execution process to handle, reducing overall throughput.

### Q4: List two advantages of utilizing historical question papers.
**Answer:**
1. Helps students understand pattern weightage and recurring syllabus topics.
2. Serves as a mock assessment tool for personal time-management testing.

### Q5: What is the threshold limit in this curriculum regulation?
**Answer:** The threshold limit refers to the minimum passing standards (typically 50% internal and external combined) required to clear the coursework for {paper.subject_code}.
"""
    else: # chat
        if rate_limited:
            return f"""**AI Tutor**: Hello! I am your AI Study assistant for **{paper.subject_name}**.

⚠️ **Notice**: My live AI generation rate-limit was exceeded just now. I am answering using a cached response context.

To study the details, you asked: *"{message}"*
This topic is essential for **{paper.subject_code}** and is commonly asked as a 10-mark question! Let me know if you need help with anything else."""
        else:
            return f"""**AI Tutor**: Hello! I am your AI Study assistant for **{paper.subject_name}**.

⚠️ **Notice**: Running in preview mode because no `GEMINI_API_KEY` was found in the environment.

In the meantime, you asked: *"{message}"*
As a tutor, I can tell you that this topic is essential for **{paper.subject_code}** and is commonly asked as a 10-mark question! Let me know if you need help with anything else."""


@main_bp.route('/api/ai/chat/history/<int:paper_id>', methods=['GET'])
def ai_chat_history(paper_id):
    student_id = session.get('student_id')
    if not student_id:
        return jsonify({'success': False, 'error': 'You must be logged in to view chat history.'}), 401

    chats = StudentAIChat.query.filter_by(student_id=student_id, paper_id=paper_id)\
        .order_by(StudentAIChat.created_at.asc()).all()

    return jsonify({
        'success': True,
        'history': [c.to_dict() for c in chats]
    })


@main_bp.route('/api/ai/study-guide', methods=['POST'])
@csrf.exempt
def ai_study_guide():
    data = request.get_json() or {}
    paper_id = data.get('paper_id')
    action = data.get('action') # 'revision', 'answers', 'quiz', 'chat'
    user_message = data.get('message', '').strip()
    
    if not paper_id or not action:
        return jsonify({'success': False, 'error': 'Missing paper_id or action'}), 400
        
    paper = QuestionPaper.query.get_or_404(paper_id)

    student_id = session.get('student_id')
    student = None
    if student_id:
        student = Student.query.get(student_id)

    # ── AI Cache retrieval ──────────────────────────────────────────────────────
    from models import AICache
    if action in ['revision', 'answers', 'quiz', 'two_marks']:
        cache = AICache.query.filter_by(paper_id=paper_id, action=action).first()
        if cache:
            api_key = get_gemini_api_key()
            # If cached response is real (not mock), or if we are in offline/preview mode (no API key available)
            if not cache.is_mock or not api_key:
                # Fetch database questions for this subject code
                db_questions_md = ""
                if action in ['answers', 'two_marks']:
                    try:
                        from models import Question
                        db_questions = Question.query.filter_by(subject_code=paper.subject_code).all()
                        if db_questions:
                            db_questions_md = "### 📂 Database Practice Questions\n"
                            db_questions_md += "Click a question below to open its details page and generate a custom AI Answer:\n\n"
                            for q in db_questions:
                                db_questions_md += f"- ❓ [**{q.text}**](/question/{q.id}) *(Marks: {q.marks})*\n"
                            db_questions_md += "\n---\n\n"
                    except Exception as ex:
                        print(f"Error fetching db questions: {ex}")
                
                return jsonify({
                    'success': True,
                    'response': db_questions_md + cache.response_text,
                    'is_mock': cache.is_mock,
                    'notice': 'Loaded from cached AI response.' if not cache.is_mock else 'Running in preview mode. Showing cached mock response.'
                })

    # Save student message if chat
    if action == 'chat' and student and user_message:
        student_chat = StudentAIChat(
            student_id=student.id,
            paper_id=paper.id,
            sender='student',
            message=user_message
        )
        db.session.add(student_chat)
        db.session.commit()
    
    # Extract context text from PDF if it exists
    pdf_text = ""
    file_path = get_upload_path(paper.filename)
    if os.path.isfile(file_path):
        try:
            with pdfplumber.open(file_path) as pdf:
                pages_to_read = pdf.pages
                for page in pages_to_read:
                    text = page.extract_text()
                    if text:
                        pdf_text += "\n" + text
        except Exception as e:
            print(f"Error extracting PDF context: {e}")
            
    pdf_text = pdf_text.strip()
    
    context_desc = (
        f"Subject Code: {paper.subject_code}\n"
        f"Subject Name: {paper.subject_name}\n"
        f"Department: {paper.department}\n"
        f"Semester: {paper.semester}\n"
        f"Year of Exam: {paper.year}\n"
    )
    
    is_scanned = (len(pdf_text) < 100)

    # Formulate prompts
    if is_scanned:
        if action == 'revision':
            prompt = (
                f"You are a helpful college professor. Analyze the uploaded scanned exam paper for:\n"
                f"{context_desc}\n\n"
                f"Provide a structured, easy-to-read Revision Study Guide for this subject. Include:\n"
                f"1. Core Concepts & Definitions based on the questions in this scanned paper.\n"
                f"2. High-Yield/Important Topics that students should focus on.\n"
                f"3. Revision tips.\n"
                f"Use markdown headers, lists, and bold text. Keep it concise, engaging, and extremely helpful for a student revising 1 day before the exam."
            )
        elif action == 'answers':
            prompt = (
                f"You are a college teacher. Analyze the uploaded scanned exam paper for:\n"
                f"{context_desc}\n\n"
                f"Identify the major/complex questions in this exam paper. Select 3 important questions (e.g. 8-mark or 16-mark questions) from the paper and provide clear, comprehensive university-standard model answers or step-by-step solutions for them so students can study how to answer them in the semester exam.\n"
                f"Use markdown formatting and organize it cleanly with headings."
            )
        elif action == 'quiz':
            prompt = (
                f"You are an examiner. Analyze the uploaded scanned exam paper for:\n"
                f"{context_desc}\n\n"
                f"Generate a mock practice quiz containing 5 multiple-choice questions (MCQs) based on the topics and questions in this scanned paper.\n"
                f"Format it beautifully using markdown. For each question, provide 4 options (A, B, C, D) and then show the correct answer and a brief 1-sentence explanation hidden inside a collapsible details tag like this:\n"
                f"<details><summary><b>Reveal Answer</b></summary>Correct Option: A. Explanation: ...</details>\n"
                f"This will allow students to self-test interactively on the webpage."
            )
        elif action == 'two_marks':
            prompt = (
                f"You are an expert college professor. Analyze the uploaded scanned exam paper for:\n"
                f"{context_desc}\n\n"
                f"Scan the scanned PDF pages and identify all 2-mark (short answer) questions.\n"
                f"For each 2-mark question found in this paper, write the question verbatim, and write a precise, high-scoring 2-3 sentence answer suitable for semester exams.\n"
                f"If the scanned paper does not contain 2-mark questions, generate 5 typical 2-mark questions and answers for this subject syllabus using your own academic knowledge.\n"
                f"Format it beautifully using markdown with headers and bold text."
            )
        elif action == 'chat':
            if not user_message:
                return jsonify({'success': False, 'error': 'Message is required for chat'}), 400
            prompt = (
                f"You are QuestBank AI, an expert university professor.\n"
                f"You are acting as an academic tutor for this scanned exam paper:\n"
                f"{context_desc}\n\n"
                f"A student is asking a question about this paper or subject. Read the scanned pages of the PDF to answer. "
                f"If they ask to answer a question from the paper, locate it, analyze it, and write a complete university-standard answer according to the marks (2, 8 or 16 marks) using headings, bullet points, examples, and diagram explanations if applicable. "
                f"If the paper contains only questions, use your own academic knowledge to answer. Never say 'The PDF is scanned' or 'I cannot answer'.\n"
                f"Student Question: '{user_message}'"
            )
        else:
            return jsonify({'success': False, 'error': 'Invalid action'}), 400
    else:
        # Text-based PDF prompts
        context = f"{context_desc}\nHere is some text content extracted from the exam paper:\n{pdf_text[:8000]}"
        if action == 'revision':
            prompt = (
                f"You are a helpful college professor. Based on this exam paper details:\n\n{context}\n\n"
                f"Provide a structured, easy-to-read Revision Study Guide for this subject. Include:\n"
                f"1. Core Concepts & Definitions (based on subject name/code and questions if available).\n"
                f"2. High-Yield/Important Topics that students should focus on.\n"
                f"3. Revision tips.\n"
                f"Use markdown headers, lists, and bold text. Keep it concise, engaging, and extremely helpful for a student revising 1 day before the exam."
            )
        elif action == 'answers':
            prompt = (
                f"You are a college teacher. Based on this exam paper details:\n\n{context}\n\n"
                f"Select 3 important/complex questions or topics that are typical for this subject ({paper.subject_name}). "
                f"Provide clear, model answers or step-by-step solutions/explanations for them so students can study how to answer them in the semester exam.\n"
                f"Use markdown formatting and organize it cleanly."
            )
        elif action == 'quiz':
            prompt = (
                f"You are an examiner. Based on this exam details:\n\n{context}\n\n"
                f"Generate a mock practice quiz containing 5 multiple-choice questions (MCQs) for the subject '{paper.subject_name}'.\n"
                f"Format it beautifully using markdown. For each question, provide 4 options (A, B, C, D) and then show the correct answer and a brief 1-sentence explanation hidden inside a collapsible details tag like this:\n"
                f"<details><summary><b>Reveal Answer</b></summary>Correct Option: A. Explanation: ...</details>\n"
                f"This will allow students to self-test interactively on the webpage."
            )
        elif action == 'two_marks':
            prompt = (
                f"You are a college professor. You must analyze the following extracted text from a specific question paper:\n\n"
                f"--- EXTRACTED EXAM TEXT START ---\n{context}\n--- EXTRACTED EXAM TEXT END ---\n\n"
                f"Task:\n"
                f"1. Read the text above, find the 2-mark questions (typically under Part-A or Part-I).\n"
                f"2. For each identified 2-mark question, write the question verbatim, and write a precise, high-scoring 2-3 sentence answer suitable for semester exams.\n"
                f"3. You MUST ONLY answer the actual questions present in the text above. Do NOT generate generic or typical questions. If the text above is empty, contains no text, or is not readable, reply exactly with: 'Error: This question paper is a scanned image or empty. Please ask your questions in the Ask Tutor tab to get answers.'\n"
                f"Format the output using markdown headers and lists."
            )
        elif action == 'chat':
            if not user_message:
                return jsonify({'success': False, 'error': 'Message is required for chat'}), 400
            prompt = (
                f"You are QuestBank AI, an expert university professor.\n\n"
                f"The uploaded document is a scanned university question paper. Here is the metadata and extracted text content:\n\n"
                f"{context}\n\n"
                f"When the student asks any question:\n"
                f"- Find that question in the uploaded paper / metadata.\n"
                f"- Analyse the question.\n"
                f"- Generate a complete university-standard answer.\n"
                f"- Use your own academic knowledge whenever the PDF contains only questions.\n"
                f"- Answer according to the marks (2, 8 or 16 marks).\n"
                f"- Use headings, bullet points and examples.\n"
                f"- If applicable, explain the diagram.\n"
                f"- Never say 'The PDF contains only questions, so I cannot answer.'\n\n"
                f"Student Question: '{user_message}'"
            )
        else:
            return jsonify({'success': False, 'error': 'Invalid action'}), 400

    # Fetch database questions for this subject code
    db_questions_md = ""
    if action in ['answers', 'two_marks']:
        try:
            from models import Question
            db_questions = Question.query.filter_by(subject_code=paper.subject_code).all()
            if db_questions:
                db_questions_md = "### 📂 Database Practice Questions\n"
                db_questions_md += "Click a question below to open its details page and generate a custom AI Answer:\n\n"
                for q in db_questions:
                    db_questions_md += f"- ❓ [**{q.text}**](/question/{q.id}) *(Marks: {q.marks})*\n"
                db_questions_md += "\n---\n\n"
        except Exception as ex:
            print(f"Error fetching db questions: {ex}")

    # Execute query
    api_key = get_gemini_api_key()
    if not api_key:
        mock_response = get_mock_ai_response(action, paper, user_message)
        if action == 'chat' and student and mock_response:
            tutor_chat = StudentAIChat(
                student_id=student.id,
                paper_id=paper.id,
                sender='tutor',
                message=mock_response
            )
            db.session.add(tutor_chat)
            db.session.commit()
        elif action in ['revision', 'answers', 'quiz', 'two_marks'] and mock_response:
            from models import AICache
            cache = AICache.query.filter_by(paper_id=paper.id, action=action).first()
            if not cache:
                cache = AICache(paper_id=paper.id, action=action, response_text=mock_response, is_mock=True)
                db.session.add(cache)
                db.session.commit()
        return jsonify({
            'success': True, 
            'response': db_questions_md + mock_response,
            'is_mock': True,
            'notice': 'To enable live AI answers, set the GEMINI_API_KEY environment variable in your .env file.'
        })
        
    try:
        if is_scanned and os.path.isfile(file_path):
            print("Executing hybrid scanned PDF workflow using Gemini Files API...")
            response_text = query_gemini_with_file(prompt, file_path)
        else:
            print("Executing standard text-based PDF workflow...")
            response_text = query_gemini(prompt)

        if action == 'chat' and student and response_text:
            tutor_chat = StudentAIChat(
                student_id=student.id,
                paper_id=paper.id,
                sender='tutor',
                message=response_text
            )
            db.session.add(tutor_chat)
            db.session.commit()
        elif action in ['revision', 'answers', 'quiz', 'two_marks'] and response_text:
            from models import AICache
            cache = AICache.query.filter_by(paper_id=paper.id, action=action).first()
            if not cache:
                cache = AICache(paper_id=paper.id, action=action)
            cache.response_text = response_text
            cache.is_mock = False
            db.session.add(cache)
            db.session.commit()
        return jsonify({
            'success': True,
            'response': db_questions_md + response_text,
            'is_mock': False
        })
    except Exception as e:
        error_msg = str(e)
        print(f"Gemini API execution error: {error_msg}")
        mock_response = get_mock_ai_response(action, paper, user_message, rate_limited=True)
        if action == 'chat' and student and mock_response:
            tutor_chat = StudentAIChat(
                student_id=student.id,
                paper_id=paper.id,
                sender='tutor',
                message=mock_response
            )
            db.session.add(tutor_chat)
            db.session.commit()
        elif action in ['revision', 'answers', 'quiz', 'two_marks'] and mock_response:
            from models import AICache
            cache = AICache.query.filter_by(paper_id=paper.id, action=action).first()
            if not cache:
                cache = AICache(paper_id=paper.id, action=action, response_text=mock_response, is_mock=True)
                db.session.add(cache)
                db.session.commit()
        key_hint = (
            'The configured Gemini key is present but was rejected by Google. '
            'Generate a fresh API key in Google AI Studio and update GEMINI_API_KEY in your .env file.'
            if 'API key not valid' in error_msg or 'API_KEY_INVALID' in error_msg
            else f"Live AI rate-limited or unavailable ({error_msg}). Loaded offline guide."
        )
        return jsonify({
            'success': True,
            'response': db_questions_md + mock_response,
            'is_mock': True,
            'notice': key_hint
        })


# ─────────────────────────────────────────────────────────────────────────────
#  Student/Staff Public Contribution Routes
# ─────────────────────────────────────────────────────────────────────────────

from routes.admin import parse_pdf_metadata
from werkzeug.utils import secure_filename
import uuid

@main_bp.route('/api/parse-pdf-public', methods=['POST'])
@csrf.exempt
def api_parse_pdf_public():
    if 'pdf_file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        
    file = request.files['pdf_file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400
        
    try:
        file.seek(0)
        file_bytes = file.read()
        file_stream = io.BytesIO(file_bytes)
        
        metadata = parse_pdf_metadata(file_stream, file.filename)
        return jsonify({'success': True, 'metadata': metadata})
    except Exception as e:
        print(f"Public PDF parser error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/contribute', methods=['GET', 'POST'])
@csrf.exempt
def contribute():
    if not session.get('student_logged_in'):
        if request.method == 'POST':
            return jsonify({'success': False, 'error': 'Please log in to contribute papers.'}), 401
        flash('Please log in to access this page.', 'warning')
        return redirect('/student/login?force_student=true')

    if request.method == 'POST':
        pdf_file = request.files.get('pdf_file')
        department = request.form.get('department', '').strip()
        semester = request.form.get('semester', '').strip()
        subject_code = request.form.get('subject_code', '').strip().upper()
        subject_name = request.form.get('subject_name', '').strip()
        exam_type = request.form.get('exam_type', '').strip()
        year_str = request.form.get('year', '').strip()
        class_name = request.form.get('class_name', '').strip()
        
        if not (pdf_file and department and semester and subject_code and subject_name and exam_type and year_str):
            return jsonify({'success': False, 'error': 'All fields are required!'}), 400
            
        try:
            year = int(year_str)
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid year format!'}), 400
            
        orig_name = pdf_file.filename
        ext = os.path.splitext(orig_name)[1].lower()
        if ext != '.pdf':
            return jsonify({'success': False, 'error': 'Only PDF files are allowed!'}), 400
            
        unique_fn = f"{uuid.uuid4().hex}_{secure_filename(orig_name)}"
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_fn)
        pdf_file.save(file_path)
        
        paper = QuestionPaper(
            department=department,
            semester=semester,
            subject_code=subject_code,
            subject_name=subject_name,
            exam_type=exam_type,
            year=year,
            class_name=class_name or None,
            filename=unique_fn,
            original_name=orig_name,
            status='pending'
        )
        db.session.add(paper)
        db.session.flush() # Populate paper.id before commit
        
        # Add admin notification
        notif = AdminNotification(
            message=f"New paper contribution: {paper.subject_code} - {paper.subject_name} for {paper.department} (Semester {paper.semester})"
        )
        db.session.add(notif)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Paper submitted successfully! It will be live after Admin review.',
            'paper_id': paper.id,
            'subject_code': paper.subject_code,
            'subject_name': paper.subject_name
        })
        
    student = Student.query.get(session.get('student_id'))
    student_dept = student.department if student else None
    
    DEPT_MAP = {
        'CS': 'Computer Science (CS)',
        'BCA': 'Computer Applications (BCA)',
        'BA': 'Business Administration (BA)',
        'BOTANY': 'Botany',
        'PHYSICS': 'Physics',
        'CHEMISTRY': 'Chemistry',
        'MATHEMATICS': 'Mathematics',
        'ENGLISH': 'English',
        'COMMERCE': 'Commerce',
        'ECONOMICS': 'Economics'
    }
    
    depts = []
    if student_dept:
        depts.append({
            'value': student_dept,
            'label': DEPT_MAP.get(student_dept, student_dept)
        })
    depts.append({
        'value': 'All Departments',
        'label': 'All Departments'
    })
        
    return render_template('contribute.html', departments=depts)


@main_bp.route('/api/papers/contributions/status', methods=['POST'])
@csrf.exempt
def api_contributions_status():
    data = request.get_json() or {}
    ids = data.get('ids', [])
    if not ids:
        return jsonify([])
        
    try:
        int_ids = [int(i) for i in ids]
    except (ValueError, TypeError):
        return jsonify([])
        
    papers = QuestionPaper.query.filter(QuestionPaper.id.in_(int_ids)).all()
    found_ids = {p.id: p for p in papers}
    
    results = []
    for pid in int_ids:
        if pid in found_ids:
            paper = found_ids[pid]
            results.append({
                'id': pid,
                'status': paper.status or 'approved',
                'subject_code': paper.subject_code,
                'subject_name': paper.subject_name
            })
        else:
            results.append({
                'id': pid,
                'status': 'rejected'
            })
            
    return jsonify(results)


# ─────────────────────────────────────────────────────────────────────────────
#  Public Question Discussion Forum Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@main_bp.route('/api/papers/<int:paper_id>/discussion', methods=['GET'])
def get_discussion(paper_id):
    QuestionPaper.query.get_or_404(paper_id)
    posts = DiscussionPost.query.filter_by(paper_id=paper_id).order_by(DiscussionPost.created_at.asc()).all()
    serialized = [p.to_dict() for p in posts]
    return jsonify(serialized)


@main_bp.route('/api/papers/<int:paper_id>/discussion', methods=['POST'])
@csrf.exempt
def post_discussion(paper_id):
    QuestionPaper.query.get_or_404(paper_id)
    
    data = request.get_json() or {}
    author_name = data.get('author_name', '').strip() or 'Anonymous Student'
    content = data.get('content', '').strip()
    parent_id = data.get('parent_id')
    
    if not content:
        return jsonify({'success': False, 'error': 'Comment content cannot be empty!'}), 400
        
    post = DiscussionPost(
        paper_id=paper_id,
        author_name=author_name,
        content=content,
        parent_id=parent_id
    )
    db.session.add(post)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': 'Comment posted successfully!',
        'post': post.to_dict()
    })


# ─────────────────────────────────────────────────────────────────────────────
#  Question Details & AI Answer Generation
# ─────────────────────────────────────────────────────────────────────────────

@main_bp.route('/question/<int:question_id>')
def question_detail(question_id):
    if not session.get('student_logged_in'):
        flash('Please log in to access this page.', 'warning')
        return redirect('/student/login?force_student=true')
    from models import Question
    question = Question.query.get_or_404(question_id)
    return render_template('question_detail.html', question=question)


@main_bp.route('/api/question/<int:question_id>/generate-answer', methods=['POST'])
@csrf.exempt
def generate_question_answer(question_id):
    if not session.get('student_logged_in'):
        return jsonify({'success': False, 'error': 'Unauthorized. Please log in.'}), 401
    from models import Question
    question = Question.query.get_or_404(question_id)

    # ── AI Cache retrieval ──────────────────────────────────────────────────────
    if question.ai_answer:
        return jsonify({
            'success': True,
            'answer': question.ai_answer,
            'notice': 'Loaded from cached AI response.'
        })

    api_key = get_gemini_api_key()
    if not api_key:
        return jsonify({
            'success': False, 
            'error': 'Gemini API key is not configured. Please set the GEMINI_API_KEY environment variable in your .env file.'
        }), 400

    try:
        from google import genai
    except ImportError:
        return jsonify({
            'success': False,
            'error': 'google-genai package is not installed. Please add it to requirements.txt.'
        }), 500

    try:
        client = genai.Client(api_key=api_key)
        
        prompt = f"""You are an experienced university professor.

Generate a high-quality university exam answer for the following question.

Requirements:
* Use simple and clear English.
* Give a proper definition.
* Explain the concept in detail.
* Use headings and bullet points.
* Include examples where appropriate.
* Mention advantages and disadvantages if applicable.
* Mention applications if applicable.
* End with a short conclusion.
* If the question requires a diagram, include a simple ASCII diagram.
* If it is a 2-mark question, give a short answer.
* If it is a 5-mark question, give a medium-length answer.
* If it is a 10 or 16-mark question, give a detailed answer.

Return only the answer without any extra text.

Question: {question.text}
Marks: {question.marks}
"""
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=prompt
        )
        answer_text = response.text
        
        # Save to database cache
        question.ai_answer = answer_text
        db.session.commit()
        
        return jsonify({
            'success': True,
            'answer': answer_text
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Gemini API Error: {str(e)}. Please make sure your GEMINI_API_KEY is valid.'
        }), 500


# ─────────────────────────────────────────────────────────────────────────────
#  Shared Study Resources / Supplementary Materials
# ─────────────────────────────────────────────────────────────────────────────

@main_bp.route('/api/papers/<int:paper_id>/resources', methods=['GET'])
def get_study_resources(paper_id):
    if not session.get('student_logged_in'):
        return jsonify({'success': False, 'error': 'Unauthorized. Please log in.'}), 401
    from models import StudyResource
    resources = StudyResource.query.filter_by(paper_id=paper_id).order_by(StudyResource.uploaded_at.desc()).all()
    return jsonify([r.to_dict() for r in resources])


@main_bp.route('/api/papers/<int:paper_id>/resources', methods=['POST'])
@csrf.exempt
def upload_study_resource(paper_id):
    if not session.get('student_logged_in'):
        return jsonify({'success': False, 'error': 'Unauthorized. Please log in.'}), 401
    from models import StudyResource

    from werkzeug.utils import secure_filename
    import uuid
    
    title = request.form.get('title')
    if not title:
        return jsonify({'success': False, 'error': 'Title is required'}), 400
        
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file part'}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No selected file'}), 400
        
    # Get current student if logged in, otherwise anonymous
    student_id = session.get('student_id')
    
    filename = secure_filename(file.filename)
    unique_filename = f"{uuid.uuid4().hex}_{filename}"
    
    resources_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'resources')
    os.makedirs(resources_dir, exist_ok=True)
    
    file_path = os.path.join(resources_dir, unique_filename)
    file.save(file_path)
    
    resource = StudyResource(
        paper_id=paper_id,
        student_id=student_id,
        title=title,
        filename=unique_filename
    )
    
    db.session.add(resource)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': 'Resource uploaded successfully!',
        'resource': resource.to_dict()
    })


@main_bp.route('/api/resources/delete/<int:resource_id>', methods=['POST'])
@csrf.exempt
def delete_study_resource(resource_id):
    from models import StudyResource
    student_id = session.get('student_id')
    if not student_id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
    resource = StudyResource.query.get_or_404(resource_id)
    if resource.student_id != student_id:
        return jsonify({'success': False, 'error': 'You are not authorized to delete this resource'}), 403
        
    try:
        resources_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'resources')
        file_path = os.path.join(resources_dir, resource.filename)
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as ex:
        print(f"Error deleting physical file: {ex}")
        
    db.session.delete(resource)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Resource deleted successfully!'})



