from extensions import db
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash


# ─────────────────────────────────────────────────────────────────────────────
#  AdminUser  –  single admin account, credentials stored securely in the DB
# ─────────────────────────────────────────────────────────────────────────────

class AdminUser(db.Model):
    __tablename__ = 'admin_users'

    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role          = db.Column(db.String(20), default='admin')
    department    = db.Column(db.String(50), nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def set_password(self, plain_text_password: str) -> None:
        """Hash *plain_text_password* with PBKDF2-SHA256 and store the digest."""
        self.password_hash = generate_password_hash(plain_text_password)

    def check_password(self, plain_text_password: str) -> bool:
        """Return True if *plain_text_password* matches the stored hash."""
        return check_password_hash(self.password_hash, plain_text_password)

    def __repr__(self):
        return f'<AdminUser {self.username}>'


# ─────────────────────────────────────────────────────────────────────────────
#  AdminNotification  –  holds messages for new public paper contributions
# ─────────────────────────────────────────────────────────────────────────────

class AdminNotification(db.Model):
    __tablename__ = 'admin_notifications'

    id         = db.Column(db.Integer, primary_key=True)
    message    = db.Column(db.String(500), nullable=False)
    is_read    = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':         self.id,
            'message':    self.message,
            'is_read':    self.is_read,
            'created_at': self.created_at.strftime('%d %b %Y %H:%M')
        }


# ─────────────────────────────────────────────────────────────────────────────
#  QuestionPaper  –  now includes class_name (e.g. B.Sc, B.Com, B.A)
# ─────────────────────────────────────────────────────────────────────────────

class QuestionPaper(db.Model):
    __tablename__ = 'question_papers'

    id            = db.Column(db.Integer, primary_key=True)

    # ── Academic metadata ──────────────────────────────────────────────────────
    department    = db.Column(db.String(100), nullable=False, index=True)
    semester      = db.Column(db.String(20),  nullable=False, index=True)
    subject_code  = db.Column(db.String(30),  nullable=False, index=True)
    subject_name  = db.Column(db.String(200), nullable=False)
    exam_type     = db.Column(db.String(50),  nullable=False, index=True)
    year          = db.Column(db.Integer,     nullable=False, index=True)

    # ── NEW: Class / Programme ─────────────────────────────────────────────────
    # e.g. "B.Sc", "B.Com", "B.A", "B.E", "MBA" …
    # nullable=True so existing rows without this field don't break.
    class_name    = db.Column(db.String(50),  nullable=True,  index=True)
    status        = db.Column(db.String(30),  nullable=True,  default='approved', index=True)

    # ── File metadata ──────────────────────────────────────────────────────────
    filename      = db.Column(db.String(300), nullable=False)
    original_name = db.Column(db.String(300), nullable=True)
    download_count= db.Column(db.Integer,     default=0, nullable=True)

    # ── Timestamps ─────────────────────────────────────────────────────────────
    uploaded_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':           self.id,
            'department':   self.department,
            'semester':     self.semester,
            'subject_code': self.subject_code,
            'subject_name': self.subject_name,
            'exam_type':    self.exam_type,
            'year':         self.year,
            'status':       self.status or 'approved',
            # class_name falls back to empty string so JS templates stay simple
            'class_name':   self.class_name or '',
            'filename':     self.filename,
            'original_name':self.original_name,
            'download_count':self.download_count or 0,
            'uploaded_at':  self.uploaded_at.strftime('%d %b %Y'),
        }

    def __repr__(self):
        return f'<QuestionPaper {self.subject_code} {self.exam_type} {self.year}>'


# ─────────────────────────────────────────────────────────────────────────────
#  DiscussionPost  –  public comments and nested replies under each paper
# ─────────────────────────────────────────────────────────────────────────────

class DiscussionPost(db.Model):
    __tablename__ = 'discussion_posts'

    id          = db.Column(db.Integer, primary_key=True)
    paper_id    = db.Column(db.Integer, db.ForeignKey('question_papers.id', ondelete='CASCADE'), nullable=False, index=True)
    author_name = db.Column(db.String(100), nullable=False, default='Anonymous Student')
    content     = db.Column(db.Text, nullable=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    parent_id   = db.Column(db.Integer, db.ForeignKey('discussion_posts.id', ondelete='CASCADE'), nullable=True, index=True)

    # Self-referential relationship for nested replies
    replies     = db.relationship('DiscussionPost', 
                                  backref=db.backref('parent', remote_side=[id]),
                                  lazy='dynamic',
                                  cascade='all, delete-orphan')

    # Relationship to paper
    paper       = db.relationship('QuestionPaper', backref=db.backref('discussions', lazy='dynamic', cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            'id':          self.id,
            'paper_id':    self.paper_id,
            'author_name': self.author_name,
            'content':     self.content,
            'created_at':  self.created_at.strftime('%d %b %Y %H:%M'),
            'parent_id':   self.parent_id
        }


# ─────────────────────────────────────────────────────────────────────────────
#  Student  –  student accounts for profile, dashboard, leaderboard
# ─────────────────────────────────────────────────────────────────────────────

class Student(db.Model):
    __tablename__ = 'students'

    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80),  unique=True, nullable=False, index=True)
    email         = db.Column(db.String(200), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    display_name  = db.Column(db.String(100), nullable=True)
    avatar_color  = db.Column(db.String(7),   default='#007aff')
    points        = db.Column(db.Integer,     default=0)
    department    = db.Column(db.String(50),  nullable=True)
    created_at    = db.Column(db.DateTime,    default=datetime.utcnow)

    # Relationships
    bookmarks   = db.relationship('StudentBookmark', backref='student', lazy='dynamic', cascade='all, delete-orphan')
    downloads   = db.relationship('StudentDownload', backref='student', lazy='dynamic', cascade='all, delete-orphan')
    activities  = db.relationship('StudentActivity', backref='student', lazy='dynamic', cascade='all, delete-orphan')
    badges      = db.relationship('StudentBadge', backref='student', lazy='dynamic', cascade='all, delete-orphan')
    study_plans = db.relationship('StudyPlan', backref='student', lazy='dynamic', cascade='all, delete-orphan')
    annotations = db.relationship('Annotation', backref='student', lazy='dynamic', cascade='all, delete-orphan')
    mock_attempts = db.relationship('MockAttempt', backref='student', lazy='dynamic', cascade='all, delete-orphan')

    def set_password(self, plain_text_password: str) -> None:
        self.password_hash = generate_password_hash(plain_text_password)

    def check_password(self, plain_text_password: str) -> bool:
        return check_password_hash(self.password_hash, plain_text_password)

    def get_initials(self):
        name = self.display_name or self.username
        parts = name.strip().split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return name[:2].upper()

    def to_dict(self):
        return {
            'id':           self.id,
            'username':     self.username,
            'display_name': self.display_name or self.username,
            'avatar_color': self.avatar_color,
            'points':       self.points,
            'department':   self.department,
            'initials':     self.get_initials(),
            'created_at':   self.created_at.strftime('%d %b %Y'),
        }

    def __repr__(self):
        return f'<Student {self.username}>'


# ─────────────────────────────────────────────────────────────────────────────
#  StudentBookmark  –  server-synced saved papers
# ─────────────────────────────────────────────────────────────────────────────

class StudentBookmark(db.Model):
    __tablename__ = 'student_bookmarks'

    id         = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    paper_id   = db.Column(db.Integer, db.ForeignKey('question_papers.id', ondelete='CASCADE'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('student_id', 'paper_id', name='uq_student_paper_bookmark'),)


# ─────────────────────────────────────────────────────────────────────────────
#  StudentDownload  –  student downloaded question paper logs
# ─────────────────────────────────────────────────────────────────────────────

class StudentDownload(db.Model):
    __tablename__ = 'student_downloads'

    id            = db.Column(db.Integer, primary_key=True)
    student_id    = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    paper_id      = db.Column(db.Integer, db.ForeignKey('question_papers.id', ondelete='CASCADE'), nullable=False, index=True)
    downloaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    paper         = db.relationship('QuestionPaper')

    __table_args__ = (db.UniqueConstraint('student_id', 'paper_id', name='uq_student_paper_download'),)


# ─────────────────────────────────────────────────────────────────────────────
#  StudentActivity  –  activity timeline for dashboard
# ─────────────────────────────────────────────────────────────────────────────

class StudentActivity(db.Model):
    __tablename__ = 'student_activities'

    id            = db.Column(db.Integer, primary_key=True)
    student_id    = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    activity_type = db.Column(db.String(50),  nullable=False)   # 'bookmark', 'discussion', 'contribution', 'badge', 'planner', 'annotation'
    detail        = db.Column(db.String(300), nullable=True)
    points_earned = db.Column(db.Integer,     default=0)
    created_at    = db.Column(db.DateTime,    default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':            self.id,
            'activity_type': self.activity_type,
            'detail':        self.detail,
            'points_earned': self.points_earned,
            'created_at':    self.created_at.strftime('%d %b %Y %H:%M'),
        }


# ─────────────────────────────────────────────────────────────────────────────
#  Badge & StudentBadge  –  achievements / gamification
# ─────────────────────────────────────────────────────────────────────────────

class Badge(db.Model):
    __tablename__ = 'badges'

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(300), nullable=False)
    icon        = db.Column(db.String(10),  nullable=False)    # emoji
    threshold   = db.Column(db.Integer,     default=0)
    category    = db.Column(db.String(50),  nullable=False)    # 'general', 'contribution', 'discussion', 'study', 'streak'

    def to_dict(self):
        return {
            'id':          self.id,
            'name':        self.name,
            'description': self.description,
            'icon':        self.icon,
            'threshold':   self.threshold,
            'category':    self.category,
        }


class StudentBadge(db.Model):
    __tablename__ = 'student_badges'

    id         = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    badge_id   = db.Column(db.Integer, db.ForeignKey('badges.id', ondelete='CASCADE'), nullable=False, index=True)
    earned_at  = db.Column(db.DateTime, default=datetime.utcnow)

    badge = db.relationship('Badge')

    __table_args__ = (db.UniqueConstraint('student_id', 'badge_id', name='uq_student_badge'),)

    def to_dict(self):
        return {
            'badge':     self.badge.to_dict() if self.badge else None,
            'earned_at': self.earned_at.strftime('%d %b %Y'),
        }


# ─────────────────────────────────────────────────────────────────────────────
#  StudyPlan & StudyTask  –  personalized study planner
# ─────────────────────────────────────────────────────────────────────────────

class StudyPlan(db.Model):
    __tablename__ = 'study_plans'

    id         = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    title      = db.Column(db.String(200), nullable=False)
    exam_date  = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    tasks = db.relationship('StudyTask', backref='plan', lazy='dynamic', cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id':         self.id,
            'title':      self.title,
            'exam_date':  self.exam_date.strftime('%Y-%m-%d') if self.exam_date else None,
            'created_at': self.created_at.strftime('%d %b %Y'),
            'tasks':      [t.to_dict() for t in self.tasks.order_by(StudyTask.scheduled_date).all()],
        }


class StudyTask(db.Model):
    __tablename__ = 'study_tasks'

    id             = db.Column(db.Integer, primary_key=True)
    plan_id        = db.Column(db.Integer, db.ForeignKey('study_plans.id', ondelete='CASCADE'), nullable=False, index=True)
    paper_id       = db.Column(db.Integer, db.ForeignKey('question_papers.id'), nullable=True)
    title          = db.Column(db.String(200), nullable=False)
    description    = db.Column(db.String(500), nullable=True)
    scheduled_date = db.Column(db.Date, nullable=True)
    is_completed   = db.Column(db.Boolean, default=False)
    priority       = db.Column(db.String(20), default='medium')  # low, medium, high

    paper = db.relationship('QuestionPaper')

    def to_dict(self):
        return {
            'id':             self.id,
            'title':          self.title,
            'description':    self.description,
            'scheduled_date': self.scheduled_date.strftime('%Y-%m-%d') if self.scheduled_date else None,
            'is_completed':   self.is_completed,
            'priority':       self.priority,
            'paper_id':       self.paper_id,
        }


# ─────────────────────────────────────────────────────────────────────────────
#  Annotation  –  collaborative PDF annotations
# ─────────────────────────────────────────────────────────────────────────────

class Annotation(db.Model):
    __tablename__ = 'annotations'

    id          = db.Column(db.Integer, primary_key=True)
    paper_id    = db.Column(db.Integer, db.ForeignKey('question_papers.id', ondelete='CASCADE'), nullable=False, index=True)
    student_id  = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    page_number = db.Column(db.Integer, nullable=False)
    x_percent   = db.Column(db.Float,   nullable=False)
    y_percent   = db.Column(db.Float,   nullable=False)
    content     = db.Column(db.Text,     nullable=False)
    color       = db.Column(db.String(20), default='#ffcc00')
    ann_type    = db.Column(db.String(20), default='note')     # 'note', 'highlight', 'question'
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    paper = db.relationship('QuestionPaper')

    def to_dict(self):
        return {
            'id':          self.id,
            'paper_id':    self.paper_id,
            'student_id':  self.student_id,
            'student':     self.student.to_dict() if self.student else None,
            'page_number': self.page_number,
            'x_percent':   self.x_percent,
            'y_percent':   self.y_percent,
            'content':     self.content,
            'color':       self.color,
            'ann_type':    self.ann_type,
            'created_at':  self.created_at.strftime('%d %b %Y %H:%M'),
        }


# ─────────────────────────────────────────────────────────────────────────────
#  MockAttempt  –  distraction-free 3-hour focus mock exam attempts
# ─────────────────────────────────────────────────────────────────────────────

class MockAttempt(db.Model):
    __tablename__ = 'mock_attempts'

    id           = db.Column(db.Integer, primary_key=True)
    student_id   = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    paper_id     = db.Column(db.Integer, db.ForeignKey('question_papers.id', ondelete='CASCADE'), nullable=False, index=True)
    notes        = db.Column(db.Text, nullable=True)
    time_spent   = db.Column(db.Integer, default=0) # time spent in seconds
    is_submitted = db.Column(db.Boolean, default=False)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime, nullable=True)

    paper = db.relationship('QuestionPaper')

    def to_dict(self):
        return {
            'id':           self.id,
            'student_id':   self.student_id,
            'paper_id':     self.paper_id,
            'paper':        self.paper.to_dict() if self.paper else None,
            'notes':        self.notes,
            'time_spent':   self.time_spent,
            'is_submitted': self.is_submitted,
            'created_at':   self.created_at.strftime('%d %b %Y %H:%M'),
            'submitted_at': self.submitted_at.strftime('%d %b %Y %H:%M') if self.submitted_at else None,
        }


# ─────────────────────────────────────────────────────────────────────────────
#  GroupMessage  –  collaborative study groups department messages
# ─────────────────────────────────────────────────────────────────────────────

class GroupMessage(db.Model):
    __tablename__ = 'group_messages'

    id         = db.Column(db.Integer, primary_key=True)
    department = db.Column(db.String(50), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    content    = db.Column(db.Text, nullable=False)
    reactions  = db.Column(db.Text, nullable=True, default='{}')
    parent_id  = db.Column(db.Integer, db.ForeignKey('group_messages.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship('Student', backref=db.backref('group_messages', lazy='dynamic', cascade='all, delete-orphan'))

    def to_dict(self):
        import json
        try:
            reactions_dict = json.loads(self.reactions) if self.reactions else {}
        except Exception:
            reactions_dict = {}
            
        parent_msg = GroupMessage.query.get(self.parent_id) if self.parent_id else None
        parent_dict = None
        if parent_msg:
            parent_dict = {
                'id': parent_msg.id,
                'author': parent_msg.student.display_name if parent_msg.student else "Anonymous",
                'content': parent_msg.content
            }
            
        return {
            'id':         self.id,
            'department': self.department,
            'student_id': self.student_id,
            'student':    self.student.to_dict() if self.student else None,
            'content':    self.content,
            'reactions':  reactions_dict,
            'parent_id':  self.parent_id,
            'parent':     parent_dict,
            'created_at': self.created_at.strftime('%I:%M %p')
        }


# ─────────────────────────────────────────────────────────────────────────────
#  StudentAIChat  –  academic AI Study Buddy Chat History logs
# ─────────────────────────────────────────────────────────────────────────────

class StudentAIChat(db.Model):
    __tablename__ = 'student_ai_chats'

    id         = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False, index=True)
    paper_id   = db.Column(db.Integer, db.ForeignKey('question_papers.id', ondelete='CASCADE'), nullable=False, index=True)
    sender     = db.Column(db.String(10), nullable=False) # 'student' or 'tutor'
    message    = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship('Student', backref=db.backref('ai_chats', lazy='dynamic', cascade='all, delete-orphan'))
    paper   = db.relationship('QuestionPaper')

    def to_dict(self):
        return {
            'id':         self.id,
            'student_id': self.student_id,
            'paper_id':   self.paper_id,
            'sender':     self.sender,
            'message':    self.message,
            'created_at': self.created_at.strftime('%I:%M %p')
        }


# ─────────────────────────────────────────────────────────────────────────────
#  Question  –  individual questions stored in the SQLite database
# ─────────────────────────────────────────────────────────────────────────────

class Question(db.Model):
    __tablename__ = 'questions'

    id           = db.Column(db.Integer, primary_key=True)
    text         = db.Column(db.Text, nullable=False)
    marks        = db.Column(db.Integer, nullable=False, default=10)
    subject_code = db.Column(db.String(30), nullable=True)
    ai_answer    = db.Column(db.Text, nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def to_dict(self):
        return {
            'id':           self.id,
            'text':         self.text,
            'marks':        self.marks,
            'subject_code': self.subject_code or '',
            'created_at':   self.created_at.strftime('%d %b %Y'),
        }


# ─────────────────────────────────────────────────────────────────────────────
#  StudyResource  –  supplementary study notes/resources uploaded by students
# ─────────────────────────────────────────────────────────────────────────────

class StudyResource(db.Model):
    __tablename__ = 'study_resources'

    id           = db.Column(db.Integer, primary_key=True)
    paper_id     = db.Column(db.Integer, db.ForeignKey('question_papers.id', ondelete='CASCADE'), nullable=False)
    student_id   = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='SET NULL'), nullable=True)
    title        = db.Column(db.String(200), nullable=False)
    filename     = db.Column(db.String(300), nullable=False)
    uploaded_at  = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship('Student')
    paper   = db.relationship('QuestionPaper')

    def to_dict(self):
        return {
            'id':          self.id,
            'paper_id':    self.paper_id,
            'student_id':  self.student_id,
            'title':       self.title,
            'filename':    self.filename,
            'uploaded_at': self.uploaded_at.strftime('%d %b %Y'),
            'student_name': self.student.username if self.student else 'Anonymous Student'
        }


# ─────────────────────────────────────────────────────────────────────────────
#  AICache  –  caching generated AI study guide outputs to save tokens
# ─────────────────────────────────────────────────────────────────────────────

class AICache(db.Model):
    __tablename__ = 'ai_caches'

    id            = db.Column(db.Integer, primary_key=True)
    paper_id      = db.Column(db.Integer, db.ForeignKey('question_papers.id', ondelete='CASCADE'), nullable=False, index=True)
    action        = db.Column(db.String(50), nullable=False, index=True)
    response_text = db.Column(db.Text, nullable=False)
    is_mock       = db.Column(db.Boolean, default=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship to paper
    paper         = db.relationship('QuestionPaper', backref=db.backref('ai_caches', lazy='dynamic', cascade='all, delete-orphan'))

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


