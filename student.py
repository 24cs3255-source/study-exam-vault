from flask import (
    Blueprint, render_template, request, session,
    redirect, url_for, flash, jsonify, current_app
)
from models import (
    Student, StudentBookmark, StudentActivity, StudentBadge,
    Badge, QuestionPaper, StudyPlan, StudyTask, Annotation,
    MockAttempt, GroupMessage
)
from extensions import db, csrf
from functools import wraps
from datetime import datetime, date, timedelta
import json
import os
import uuid
from werkzeug.utils import secure_filename

student_bp = Blueprint('student', __name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Decorator: require student login
# ─────────────────────────────────────────────────────────────────────────────

def student_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('student_logged_in'):
            flash('Please log in to access this page.', 'warning')
            return redirect('/student/login?force_student=true')
        return f(*args, **kwargs)
    return decorated


def get_current_student():
    sid = session.get('student_id')
    if sid:
        return Student.query.get(sid)
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Points & Badge Helper
# ─────────────────────────────────────────────────────────────────────────────

def award_points(student_id, points, activity_type, detail):
    """Award points to a student and log the activity."""
    student = Student.query.get(student_id)
    if not student:
        return

    student.points += points
    activity = StudentActivity(
        student_id=student_id,
        activity_type=activity_type,
        detail=detail,
        points_earned=points,
    )
    db.session.add(activity)

    # Check badge thresholds
    check_badges(student)
    db.session.commit()


def check_badges(student):
    """Check if student qualifies for any new badges."""
    earned_ids = {sb.badge_id for sb in student.badges.all()}
    all_badges = Badge.query.all()

    for badge in all_badges:
        if badge.id in earned_ids:
            continue

        earned = False
        if badge.category == 'general':
            # "First Steps" is awarded on registration, skip here
            if badge.name == 'Achiever' and student.points >= 100:
                earned = True
            elif badge.name == 'Legend' and student.points >= 500:
                earned = True
        elif badge.category == 'contribution':
            count = QuestionPaper.query.filter_by(status='approved').count()
            if count >= badge.threshold:
                earned = True
        elif badge.category == 'discussion':
            from models import DiscussionPost
            count = DiscussionPost.query.filter_by(author_name=student.display_name or student.username).count()
            if count >= badge.threshold:
                earned = True
        elif badge.category == 'study':
            count = student.bookmarks.count()
            if count >= badge.threshold:
                earned = True

        if earned:
            sb = StudentBadge(student_id=student.id, badge_id=badge.id)
            db.session.add(sb)
            act = StudentActivity(
                student_id=student.id,
                activity_type='badge',
                detail=f'Earned badge: {badge.icon} {badge.name}',
                points_earned=0,
            )
            db.session.add(act)


# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 2 — Student Dashboard
# ═════════════════════════════════════════════════════════════════════════════

@student_bp.route('/student/dashboard')
@student_required
def dashboard():
    student = get_current_student()
    if not student:
        return redirect('/student/login')

    # Stats
    bookmark_count = student.bookmarks.count()
    from models import DiscussionPost
    discussion_count = DiscussionPost.query.filter_by(
        author_name=student.display_name or student.username
    ).count()

    contribution_count = QuestionPaper.query.filter_by(status='pending').count() + \
                         QuestionPaper.query.filter_by(status='approved').count()

    # Badges
    earned_badges = [sb.to_dict() for sb in student.badges.all()]
    all_badges = [b.to_dict() for b in Badge.query.all()]

    # Recent activity
    activities = [a.to_dict() for a in
                  student.activities.order_by(StudentActivity.created_at.desc()).limit(15).all()]

    # Bookmarked papers
    bm_paper_ids = [b.paper_id for b in student.bookmarks.all()]
    saved_papers = []
    if bm_paper_ids:
        saved_papers = [p.to_dict() for p in
                        QuestionPaper.query.filter(QuestionPaper.id.in_(bm_paper_ids)).all()]

    # Mock Attempts
    mock_attempts = [m.to_dict() for m in
                     student.mock_attempts.order_by(MockAttempt.created_at.desc()).all()]

    # Downloaded papers
    downloaded_papers = [d.paper.to_dict() for d in student.downloads.all() if d.paper]

    return render_template(
        'student_dashboard.html',
        student=student,
        bookmark_count=bookmark_count,
        discussion_count=discussion_count,
        contribution_count=contribution_count,
        earned_badges=earned_badges,
        all_badges=all_badges,
        activities=activities,
        saved_papers=saved_papers,
        mock_attempts=mock_attempts,
        downloaded_papers=downloaded_papers,
    )


@student_bp.route('/api/student/downloads/delete/<int:paper_id>', methods=['DELETE', 'POST'])
@csrf.exempt
@student_required
def delete_download_record(paper_id):
    student = get_current_student()
    if not student:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    from models import StudentDownload
    dl = StudentDownload.query.filter_by(student_id=student.id, paper_id=paper_id).first()
    if dl:
        db.session.delete(dl)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Download record deleted successfully.'})
    return jsonify({'success': False, 'error': 'Download record not found'}), 404


@student_bp.route('/api/student/mock/delete/<int:attempt_id>', methods=['DELETE', 'POST'])
@csrf.exempt
@student_required
def delete_mock_attempt(attempt_id):
    student = get_current_student()
    if not student:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    attempt = MockAttempt.query.get_or_404(attempt_id)
    if attempt.student_id != student.id:
        return jsonify({'success': False, 'error': 'Access denied'}), 403

    db.session.delete(attempt)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Mock exam practice history deleted successfully.'})


@student_bp.route('/api/student/bookmarks/sync', methods=['POST'])
@csrf.exempt
@student_required
def sync_bookmarks():
    """Sync bookmarks from localStorage to server."""
    student = get_current_student()
    if not student:
        return jsonify({'success': False}), 401

    data = request.get_json() or {}
    paper_ids = data.get('ids', [])

    # Get existing server bookmarks
    existing = {b.paper_id for b in student.bookmarks.all()}

    added = 0
    for pid in paper_ids:
        try:
            pid = int(pid)
        except (ValueError, TypeError):
            continue
        if pid not in existing:
            bm = StudentBookmark(student_id=student.id, paper_id=pid)
            db.session.add(bm)
            added += 1

    if added:
        db.session.commit()

    # Return the current server bookmark list
    current = [b.paper_id for b in student.bookmarks.all()]
    return jsonify({'success': True, 'synced': added, 'bookmarks': current})


@student_bp.route('/api/student/bookmarks/toggle', methods=['POST'])
@csrf.exempt
@student_required
def toggle_bookmark():
    student = get_current_student()
    if not student:
        return jsonify({'success': False}), 401

    data = request.get_json() or {}
    paper_id = data.get('paper_id')
    if not paper_id:
        return jsonify({'success': False, 'error': 'Missing paper_id'}), 400

    existing = StudentBookmark.query.filter_by(
        student_id=student.id, paper_id=int(paper_id)
    ).first()

    if existing:
        db.session.delete(existing)
        db.session.commit()
        return jsonify({'success': True, 'action': 'removed'})
    else:
        bm = StudentBookmark(student_id=student.id, paper_id=int(paper_id))
        db.session.add(bm)
        award_points(student.id, 1, 'bookmark', f'Saved a question paper')
        return jsonify({'success': True, 'action': 'added'})


# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 3 — Leaderboard & Badges
# ═════════════════════════════════════════════════════════════════════════════





# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 4 — Personalized Study Planner
# ═════════════════════════════════════════════════════════════════════════════

@student_bp.route('/student/planner')
@student_required
def planner():
    student = get_current_student()
    if not student:
        return redirect('/student/login')

    plans = [p.to_dict() for p in
             student.study_plans.order_by(StudyPlan.created_at.desc()).all()]

    # Get student's bookmarked papers for the plan creation form
    bm_ids = [b.paper_id for b in student.bookmarks.all()]
    saved_papers = []
    if bm_ids:
        saved_papers = [p.to_dict() for p in
                        QuestionPaper.query.filter(QuestionPaper.id.in_(bm_ids)).all()]

    return render_template(
        'student_planner.html',
        student=student,
        plans=plans,
        saved_papers=saved_papers,
    )


@student_bp.route('/api/student/planner/create', methods=['POST'])
@csrf.exempt
@student_required
def create_plan():
    student = get_current_student()
    if not student:
        return jsonify({'success': False}), 401

    data = request.get_json() or {}
    title = data.get('title', '').strip()
    exam_date_str = data.get('exam_date', '')
    tasks_data = data.get('tasks', [])

    if not title:
        return jsonify({'success': False, 'error': 'Title is required'}), 400

    exam_date = None
    if exam_date_str:
        try:
            exam_date = datetime.strptime(exam_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    plan = StudyPlan(
        student_id=student.id,
        title=title,
        exam_date=exam_date,
    )
    db.session.add(plan)
    db.session.flush()

    for t in tasks_data:
        task = StudyTask(
            plan_id=plan.id,
            title=t.get('title', 'Study Task'),
            description=t.get('description', ''),
            paper_id=t.get('paper_id'),
            scheduled_date=datetime.strptime(t['scheduled_date'], '%Y-%m-%d').date() if t.get('scheduled_date') else None,
            priority=t.get('priority', 'medium'),
        )
        db.session.add(task)

    award_points(student.id, 10, 'planner', f'Created study plan: {title}')

    return jsonify({'success': True, 'plan': plan.to_dict()})


@student_bp.route('/api/student/planner/task/toggle', methods=['POST'])
@csrf.exempt
@student_required
def toggle_task():
    student = get_current_student()
    if not student:
        return jsonify({'success': False}), 401

    data = request.get_json() or {}
    task_id = data.get('task_id')
    if not task_id:
        return jsonify({'success': False, 'error': 'Missing task_id'}), 400

    task = StudyTask.query.get(task_id)
    if not task or task.plan.student_id != student.id:
        return jsonify({'success': False, 'error': 'Task not found'}), 404

    task.is_completed = not task.is_completed
    db.session.commit()

    return jsonify({'success': True, 'is_completed': task.is_completed})


@student_bp.route('/api/student/planner/<int:plan_id>', methods=['DELETE', 'POST'])
@csrf.exempt
@student_required
def delete_plan(plan_id):
    student = get_current_student()
    if not student:
        return jsonify({'success': False}), 401

    plan = StudyPlan.query.get(plan_id)
    if not plan or plan.student_id != student.id:
        return jsonify({'success': False, 'error': 'Plan not found'}), 404

    db.session.delete(plan)
    db.session.commit()
    return jsonify({'success': True})


# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 5 — Collaborative Annotations
# ═════════════════════════════════════════════════════════════════════════════

@student_bp.route('/api/papers/<int:paper_id>/annotations')
def get_annotations(paper_id):
    QuestionPaper.query.get_or_404(paper_id)
    annotations = Annotation.query.filter_by(paper_id=paper_id)\
        .order_by(Annotation.created_at.asc()).all()
    return jsonify([a.to_dict() for a in annotations])


@student_bp.route('/api/papers/<int:paper_id>/annotations', methods=['POST'])
@csrf.exempt
@student_required
def create_annotation(paper_id):
    QuestionPaper.query.get_or_404(paper_id)
    student = get_current_student()
    if not student:
        return jsonify({'success': False}), 401

    data = request.get_json() or {}
    content = data.get('content', '').strip()
    page_number = data.get('page_number', 1)
    x_percent = data.get('x_percent', 50.0)
    y_percent = data.get('y_percent', 50.0)
    color = data.get('color', '#ffcc00')
    ann_type = data.get('ann_type', 'note')

    if not content:
        return jsonify({'success': False, 'error': 'Content is required'}), 400

    annotation = Annotation(
        paper_id=paper_id,
        student_id=student.id,
        page_number=page_number,
        x_percent=x_percent,
        y_percent=y_percent,
        content=content,
        color=color,
        ann_type=ann_type,
    )
    db.session.add(annotation)
    award_points(student.id, 2, 'annotation', f'Added annotation on paper #{paper_id}')

    return jsonify({'success': True, 'annotation': annotation.to_dict()})


@student_bp.route('/api/annotations/<int:ann_id>', methods=['DELETE', 'POST'])
@csrf.exempt
@student_required
def delete_annotation(ann_id):
    student = get_current_student()
    if not student:
        return jsonify({'success': False}), 401

    annotation = Annotation.query.get(ann_id)
    if not annotation or annotation.student_id != student.id:
        return jsonify({'success': False, 'error': 'Not found or unauthorized'}), 404

    db.session.delete(annotation)
    db.session.commit()
    return jsonify({'success': True})


# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 6 — Mock Exam Simulator
# ═════════════════════════════════════════════════════════════════════════════

@student_bp.route('/student/mock/<int:paper_id>', methods=['GET'])
@student_required
def mock_exam(paper_id):
    student = get_current_student()
    if not student:
        return redirect('/student/login')

    paper = QuestionPaper.query.get_or_404(paper_id)

    # Find existing incomplete attempt or create a new one
    attempt = MockAttempt.query.filter_by(
        student_id=student.id,
        paper_id=paper_id,
        is_submitted=False
    ).first()

    if not attempt:
        attempt = MockAttempt(
            student_id=student.id,
            paper_id=paper_id,
            notes="",
            time_spent=0
        )
        db.session.add(attempt)
        db.session.commit()

    return render_template(
        'student_mock_exam.html',
        attempt=attempt,
        paper=paper,
        student=student
    )


@student_bp.route('/student/mock/save/<int:attempt_id>', methods=['POST'])
@csrf.exempt
@student_required
def mock_save(attempt_id):
    student = get_current_student()
    if not student:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    attempt = MockAttempt.query.get_or_404(attempt_id)
    if attempt.student_id != student.id or attempt.is_submitted:
        return jsonify({'success': False, 'error': 'Forbidden or submitted'}), 403

    data = request.get_json() or {}
    attempt.notes = data.get('notes', attempt.notes)
    attempt.time_spent = int(data.get('time_spent', attempt.time_spent))
    
    db.session.commit()
    return jsonify({'success': True, 'saved_at': datetime.utcnow().strftime('%H:%M:%S')})


@student_bp.route('/student/mock/submit/<int:attempt_id>', methods=['POST'])
@csrf.exempt
@student_required
def mock_submit(attempt_id):
    student = get_current_student()
    if not student:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    attempt = MockAttempt.query.get_or_404(attempt_id)
    if attempt.student_id != student.id or attempt.is_submitted:
        return jsonify({'success': False, 'error': 'Forbidden or submitted'}), 403

    data = request.get_json() or {}
    attempt.notes = data.get('notes', attempt.notes)
    attempt.time_spent = int(data.get('time_spent', attempt.time_spent))
    attempt.is_submitted = True
    attempt.submitted_at = datetime.utcnow()

    # Award points: +20 pts
    award_points(student.id, 20, 'mock', f'Completed mock exam practice for subject code: {attempt.paper.subject_code}')
    
    db.session.commit()
    return jsonify({
        'success': True,
        'redirect_url': url_for('student.mock_result', attempt_id=attempt.id)
    })


@student_bp.route('/student/mock/result/<int:attempt_id>', methods=['GET'])
@student_required
def mock_result(attempt_id):
    student = get_current_student()
    if not student:
        return redirect('/student/login')

    attempt = MockAttempt.query.get_or_404(attempt_id)
    if attempt.student_id != student.id:
        return redirect('/student/dashboard')

    return render_template(
        'student_mock_result.html',
        attempt=attempt,
        paper=attempt.paper,
        student=student
    )


# ═════════════════════════════════════════════════════════════════════════════
#  PHASE 7 — Collaborative Study Groups
# ═════════════════════════════════════════════════════════════════════════════

@student_bp.route('/student/groups', methods=['GET'])
@student_required
def study_groups():
    student = get_current_student()
    if not student:
        return redirect('/student/login')

    # Default/backwards-compatibility if student has no department set
    if not student.department:
        student.department = 'CS'
        db.session.commit()

    dept = student.department.upper()
    departments = ['CS', 'BCA', 'BA', 'BOTANY', 'PHYSICS', 'CHEMISTRY', 'MATHEMATICS', 'ENGLISH', 'COMMERCE', 'ECONOMICS']

    return render_template(
        'student_groups.html',
        student=student,
        current_dept=dept,
        departments=departments
    )


@student_bp.route('/api/groups/<dept>/messages', methods=['GET'])
@student_required
def get_group_messages(dept):
    student = get_current_student()
    if not student:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    if not student.department:
        student.department = 'CS'
        db.session.commit()

    if dept.upper() != student.department.upper():
        return jsonify({'success': False, 'error': 'Access denied. You can only view your registered department channel.'}), 403

    dept = dept.upper()
    # Fetch latest 50 messages
    messages = GroupMessage.query.filter_by(department=dept)\
        .order_by(GroupMessage.created_at.desc()).limit(50).all()
    
    # Return in chronological order
    messages.reverse()
    return jsonify([m.to_dict() for m in messages])


@student_bp.route('/api/groups/<dept>/messages', methods=['POST'])
@csrf.exempt
@student_required
def post_group_message(dept):
    student = get_current_student()
    if not student:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    if not student.department:
        student.department = 'CS'
        db.session.commit()

    if dept.upper() != student.department.upper():
        return jsonify({'success': False, 'error': 'Access denied. You can only post to your registered department channel.'}), 403

    data = request.get_json() or {}
    content = data.get('content', '').strip()
    parent_id = data.get('parent_id')

    if not content:
        return jsonify({'success': False, 'error': 'Message cannot be empty'}), 400

    msg = GroupMessage(
        department=dept.upper(),
        student_id=student.id,
        content=content,
        parent_id=parent_id
    )
    db.session.add(msg)
    db.session.commit()
    
    return jsonify({'success': True, 'message': msg.to_dict()})


@student_bp.route('/api/groups/messages/<int:message_id>/react', methods=['POST'])
@csrf.exempt
@student_required
def react_group_message(message_id):
    student = get_current_student()
    if not student:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    msg = GroupMessage.query.get_or_404(message_id)
    data = request.get_json() or {}
    emoji = data.get('emoji', '').strip()
    if not emoji:
        return jsonify({'success': False, 'error': 'Emoji required'}), 400

    import json
    try:
        reactions = json.loads(msg.reactions) if msg.reactions else {}
    except Exception:
        reactions = {}

    if emoji not in reactions:
        reactions[emoji] = []

    if student.id in reactions[emoji]:
        reactions[emoji].remove(student.id)
    else:
        reactions[emoji].append(student.id)

    msg.reactions = json.dumps(reactions)
    db.session.commit()

    return jsonify({'success': True, 'reactions': reactions})


@student_bp.route('/api/groups/upload', methods=['POST'])
@csrf.exempt
@student_required
def upload_group_attachment():
    student = get_current_student()
    if not student:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file part'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No selected file'}), 400

    group_uploads_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'groups')
    os.makedirs(group_uploads_dir, exist_ok=True)

    filename = secure_filename(file.filename)
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    save_path = os.path.join(group_uploads_dir, unique_name)
    file.save(save_path)

    file_url = f"/static/uploads/groups/{unique_name}"
    
    return jsonify({
        'success': True,
        'file_url': file_url,
        'file_name': filename,
        'file_type': file.content_type or filename.rsplit('.', 1)[-1].lower()
    })


@student_bp.route('/api/groups/messages/<int:message_id>', methods=['DELETE', 'POST'])
@csrf.exempt
@student_required
def delete_group_message(message_id):
    student = get_current_student()
    if not student:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    msg = GroupMessage.query.get_or_404(message_id)

    if msg.student_id != student.id:
        return jsonify({'success': False, 'error': 'Access denied. You can only delete your own messages.'}), 403

    if msg.content.strip().startswith('{'):
        try:
            payload = json.loads(msg.content)
            file_url = payload.get('file_url')
            if file_url and file_url.startswith('/static/uploads/groups/'):
                filename = file_url.split('/')[-1]
                group_uploads_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'groups')
                file_path = os.path.join(group_uploads_dir, filename)
                if os.path.exists(file_path):
                    os.remove(file_path)
        except Exception:
            pass

    db.session.delete(msg)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Message deleted successfully.'})


# ─────────────────────────────────────────────────────────────────────────────
#  Mock Exam Answer Sheet PDF Download
# ─────────────────────────────────────────────────────────────────────────────

@student_bp.route('/student/mock/download-pdf/<int:attempt_id>', methods=['GET'])
@student_required
def download_mock_pdf(attempt_id):
    student = get_current_student()
    if not student:
        return redirect('/student/login')

    attempt = MockAttempt.query.get_or_404(attempt_id)
    if attempt.student_id != student.id:
        return redirect('/student/dashboard')

    # Generate PDF in memory using ReportLab
    import io
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors

    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        pdf_buffer, 
        pagesize=letter,
        rightMargin=54, leftMargin=54,
        topMargin=54, bottomMargin=54
    )

    styles = getSampleStyleSheet()

    # Custom styles matching our PDF generation design
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Title'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#4f46e5'),
        alignment=1,
        spaceAfter=5
    )
    subtitle_style = ParagraphStyle(
        'DocSubTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=15,
        textColor=colors.HexColor('#64748b'),
        alignment=1,
        spaceAfter=20
    )
    section_heading = ParagraphStyle(
        'SectionHeading',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=17,
        textColor=colors.HexColor('#0f172a'),
        spaceBefore=15,
        spaceAfter=10,
        keepWithNext=True
    )
    body_style = ParagraphStyle(
        'Body',
        parent=styles['BodyText'],
        fontName='Helvetica',
        fontSize=10,
        leading=15,
        textColor=colors.HexColor('#334155'),
        spaceAfter=10
    )
    meta_label_style = ParagraphStyle(
        'MetaLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=11,
        textColor=colors.HexColor('#0f172a')
    )
    meta_val_style = ParagraphStyle(
        'MetaVal',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=11,
        textColor=colors.HexColor('#334155')
    )

    story = []

    # Title & Subtitle
    story.append(Paragraph("EXAM-STUDY-VAULT — MOCK EXAM ANSWER SHEET", title_style))
    story.append(Paragraph(f"Subject: {attempt.paper.subject_name} ({attempt.paper.subject_code})", subtitle_style))
    story.append(Spacer(1, 10))

    # Time calculation
    duration_minutes = attempt.time_spent // 60
    duration_seconds = attempt.time_spent % 60
    duration_str = f"{duration_minutes}m {duration_seconds}s" if duration_minutes > 0 else f"{duration_seconds}s"
    if attempt.time_spent >= 3600:
        duration_str = f"{attempt.time_spent // 3600}h {duration_minutes % 60}m"

    # Info Table
    metadata_data = [
        [Paragraph("Student Name:", meta_label_style), Paragraph(student.display_name or student.username, meta_val_style),
         Paragraph("Department:", meta_label_style), Paragraph(attempt.paper.department, meta_val_style)],
        [Paragraph("Semester:", meta_label_style), Paragraph(attempt.paper.semester, meta_val_style),
         Paragraph("Exam Year:", meta_label_style), Paragraph(str(attempt.paper.year), meta_val_style)],
        [Paragraph("Time Spent:", meta_label_style), Paragraph(duration_str, meta_val_style),
         Paragraph("Submission Date:", meta_label_style), Paragraph(attempt.submitted_at.strftime('%d %b %Y %H:%M') if attempt.submitted_at else 'N/A', meta_val_style)]
    ]

    t = Table(metadata_data, colWidths=[90, 160, 90, 160])
    t.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#f8fafc')),
        ('BACKGROUND', (2,0), (2,-1), colors.HexColor('#f8fafc')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
    ]))
    
    story.append(t)
    story.append(Spacer(1, 15))

    # Answers Content
    story.append(Paragraph("STUDENT WRITTEN ANSWERS / DRAFT", section_heading))
    
    notes_content = attempt.notes or "No answers were written in the notepad during this session."
    paragraphs = notes_content.split('\n')
    for p_text in paragraphs:
        p_text_clean = p_text.strip()
        if p_text_clean:
            story.append(Paragraph(p_text_clean, body_style))
        else:
            story.append(Spacer(1, 5))

    doc.build(story)

    pdf_buffer.seek(0)
    
    from flask import send_file
    safe_subject = "".join(x for x in attempt.paper.subject_name if x.isalnum() or x in " -_").strip()
    pdf_filename = f"MockAnswer_{attempt.paper.subject_code}_{safe_subject}.pdf"
    
    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=pdf_filename
    )


