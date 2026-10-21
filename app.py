import os
import re
import secrets
import calendar
import hmac
from datetime import date, datetime
from functools import wraps
from flask import Flask, flash, render_template, request, redirect, url_for, abort, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, or_, text
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from PyPDF2 import PdfReader

app = Flask(__name__)
app.secret_key = os.environ.get("WORKFORCE_SECRET_KEY") or secrets.token_hex(32)
if not os.environ.get("WORKFORCE_SECRET_KEY"):
    app.logger.warning(
        "WORKFORCE_SECRET_KEY is unset; login sessions will expire when the app restarts."
    )

# --- CONFIG FOR DATABASE & RESUME UPLOADS ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///workforce.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

ADMIN_PAGE_SIZE = 10


def _pagination_details(items, total, page):
    total_pages = max(1, (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
    page = min(max(page, 1), total_pages)
    return {
        "items": items,
        "page": page,
        "pages": total_pages,
        "total": total,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_num": page - 1,
        "next_num": page + 1,
        "first_item": (page - 1) * ADMIN_PAGE_SIZE + 1 if total else 0,
        "last_item": min(page * ADMIN_PAGE_SIZE, total),
    }


def paginate_query(query, page):
    total = query.order_by(None).count()
    total_pages = max(1, (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
    page = min(max(page, 1), total_pages)
    items = query.offset((page - 1) * ADMIN_PAGE_SIZE).limit(ADMIN_PAGE_SIZE).all()
    return _pagination_details(items, total, page)


def paginate_items(items, page):
    total = len(items)
    total_pages = max(1, (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
    page = min(max(page, 1), total_pages)
    start = (page - 1) * ADMIN_PAGE_SIZE
    return _pagination_details(items[start:start + ADMIN_PAGE_SIZE], total, page)


UPLOAD_FOLDER = os.path.join(app.root_path, "static", "resumes")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# --- DATABASE MODELS ---
class Employee(db.Model):
    id = db.Column(db.String(50), primary_key=True) # e.g. "EMP-001"
    name = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(100), nullable=False)
    dept = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    status = db.Column(db.String(50), default="Active")
    avatar = db.Column(db.String(10), nullable=False)
    phone = db.Column(db.String(50), nullable=False)
    joining = db.Column(db.String(50), nullable=False)
    
    dob = db.Column(db.String(50))
    address = db.Column(db.Text)
    emergency_name = db.Column(db.String(150))
    emergency_phone = db.Column(db.String(50))
    bank_account = db.Column(db.String(100))
    ifsc_code = db.Column(db.String(50))
    resume_file = db.Column(db.String(255))


class Job(db.Model):
    id = db.Column(db.String(50), primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    dept = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), default="Full-Time")
    applicants = db.Column(db.Integer, default=0)
    status = db.Column(db.String(50), default="Active")


class Candidate(db.Model):
    id = db.Column(db.String(50), primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(150), nullable=False)
    exp = db.Column(db.String(50), nullable=False)
    skills = db.Column(db.Text, nullable=False)
    stage = db.Column(db.String(50), default="Applied")
    match = db.Column(db.String(10), default="85%")
    summary = db.Column(db.Text)
    resume_file = db.Column(db.String(255), default="default_resume.pdf")


class Attendance(db.Model):
    id = db.Column(db.String(50), primary_key=True)
    emp_id = db.Column(db.String(50), db.ForeignKey("employee.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    date = db.Column(db.String(50), nullable=False)
    check_in = db.Column(db.String(50), default="-")
    check_out = db.Column(db.String(50), default="-")
    status = db.Column(db.String(50), default="Present")

class Application(db.Model):
  id = db.Column(db.String(50), primary_key=True)
  name = db.Column(db.String(150), nullable=False)
  email = db.Column(db.String(150), nullable=False)
  role = db.Column(db.String(150), nullable=False)
  date = db.Column(db.String(50), nullable=False)
  status = db.Column(db.String(50), default='Pending Review')
  match = db.Column(db.String(10), default='85%')
  resume_file = db.Column(db.String(255), default='resume.pdf')
  interview_date = db.Column(db.String(50), nullable=True)
  interview_time = db.Column(db.String(50), nullable=True)


class Interview(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    candidate = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(150), nullable=False)
    interviewer = db.Column(db.String(150), nullable=False)  # Stores employee name/username
    date = db.Column(db.String(100), nullable=False)
    interview_type = db.Column(db.String(50), default='Online')  # Online / In-person
    status = db.Column(db.String(50), default='Scheduled')  # Scheduled, Completed
    
    # Scorecard & Feedback Fields
    skills_score = db.Column(db.Integer, default=0)
    problem_solving_score = db.Column(db.Integer, default=0)
    communication_score = db.Column(db.Integer, default=0)
    role_fit_score = db.Column(db.Integer, default=0)
    feedback_notes = db.Column(db.Text, nullable=True)
    feedback_status = db.Column(db.String(50), default='Pending')  # Pending / Submitted

class LeaveRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    emp_id = db.Column(db.String(50), db.ForeignKey('employee.id'), nullable=False)
    leave_type = db.Column(db.String(50), nullable=False)
    start_date = db.Column(db.String(50), nullable=False)
    end_date = db.Column(db.String(50), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), default="Pending")
    manager_id = db.Column(db.String(50))
    manager_name = db.Column(db.String(150))
    manager_sent_at = db.Column(db.String(100))

    @property
    def duration(self):
        try:
            start = datetime.strptime(self.start_date, "%Y-%m-%d")
            end = datetime.strptime(self.end_date, "%Y-%m-%d")
            days = (end - start).days + 1
        except (TypeError, ValueError):
            days = 1

        return f"{days} day{'s' if days != 1 else ''}"


class User(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  username = db.Column(db.String(150), unique=True, nullable=False)
  email = db.Column(
      db.String(150),
      unique=True,
      nullable=False,
      default=lambda context: context.get_current_parameters()["username"]
  )
  password = db.Column(db.String(150), nullable=False)
  name = db.Column(db.String(150), nullable=True)
  role = db.Column(db.String(50), nullable=False, default='employee')

class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'
    
    id = db.Column(db.Integer, primary_key=True)
    emp_id = db.Column(db.String(50), nullable=False)
    sender = db.Column(db.String(20), nullable=False)  # 'user' or 'bot'
    message = db.Column(db.Text, nullable=False)  

def calculate_ai_match(resume_path, target_role_or_desc):
  """Extracts text from PDF CV and computes keyword match percentage against target role/description."""
  if not resume_path or not os.path.exists(resume_path):
    return '50%'  # Default baseline if no resume uploaded

  try:
    # 1. Read PDF text
    reader = PdfReader(resume_path)
    resume_text = ''
    for page in reader.pages:
      text = page.extract_text()
      if text:
        resume_text += text.lower() + ' '

    if not resume_text.strip():
      return '60%'

    # 2. Extract core skills/keywords from target job role or description
    keywords = re.findall(r'\b[a-zA-Z]{3,}\b', target_role_or_desc.lower())

    # Common core skill keywords to check for
    tech_skills = [
        'python',
        'flask',
        'sql',
        'javascript',
        'html',
        'css',
        'react',
        'node',
        'data',
        'developer',
        'engineer',
        'api',
        'git',
        'database',
        'agile',
        'aws',
        'java',
        'c++',
    ]

    # Combine extracted job keywords and standard skills
    target_skills = set(
        [k for k in keywords if k not in ['senior', 'junior', 'the', 'and']]
        + tech_skills
    )

    # 3. Count matching terms in candidate CV
    matched_count = sum(1 for skill in target_skills if skill in resume_text)

    # 4. Report the actual keyword overlap instead of forcing a baseline score.
    score = int((matched_count / max(len(target_skills), 1)) * 100)
    return f'✨ {score}%'

  except Exception as e:
    print(f'CV Parsing Error: {e}')
    return '75%'


def calculate_application_match(resume_file, target_role):
    if not resume_file or os.path.basename(resume_file) != resume_file:
        return "Not assessed"

    resume_path = os.path.join(app.config["UPLOAD_FOLDER"], resume_file)
    if not os.path.isfile(resume_path):
        return "Not assessed"

    return calculate_ai_match(resume_path, target_role or "")


LEAVE_ALLOWANCES = {
    "Casual Leave": ("casual", 12),
    "Sick Leave": ("sick", 8),
    "Privilege Leave": ("privilege", 5),
    "Earned Leave": ("earned", 2),
}


def get_leave_usage(leaves, statuses=("Approved",)):
    usage = {balance_key: 0 for balance_key, _ in LEAVE_ALLOWANCES.values()}

    for leave in leaves:
        leave_info = LEAVE_ALLOWANCES.get(leave.leave_type)
        if leave.status not in statuses or leave_info is None:
            continue

        try:
            start = date.fromisoformat(leave.start_date)
            end = date.fromisoformat(leave.end_date)
        except (TypeError, ValueError):
            try:
                start = datetime.strptime(leave.start_date, "%d-%m-%Y").date()
                end = datetime.strptime(leave.end_date, "%d-%m-%Y").date()
            except (TypeError, ValueError):
                continue

        days = (end - start).days + 1
        if days > 0:
            balance_key, _ = leave_info
            usage[balance_key] += days

    return usage


def get_leave_balances(usage):
    return {
        balance_key: max(0, allowance - usage[balance_key])
        for balance_key, allowance in LEAVE_ALLOWANCES.values()
    }


class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    emp_id = db.Column(db.String(50), db.ForeignKey('employee.id'), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    timestamp = db.Column(db.String(100), nullable=False)        

def get_current_employee():
    user_id = session.get("user_id")
    user = db.session.get(User, user_id) if user_id else None
    if not user:
        return None, None
    emp = Employee.query.filter(db.func.lower(Employee.email) == user.email.strip().lower()).first()
    return user, emp


@app.context_processor
def inject_employee_header_activities():
    if request.endpoint == "emp_dashboard" or not request.path.startswith(
        ("/employee/", "/employee-", "/emp/")
    ):
        return {}

    user, employee = get_current_employee()
    if user is None or user.role != "employee" or employee is None:
        return {}

    activities = ActivityLog.query.filter_by(emp_id=employee.id)\
                                  .order_by(ActivityLog.id.desc())\
                                  .limit(5)\
                                  .all()
    return {"employee_header_activities": activities}


def log_activity(emp_id, message):
    time_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    new_log = ActivityLog(emp_id=emp_id, message=message, timestamp=time_str)
    db.session.add(new_log)
    db.session.commit()

def next_employee_id():
    employee_number = Employee.query.count() + 1
    while Employee.query.filter_by(
        id=f"EMP-{employee_number:03d}"
    ).first() is not None:
        employee_number += 1
    return f"EMP-{employee_number:03d}"


# --- SEED INITIAL DATA FUNCTION ---
def seed_database():
    if not Employee.query.first():
        initial_employees = [
            Employee(id="EMP-001", name="Shafaque Perveen", role="Lead Developer", dept="Engineering", email="shafaque@workforcepro.demo", status="Active", avatar="SP", phone="+91 98765 43210", joining="Jan 2025"),
            Employee(id="EMP-002", name="Aman Kumar", role="HR Manager", dept="Human Resources", email="aman@workforcepro.demo", status="Active", avatar="AK", phone="+91 87654 32109", joining="Mar 2025"),
            Employee(id="EMP-003", name="Priya Sharma", role="UI/UX Designer", dept="Design", email="priya@workforcepro.demo", status="On Leave", avatar="PS", phone="+91 76543 21098", joining="Jun 2025"),
        ]
        db.session.bulk_save_objects(initial_employees)
        db.session.commit()

    if not Attendance.query.first():
        initial_attendance = [
            Attendance(id="ATT-01", emp_id="EMP-001", name="Shafaque Perveen", date="2026-09-27", check_in="09:15 AM", check_out="06:00 PM", status="Present"),
            Attendance(id="ATT-02", emp_id="EMP-002", name="Aman Kumar", date="2026-09-27", check_in="10:05 AM", check_out="-", status="Late"),
        ]
        db.session.bulk_save_objects(initial_attendance)
        db.session.commit()

    if not Interview.query.first():
        initial_interviews = [
            Interview(candidate="Sarah Jenkins", role="Frontend Engineer", interviewer="Alex Mercer", date="2026-09-28 10:00 AM", status="Scheduled"),
        ]
        db.session.bulk_save_objects(initial_interviews)
        db.session.commit()

    # --- SEED EMPLOYEE USER ACCOUNTS ---
    demo_employees = [
        ("shafaque@workforcepro.demo", "password123"),
        ("aman@workforcepro.demo", "password123"),
        ("priya@workforcepro.demo", "password123")
    ]
    for emp_email, emp_pwd in demo_employees:
        if not User.query.filter_by(email=emp_email).first():
            emp_user = User(
                username=emp_email,
                email=emp_email,
                password=generate_password_hash(emp_pwd),
                role="employee"
            )
            db.session.add(emp_user)

    # --- SEED ADMIN USER ACCOUNT ---
    admin_email = os.environ.get("WORKFORCE_ADMIN_EMAIL", "admin@workforcepro.demo").strip().lower()
    admin_password = os.environ.get("WORKFORCE_ADMIN_PASSWORD", "admin123")
    
    admin_user = User.query.filter_by(role="admin").first()
    if admin_user is None:
        admin_user = User(
            username=admin_email,
            email=admin_email,
            password=generate_password_hash(admin_password),
            role="admin"
        )
        db.session.add(admin_user)
    else:
        admin_user.email = admin_email
        admin_user.username = admin_email

    db.session.commit()


def verify_password(stored_password, submitted_password):
    if not stored_password or not submitted_password:
        return False
    return check_password_hash(stored_password, submitted_password)


def get_csrf_token():
    token = session.get("_csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf_token():
    expected_token = session.get("_csrf_token")
    submitted_token = request.form.get("csrf_token", "")
    if not expected_token or not hmac.compare_digest(expected_token, submitted_token):
        abort(400, description="Invalid form submission. Refresh the page and try again.")


def role_required(role):
    def decorator(view_function):
        @wraps(view_function)
        def wrapped_view(*args, **kwargs):
            user_id = session.get("user_id")
            user = db.session.get(User, user_id) if user_id is not None else None

            if user is None:
                session.clear()
                return redirect(url_for("login"))
            if user.role != role:
                abort(403)

            return view_function(*args, **kwargs)
        return wrapped_view
    return decorator


# --- ROUTES ---
@app.route('/')
def welcome():
  # Smart Session Check: Redirect logged-in users directly to their portals
  user_id = session.get('user_id')
  if user_id:
    user = db.session.get(User, user_id)
    if user:
      if user.role == 'admin':
        return redirect(url_for('dashboard'))
      return redirect(url_for('employee_portal'))

  # Real-time Database Statistics
  try:
    total_employees = User.query.filter_by(role='employee').count()
    active_jobs_count = Job.query.filter_by(status='Active').count()

    # Calculate real today's attendance percentage
    today_str = date.today().isoformat()
    present_today = Attendance.query.filter(
        Attendance.date.like(f'{today_str}%'), Attendance.status == 'Present'
    ).count()

    attendance_percentage = (
        round((present_today / total_employees) * 100, 1)
        if total_employees > 0
        else 100.0
    )
  except Exception:
    total_employees = 0
    active_jobs_count = 0
    attendance_percentage = 0.0

  return render_template(
      'welcome.html',
      total_employees=total_employees,
      active_jobs_count=active_jobs_count,
      attendance_percentage=attendance_percentage,
  )

@app.route('/get-started')
def get_started():
  # 1. Check if the user is already logged in
  user_id = session.get('user_id')
  if user_id:
    user = db.session.get(User, user_id)
    if user:
      # If logged in, skip login/signup and take them straight to their workspace
      if user.role == 'admin':
        return redirect(url_for('dashboard'))
      return redirect(url_for('employee_portal'))

  # 2. If not logged in, direct them to the registration/onboarding page
  return redirect(url_for('signup'))


import os

ADMIN_SECRET_KEY = os.environ.get('ADMIN_SECRET_KEY', 'HR2026')  #secret key for admin signup

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        username = (request.form.get('username') or '').strip().lower()
        password = request.form.get('password')
        role = (request.form.get('role') or 'employee').strip().lower()
        secret_key = request.form.get('admin_secret_key')

        # 1. Validate email format (@gmail.com)
        if not username or not username.endswith('@gmail.com'):
            flash('Email must be a valid @gmail.com address.', 'danger')
            return redirect(url_for('signup'))

        if role not in {'employee', 'admin'}:
            flash('Choose a valid workspace role.', 'danger')
            return redirect(url_for('signup'))

        # 2. Validate password length (fixed to 6 digits/characters)
        if not password or len(password) != 6:
            flash('Password must be exactly 6 characters/digits long.', 'danger')
            return redirect(url_for('signup'))

        existing_user = User.query.filter(
            or_(
                db.func.lower(User.username) == username,
                db.func.lower(User.email) == username
            )
        ).first()
        if existing_user:
            flash('Email already registered. Please log in.', 'danger')
            return redirect(url_for('signup'))

        if role == 'admin':
            if not secret_key or secret_key != ADMIN_SECRET_KEY:
                flash('Invalid Admin Secret Key. Access denied.', 'danger')
                return redirect(url_for('signup'))

        # Hash password and create new user in database
        hashed_password = generate_password_hash(password)
        new_user = User(username=username, password=hashed_password, name=name, role=role)
        
        db.session.add(new_user)
        db.session.commit()

        # Log user in automatically after signup
        session['user_id'] = new_user.id
        session['role'] = new_user.role

        flash('Account created successfully! Welcome to WorkforcePro.', 'success')

        # Route based on chosen workspace role
        if new_user.role == 'admin':
            return redirect(url_for('dashboard'))
        return redirect(url_for('employee_portal'))

    return render_template('signup.html')


@app.route("/login")
def login():
    if session.get("user_id"):
        user = db.session.get(User, session.get("user_id"))
        if user:
            if user.role == "admin":
                return redirect(url_for("dashboard"))
            return redirect(url_for("employee_portal"))
    return render_template("login.html", registered=request.args.get("registered") == "1")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    password_confirm = request.form.get("password_confirm") or ""
    selected_role = (request.form.get("role") or "employee").strip().lower()

    if selected_role not in {"employee", "admin"}:
        return render_template(
            "register.html",
            email=email,
            error="Choose a valid account type."
        ), 400

    if not email:
        return render_template(
            "register.html",
            email=email,
            error="Enter an email address."
        ), 400

    if User.query.filter(
        or_(
            db.func.lower(User.email) == email,
            db.func.lower(User.username) == email
        )
    ).first() is not None:
        return render_template(
            "register.html",
            email=email,
            error="An account already exists for this email. Please sign in."
        ), 409

    if len(password) < 8:
        return render_template(
            "register.html",
            email=email,
            error="Choose a password with at least 8 characters."
        ), 400

    if password != password_confirm:
        return render_template(
            "register.html",
            email=email,
            error="The passwords do not match."
        ), 400

    user = User(
        username=email,
        email=email,
        password=generate_password_hash(password),
        role=selected_role
    )
    db.session.add(user)

    if selected_role == "employee":
        emp = Employee.query.filter(db.func.lower(Employee.email) == email).first()
        if emp is None:
            new_emp = Employee(
                id=next_employee_id(),
                name=email.split('@')[0].replace('.', ' ').title(),
                role="Staff Member",
                dept="General",
                email=email,
                status="Active",
                avatar="US",
                phone="+91 99999 99999",
                joining="Jan 2026"
            )
            db.session.add(new_emp)

    db.session.commit()
    return redirect(url_for("login", registered="1"))


@app.route("/login-action", methods=["POST"])
def login_action():
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    selected_role = (request.form.get("role") or "").strip().lower()
    
    user = User.query.filter(
        or_(
            db.func.lower(User.email) == email,
            db.func.lower(User.username) == email
        )
    ).first() if email else None

    if (
        selected_role not in {"employee", "admin"}
        or user is None
        or user.role != selected_role
        or not verify_password(user.password, password)
    ):
        return render_template(
            "login.html",
            error="Invalid email, password, or account type.",
            registered=False
        ), 401

    session.permanent = True
    session['user_id'] = user.id
    session['role'] = user.role

    if user.role == "admin":
        return redirect(url_for("dashboard"))
    
    emp = Employee.query.filter(db.func.lower(Employee.email) == email).first()
    if emp is None:
        new_emp = Employee(
            id=next_employee_id(),
            name=email.split('@')[0].replace('.', ' ').title(),
            role="Staff Member",
            dept="General",
            email=email,
            status="Active",
            avatar="US",
            phone="+91 99999 99999",
            joining="Jan 2026"
        )
        db.session.add(new_emp)
        db.session.commit()

    return redirect(url_for("employee_portal"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@role_required("admin")
def dashboard():
    # ---------------------------------------------------------
    # 0. Current Live Date Fix
    # ---------------------------------------------------------
    current_date = datetime.now().strftime("%a, %d %b %Y")  # e.g., Mon, 19 Oct 2026

    # ---------------------------------------------------------
    # 1. Dynamic Metric Counts & Notifications
    # ---------------------------------------------------------
    employee_count = User.query.filter_by(role='employee').count()
    open_job_count = Job.query.filter_by(status="Active").count()
    pending_applications = Application.query.filter_by(
        status="Pending Review"
    ).all()
    scheduled_interviews = Interview.query.filter_by(
        status="Scheduled"
    ).all()
    pending_application_count = len(pending_applications)
    scheduled_interview_count = len(scheduled_interviews)
    leave_requests = LeaveRequest.query.order_by(LeaveRequest.id.desc()).all()
    pending_leave_count = sum(
        leave.status in {"Pending", "Awaiting Manager Approval"}
        for leave in leave_requests
    )
    leave_employee_ids = {leave.emp_id for leave in leave_requests}
    leave_employees = {
        employee.id: employee
        for employee in Employee.query.filter(Employee.id.in_(leave_employee_ids)).all()
    } if leave_employee_ids else {}
    leave_managers = Employee.query.filter_by(status="Active").order_by(
        Employee.name
    ).all()

    notifications = [
        {
            "title": "Pending Application",
            "message": f"{getattr(app, 'name', getattr(app, 'applicant_name', 'Candidate'))} applied for position.",
            "time": getattr(app, 'date', 'Recent'),
            "url": url_for("applications"),
            "icon": "bi-file-earmark-person",
        }
        for app in pending_applications
    ]
    notifications.extend([
        {
            "title": "Interview Scheduled",
            "message": f"Interview with {getattr(interview, 'candidate', getattr(interview, 'candidate_name', 'Candidate'))}.",
            "time": getattr(interview, 'date', 'Upcoming'),
            "url": url_for("interviews"),
            "icon": "bi-calendar-event",
        }
        for interview in scheduled_interviews
    ])
    notification_count = len(notifications)

    # ---------------------------------------------------------
    # 2. Dynamic Recruitment Chart Data (Database Driven)
    # ---------------------------------------------------------
    current_year = datetime.now().year
    selected_year = request.args.get('year', current_year, type=int)

    applications = Application.query.all()
    monthly_counts = [0] * 12
    dated_applications = []

    for application in applications:
        app_date_val = getattr(application, 'date', None) or getattr(application, 'created_at', None)
        if not app_date_val:
            continue
        try:
            # Handle ISO format string or datetime object from DB safely
            if isinstance(app_date_val, str):
                application_date = datetime.fromisoformat(app_date_val)
            else:
                application_date = app_date_val
        except (TypeError, ValueError):
            continue

        dated_applications.append((application, application_date))

        # Y-Axis Data: Increment monthly count if application matches the selected year
        if application_date.year == selected_year:
            monthly_counts[application_date.month - 1] += 1

    # Extract all distinct years available in SQLite for the dropdown filter
    available_years = sorted(
        {app_date.year for _, app_date in dated_applications},
        reverse=True
    )
    if current_year not in available_years:
        available_years.insert(0, current_year)

    # ---------------------------------------------------------
    # 3. Dynamic Recent Activity Feed (All Activities Combined)
    # ---------------------------------------------------------
    all_activities = []

    # Add Applications
    for app, app_date in dated_applications:
        all_activities.append({
            'title': 'Application Received',
            'description': f"{getattr(app, 'applicant_name', getattr(app, 'name', 'Candidate'))} applied for job",
            'date_obj': app_date,
            'date_str': app_date.strftime('%Y-%m-%d'),
            'icon': 'bi-file-earmark-text'
        })

    # Add Interviews
    for interview in Interview.query.all():
        int_date_val = getattr(interview, 'date', None) or getattr(interview, 'created_at', None)
        if not int_date_val:
            continue
        try:
            int_date = datetime.fromisoformat(str(int_date_val)) if isinstance(int_date_val, str) else int_date_val
        except (TypeError, ValueError):
            continue
        all_activities.append({
            'title': 'Interview Scheduled',
            'description': f"Interview with {getattr(interview, 'candidate_name', getattr(interview, 'candidate', 'Candidate'))}",
            'date_obj': int_date,
            'date_str': int_date.strftime('%Y-%m-%d'),
            'icon': 'bi-calendar-event'
        })

    # Add New Employees (Users with role='employee')
    for emp in User.query.filter_by(role='employee').all():
        emp_date_val = getattr(emp, 'date', None) or getattr(emp, 'created_at', None)
        if not emp_date_val:
            continue
        try:
            emp_date = datetime.fromisoformat(str(emp_date_val)) if isinstance(emp_date_val, str) else emp_date_val
        except (TypeError, ValueError):
            continue
        all_activities.append({
            'title': 'New Employee Added',
            'description': f"{getattr(emp, 'name', 'Employee')} joined as employee",
            'date_obj': emp_date,
            'date_str': emp_date.strftime('%Y-%m-%d'),
            'icon': 'bi-person-plus'
        })

    # Add Job Postings
    for job in Job.query.all():
        job_date_val = getattr(job, 'date', None) or getattr(job, 'created_at', None)
        if not job_date_val:
            continue
        try:
            job_date = datetime.fromisoformat(str(job_date_val)) if isinstance(job_date_val, str) else job_date_val
        except (TypeError, ValueError):
            continue
        all_activities.append({
            'title': 'Job Posted',
            'description': f"New position '{getattr(job, 'title', 'Job Title')}' created",
            'date_obj': job_date,
            'date_str': job_date.strftime('%Y-%m-%d'),
            'icon': 'bi-briefcase'
        })

    # Sort all activities by date descending and get top 5
    recent_activities = sorted(
        all_activities,
        key=lambda x: x['date_obj'],
        reverse=True
    )[:5]

    return render_template(
        "dashboard.html",
        current_date=current_date,
        employee_count=employee_count,
        open_job_count=open_job_count,
        pending_application_count=pending_application_count,
        scheduled_interview_count=scheduled_interview_count,
        monthly_counts=monthly_counts,
        selected_year=selected_year,
        available_years=available_years,
        recent_activities=recent_activities,
        notification_count=notification_count,
        notifications=notifications,
        leave_requests=leave_requests,
        pending_leave_count=pending_leave_count,
        leave_employees=leave_employees,
        leave_managers=leave_managers,
        csrf_token=get_csrf_token(),
    )


@app.route("/leave-request/<int:leave_id>/send-for-approval", methods=["POST"])
@role_required("admin")
def send_leave_for_manager_approval(leave_id):
    validate_csrf_token()
    leave = db.get_or_404(LeaveRequest, leave_id)
    if leave.status != "Pending":
        abort(409, description="Only pending leave requests can be sent for manager approval.")

    manager_id = (request.form.get("manager_id") or "").strip()
    manager = db.session.get(Employee, manager_id) if manager_id else None
    if manager is None or manager.status != "Active" or manager.id == leave.emp_id:
        abort(400, description="Choose a valid active manager for this request.")

    leave.manager_id = manager.id
    leave.manager_name = manager.name
    leave.manager_sent_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    leave.status = "Awaiting Manager Approval"
    db.session.commit()
    flash(f"Leave request sent to {manager.name} for manager approval.", "success")
    return redirect(url_for("dashboard"))


@app.route("/leave-request/<int:leave_id>/decision", methods=["POST"])
@role_required("admin")
def decide_leave_request(leave_id):
    validate_csrf_token()
    leave = db.get_or_404(LeaveRequest, leave_id)
    if leave.status != "Awaiting Manager Approval":
        abort(409, description="Only requests awaiting manager approval can be finalized.")

    decision = (request.form.get("decision") or "").strip()
    if decision not in {"Approved", "Rejected"}:
        abort(400, description="Choose Approve or Reject.")

    leave.status = decision
    if leave.emp_id:
        db.session.add(ActivityLog(
            emp_id=leave.emp_id,
            message=f"Your {leave.leave_type} request for {leave.start_date} to {leave.end_date} was {decision.lower()} by HR.",
            timestamp=datetime.now().strftime("%d %b %Y, %I:%M %p"),
        ))
    db.session.commit()
    flash(f"Leave request {decision.lower()}. The employee's leave status has been updated.", "success")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------
# 4. Global Search Endpoint (Connected to Search Bar)
# ---------------------------------------------------------
@app.route("/dashboard/search")
@role_required("admin")
def dashboard_search():
    query = request.args.get('q', '').strip()
    if not query:
        return redirect(url_for('dashboard'))

    # Search across employees first, redirecting to employees view
    matching_employee = User.query.filter(
        User.role == 'employee',
        or_(User.username.ilike(f'%{query}%'), User.email.ilike(f'%{query}%'))
    ).first()

    if matching_employee:
        return redirect(url_for('employees', search=query))

    # Search across open jobs
    matching_job = Job.query.filter(
        or_(Job.title.ilike(f'%{query}%'), Job.dept.ilike(f'%{query}%'))
    ).first()

    if matching_job:
        return redirect(url_for('jobs', search=query))

    # Default fallback to applications listing
    return redirect(url_for('applications', search=query))


@app.route("/employee-portal")
@role_required("employee")
def employee_portal():
    user_id = session.get("user_id")
    user = db.session.get(User, user_id) if user_id else None
    if user is None:
        session.clear()
        return redirect(url_for("login"))

    employee = Employee.query.filter(
        db.func.lower(Employee.email) == user.email.strip().lower()
    ).first()
    
    if employee is None:
        employee = Employee(
            id=next_employee_id(),
            name=user.name or user.email.split('@')[0].replace('.', ' ').title(),
            role="Staff Member",
            dept="General",
            email=user.email,
            status="Active",
            avatar="US",
            phone="+91 99999 99999",
            joining="Jan 2026"
        )
        db.session.add(employee)
        db.session.commit()

    return render_template("employee_portal.html", employee=employee, user=user)


@app.route("/employee/jobs")
@role_required("employee")
def employee_jobs():
    _, employee = get_current_employee()
    if employee is None:
        return redirect(url_for("employee_portal"))

    search_query = request.args.get("q", "").strip()
    department_filter = request.args.get("department", "").strip()
    type_filter = request.args.get("type", "").strip()
    query = Job.query.filter_by(status="Active")
    if search_query:
        query = query.filter(
            or_(
                Job.title.ilike(f"%{search_query}%"),
                Job.dept.ilike(f"%{search_query}%"),
                Job.location.ilike(f"%{search_query}%"),
            )
        )
    if department_filter:
        query = query.filter(Job.dept == department_filter)
    if type_filter:
        query = query.filter(Job.type == type_filter)

    jobs_list = query.order_by(Job.title).all()
    active_job_count = Job.query.filter_by(status="Active").count()
    departments = [
        value
        for (value,) in db.session.query(Job.dept)
        .filter(Job.status == "Active")
        .distinct()
        .order_by(Job.dept)
    ]
    job_types = [
        value
        for (value,) in db.session.query(Job.type)
        .filter(Job.status == "Active", Job.type.isnot(None))
        .distinct()
        .order_by(Job.type)
    ]
    applications_by_job = {
        application.role: application
        for application in Application.query.filter_by(email=employee.email).all()
    }
    return render_template(
        "employee_jobs.html",
        employee=employee,
        jobs=jobs_list,
        active_job_count=active_job_count,
        departments=departments,
        job_types=job_types,
        search_query=search_query,
        department_filter=department_filter,
        type_filter=type_filter,
        applications_by_job=applications_by_job,
        applied=request.args.get("applied") == "1",
        already_applied=request.args.get("already_applied") == "1",
    )


@app.route("/employee/jobs/<job_id>/apply", methods=["POST"])
@role_required("employee")
def apply_for_job(job_id):
    _, employee = get_current_employee()
    if employee is None:
        return redirect(url_for("employee_portal"))

    job = db.get_or_404(Job, job_id)
    if job.status != "Active":
        abort(400, description="Applications are closed for this job.")

    existing_application = Application.query.filter(
        db.func.lower(Application.email) == employee.email.lower(),
        Application.role == job.title,
    ).first()
    if existing_application is not None:
        return redirect(url_for("employee_jobs", already_applied="1"))

    application = Application(
        id=f"APP-{secrets.token_hex(6).upper()}",
        name=employee.name,
        email=employee.email,
        role=job.title,
        date=date.today().isoformat(),
        status="Pending Review",
        match=calculate_application_match(employee.resume_file, job.title),
        resume_file=employee.resume_file,
    )
    db.session.add(application)
    db.session.commit()
    return redirect(url_for("employee_jobs", applied="1"))


@app.route("/employee/update-profile", methods=["POST"])
@role_required("employee")
def update_employee_profile():
    _, employee = get_current_employee()
    if employee is None:
        abort(404, description="Employee profile not found.")

    employee.dept = request.form.get("dept", employee.dept)
    employee.joining = request.form.get("joining", employee.joining)
    employee.phone = request.form.get("phone", employee.phone)
    employee.dob = request.form.get("dob", employee.dob)
    employee.address = request.form.get("address", employee.address)
    employee.bank_account = request.form.get("bank_account", employee.bank_account)
    employee.ifsc_code = request.form.get("ifsc_code", employee.ifsc_code)
    employee.emergency_name = request.form.get(
        "emergency_name", employee.emergency_name
    )
    employee.emergency_phone = request.form.get(
        "emergency_phone", employee.emergency_phone
    )

    resume = request.files.get("resume")
    safe_filename = secure_filename(resume.filename or "") if resume else ""
    if safe_filename:
        if not safe_filename.lower().endswith(".pdf"):
            abort(400, description="Employee resumes must be PDF files.")
        employee.resume_file = f"{secrets.token_hex(8)}_{safe_filename}"
        resume.save(
            os.path.join(app.config["UPLOAD_FOLDER"], employee.resume_file)
        )

    db.session.commit()
    return redirect(url_for("employee_portal"))

@app.route("/employees")
@role_required("admin")
def employees():
    search_query = request.args.get("search", "").strip()
    department_filter = request.args.get("department", "").strip()
    status_filter = request.args.get("status", "").strip()
    page = request.args.get("page", 1, type=int)

    query = Employee.query

    if search_query:
        query = query.filter(
            or_(
                Employee.name.ilike(f"%{search_query}%"),
                Employee.id.ilike(f"%{search_query}%"),
                Employee.email.ilike(f"%{search_query}%"),
            )
        )

    if department_filter:
        query = query.filter(Employee.dept == department_filter)

    if status_filter:
        query = query.filter(Employee.status == status_filter)

    pagination = paginate_query(query.order_by(Employee.name), page)
    employee_list = pagination["items"]
    department_list = [
        department
        for (department,) in db.session.query(Employee.dept)
        .distinct()
        .order_by(Employee.dept)
        .all()
    ]

    # Calculate real dynamic metric values
    active_count = query.filter(Employee.status == "Active").count()
    on_leave_count = query.filter(Employee.status == "On Leave").count()

    return render_template(
        "employees.html",
        employees=employee_list,
        pagination=pagination,
        active_count=active_count,
        on_leave_count=on_leave_count,
        department_list=department_list,
        department_count=len(department_list),
    )

@app.route("/employee/<emp_id>")
@role_required("admin")
def view_profile(emp_id):
    emp = db.get_or_404(Employee, emp_id)
    return render_template("view_profile.html", emp=emp)


@app.route("/employee/<emp_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def edit_employee(emp_id):
    emp = db.get_or_404(Employee, emp_id)
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if not email:
            abort(400, description="An employee email address is required.")
        linked_user = User.query.filter(
            db.func.lower(User.email) == emp.email.lower(),
            User.role == "employee",
        ).first()
        duplicate = Employee.query.filter(
            db.func.lower(Employee.email) == email,
            Employee.id != emp.id,
        ).first()
        duplicate_user_query = User.query.filter(db.func.lower(User.email) == email)
        if linked_user is not None:
            duplicate_user_query = duplicate_user_query.filter(
                User.id != linked_user.id
            )
        duplicate_user = duplicate_user_query.first()
        if duplicate is not None or duplicate_user is not None:
            abort(409, description="That email address is already assigned.")

        emp.name = (request.form.get("name") or "").strip()
        emp.role = (request.form.get("role") or "").strip()
        emp.dept = (request.form.get("dept") or "").strip()
        emp.email = email
        if linked_user is not None:
            linked_user.email = email
            linked_user.username = email
        emp.phone = (request.form.get("phone") or "").strip()
        emp.status = request.form.get("status") or "Active"
        emp.avatar = "".join(part[0] for part in emp.name.split()[:2]).upper()
        db.session.commit()
        return redirect(url_for("view_profile", emp_id=emp.id))

    return render_template("edit_profile.html", emp=emp)


@app.route("/employee/<emp_id>/delete", methods=["POST"])
@role_required("admin")
def delete_employee(emp_id):
    emp = db.get_or_404(Employee, emp_id)
    Attendance.query.filter_by(emp_id=emp.id).delete()
    employee_user = User.query.filter(
        db.func.lower(User.email) == emp.email.lower(),
        User.role == "employee",
    ).first()
    if employee_user is not None:
        db.session.delete(employee_user)
    db.session.delete(emp)
    db.session.commit()
    return redirect(url_for("employees"))


@app.route("/jobs")
@role_required("admin")
def jobs():
    search_query = request.args.get("q", "").strip()
    department_filter = request.args.get("department", "").strip()
    type_filter = request.args.get("type", "").strip()
    status_filter = request.args.get("status", "").strip()
    page = request.args.get("page", 1, type=int)

    query = Job.query
    if search_query:
        query = query.filter(
            or_(
                Job.title.ilike(f"%{search_query}%"),
                Job.dept.ilike(f"%{search_query}%"),
                Job.location.ilike(f"%{search_query}%"),
                Job.id.ilike(f"%{search_query}%"),
            )
        )
    if department_filter:
        query = query.filter(Job.dept == department_filter)
    if type_filter:
        query = query.filter(Job.type == type_filter)
    if status_filter:
        query = query.filter(Job.status == status_filter)

    pagination = paginate_query(query.order_by(Job.title), page)
    jobs_list = pagination["items"]
    job_applicants = {}
    for job in jobs_list:
        job_applicants[job.id] = (
            Application.query.filter_by(role=job.title).count()
            + Candidate.query.filter_by(role=job.title).count()
        )

    departments = [
        value
        for (value,) in db.session.query(Job.dept).distinct().order_by(Job.dept)
    ]
    job_types = [
        value
        for (value,) in db.session.query(Job.type)
        .filter(Job.type.isnot(None))
        .distinct()
        .order_by(Job.type)
    ]
    statuses = [
        value
        for (value,) in db.session.query(Job.status)
        .filter(Job.status.isnot(None))
        .distinct()
        .order_by(Job.status)
    ]
    return render_template(
        "jobs.html",
        jobs=jobs_list,
        pagination=pagination,
        job_applicants=job_applicants,
        departments=departments,
        job_types=job_types,
        statuses=statuses,
        search_query=search_query,
        department_filter=department_filter,
        type_filter=type_filter,
        status_filter=status_filter,
    )


@app.route("/job/<job_id>/pipeline")
@role_required("admin")
def view_pipeline(job_id):
    job = db.get_or_404(Job, job_id)
    candidates_list = Candidate.query.filter_by(role=job.title).all()
    applications_list = Application.query.filter_by(role=job.title).order_by(
        Application.date.desc()
    ).all()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render_template(
            "job_pipeline_panel.html",
            job=job,
            candidates=candidates_list,
            applications=applications_list,
        )
    return render_template(
        "job_pipeline.html",
        job=job,
        candidates=candidates_list,
        applications=applications_list,
    )


# View Candidates Directory Route
@app.route('/candidates')
def candidates():
  if 'user_id' not in session:
    return redirect(url_for('login'))

  page = request.args.get("page", 1, type=int)
  pagination = paginate_query(Candidate.query.order_by(Candidate.id), page)
  all_candidates = pagination["items"]

  # Dynamically calculate/refresh AI match percentage if resume exists
  for c in all_candidates:
    if c.resume_file:
      cv_path = os.path.join(app.root_path, 'static', 'resumes', c.resume_file)
      c.match = calculate_ai_match(cv_path, c.role)

  return render_template(
    'candidates.html',
    candidates=all_candidates,
    pagination=pagination,
  )


# View Individual Candidate Profile Route
@app.route('/candidate/view/<string:name>')
def view_candidate(name):
  if 'user_id' not in session:
    return redirect(url_for('login'))

  candidate = Candidate.query.filter_by(name=name).first_or_404()
  return render_template(
    'view_candidate.html',
    candidate=candidate,
    name=candidate.name,
    csrf_token=get_csrf_token(),
  )


@app.route("/candidate/<string:name>/stage", methods=["POST"], endpoint="update_candidate_stage")
@role_required("admin")
def update_candidate_stage(name):
  validate_csrf_token()
  candidate = Candidate.query.filter_by(name=name).first_or_404()
  stage = request.form.get("stage", "").strip()
  valid_stages = {"Applied", "Screening", "Interviewing", "Hired", "Rejected"}

  if stage not in valid_stages:
    flash("Select a valid hiring stage.", "danger")
    return redirect(url_for("view_candidate", name=candidate.name))

  candidate.stage = stage
  db.session.commit()
  flash("Candidate stage updated successfully.", "success")
  return redirect(url_for("view_candidate", name=candidate.name))


@app.route("/attendance")
@role_required("admin")
def attendance():
    today = date.today()
    year = request.args.get("year", today.year, type=int)
    month = request.args.get("month", today.month, type=int)
    page = request.args.get("page", 1, type=int)
    if not 1 <= year <= 9999 or not 1 <= month <= 12:
        year, month = today.year, today.month

    attendance_records = Attendance.query.order_by(
        Attendance.date.desc(), Attendance.id.desc()
    ).all()
    month_records = []
    calendar_days = {}
    summary = {
        "present": 0,
        "late": 0,
        "half_day": 0,
        "absent": 0,
        "on_leave": 0,
    }

    for record in attendance_records:
        try:
            record_date = date.fromisoformat(record.date)
        except (TypeError, ValueError):
            try:
                record_date = datetime.strptime(record.date, "%d %b %Y").date()
            except (TypeError, ValueError):
                continue

        if record_date.year != year or record_date.month != month:
            continue

        month_records.append(record)
        day_key = record_date.isoformat()
        day_summary = calendar_days.setdefault(
            day_key,
            {
                "count": 0,
                "present": 0,
                "late": 0,
                "half_day": 0,
                "absent": 0,
                "on_leave": 0,
            },
        )
        day_summary["count"] += 1

        if record.status == "Present":
            summary["present"] += 1
            day_summary["present"] += 1
        elif record.status == "Late":
            summary["late"] += 1
            day_summary["late"] += 1
        elif record.status == "Half Day":
            summary["half_day"] += 1
            day_summary["half_day"] += 1
        elif record.status == "Absent":
            summary["absent"] += 1
            day_summary["absent"] += 1
        elif record.status == "On Leave":
            summary["on_leave"] += 1
            day_summary["on_leave"] += 1

    previous_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    employees = Employee.query.order_by(Employee.name).all()
    pagination = paginate_items(month_records, page)

    return render_template(
        "attendance.html",
        attendance=pagination["items"],
        pagination=pagination,
        employees=employees,
        summary=summary,
        calendar_days=calendar_days,
        cal_matrix=calendar.monthcalendar(year, month),
        month_name=calendar.month_name[month],
        month_num=month,
        year=year,
        current_day=today.day if (year, month) == (today.year, today.month) else None,
        today_date=today.isoformat(),
        previous_month=previous_month,
        next_month=next_month,
    )


@app.route("/attendance/mark", methods=["POST"])
@role_required("admin")
def mark_employee_attendance():
    employee_id = (request.form.get("emp_id") or "").strip()
    attendance_date = (request.form.get("date") or "").strip()
    status = (request.form.get("status") or "").strip()
    valid_statuses = {"Present", "Late", "Half Day", "Absent", "On Leave"}

    employee = db.session.get(Employee, employee_id) if employee_id else None
    if employee is None:
        abort(400, description="Choose a valid employee.")
    try:
        parsed_date = date.fromisoformat(attendance_date)
    except ValueError:
        abort(400, description="Choose a valid attendance date.")
    if status not in valid_statuses:
        abort(400, description="Choose a valid attendance status.")

    attendance_record = None
    for record in Attendance.query.filter_by(emp_id=employee.id).all():
        try:
            existing_date = date.fromisoformat(record.date)
        except (TypeError, ValueError):
            try:
                existing_date = datetime.strptime(record.date, "%d %b %Y").date()
            except (TypeError, ValueError):
                continue
        if existing_date == parsed_date:
            attendance_record = record
            break

    if attendance_record is None:
        attendance_record = Attendance(
            id=f"ATT-{secrets.token_hex(3).upper()}",
            emp_id=employee.id,
            name=employee.name,
            date=parsed_date.isoformat(),
            check_in="-",
            check_out="-",
            status=status,
        )
        db.session.add(attendance_record)
    else:
        attendance_record.name = employee.name
        attendance_record.date = parsed_date.isoformat()
        attendance_record.status = status

    db.session.commit()
    return redirect(url_for("attendance", year=parsed_date.year, month=parsed_date.month))


@app.route("/attendance/<record_id>/update", methods=["POST"])
@role_required("admin")
def update_attendance(record_id):
    record = db.get_or_404(Attendance, record_id)
    status = request.form.get("status")
    if status not in {"Present", "Late", "Absent", "Half Day", "On Leave"}:
        abort(400, description="Choose a valid attendance status.")
    record.status = status
    db.session.commit()
    try:
        record_date = date.fromisoformat(record.date)
    except ValueError:
        record_date = datetime.strptime(record.date, "%d %b %Y").date()
    return redirect(url_for("attendance", year=record_date.year, month=record_date.month))
@app.route('/applications')
@role_required('admin')
def applications():
  search_query = request.args.get('search', '').strip()
  status_filter = request.args.get('status', '').strip()
  role_filter = request.args.get('role', '').strip()
  page = request.args.get('page', 1, type=int)

  query = Application.query

  if search_query:
    query = query.filter(
        db.or_(
            Application.name.ilike(f'%{search_query}%'),
            Application.email.ilike(f'%{search_query}%'),
        )
    )
  if status_filter:
    query = query.filter(Application.status == status_filter)
  if role_filter:
    query = query.filter(Application.role == role_filter)

  pagination = paginate_query(query.order_by(Application.date.desc()), page)
  applications_list = pagination["items"]
  pending_review_count = query.filter(
      Application.status == 'Pending Review'
  ).count()
  active_application_count = query.filter(
      Application.status.in_(['Shortlisted', 'Interviewing'])
  ).count()
  updated_match_scores = False
  for application in applications_list:
    if not application.match or application.match.strip().lower() == "not assessed":
      match_score = calculate_application_match(
          application.resume_file, application.role
      )
      if match_score != "Not assessed":
        application.match = match_score
        updated_match_scores = True
  if updated_match_scores:
    db.session.commit()

  # Fetch distinct roles for the filter dropdown
  roles = [r[0] for r in db.session.query(Application.role.distinct()).all()]

  return render_template(
      'applications.html',
      applications=applications_list,
      pagination=pagination,
      pending_review_count=pending_review_count,
      active_application_count=active_application_count,
      roles=roles,
      search_query=search_query,
      status_filter=status_filter,
      role_filter=role_filter,
  )


# 3. Application Status Update Route
@app.route('/application/<app_id>/update', methods=['POST'])
@role_required('admin')
def update_application(app_id):
  application = db.get_or_404(Application, app_id)
  status = request.form.get('status')
  if status not in {
      'Pending Review',
      'Shortlisted',
      'Interviewing',
      'Rejected',
  }:
    abort(400, description='Choose a valid application status.')
  application.status = status
  db.session.commit()
  flash('Application status updated successfully!', 'success')
  return redirect(url_for('applications'))


# 4. Interview Scheduling Route
@app.route('/application/<app_id>/schedule', methods=['POST'])
@role_required('admin')
def schedule_application_interview(app_id):
  application = db.get_or_404(Application, app_id)
  application.interview_date = request.form.get('interview_date')
  application.interview_time = request.form.get('interview_time')
  application.status = 'Interviewing'
  db.session.commit()
  flash(
      f'Interview scheduled successfully for {application.name}!', 'success'
  )
  return redirect(url_for('applications'))


from datetime import date, datetime
from flask import abort, flash, redirect, render_template, request, session, url_for


# ==========================================
# ADMIN INTERVIEW MANAGEMENT ROUTES
# ==========================================

def get_interview_eligible_employees():
  return Employee.query.join(
      User, db.func.lower(Employee.email) == db.func.lower(User.email)
  ).filter(
      User.role == "employee"
  ).order_by(Employee.name).all()


def get_interview_assignee(employee_id):
  employee = Employee.query.join(
      User, db.func.lower(Employee.email) == db.func.lower(User.email)
  ).filter(
      Employee.id == employee_id,
      User.role == "employee"
  ).first()
  if employee is None:
    abort(400, description="Choose a valid employee account as the interviewer.")
  return employee

@app.route('/interviews')
@role_required('admin')
def interviews():
  page = request.args.get("page", 1, type=int)
  pagination = paginate_query(
      Interview.query.order_by(Interview.id.desc()), page
  )
  interviews_data = pagination["items"]

  # Today's date ISO prefix (e.g., '2026-10-16')
  today_prefix = date.today().isoformat()

  # Calculate metrics for admin top cards
  today_interview_count = Interview.query.filter(
      Interview.status == 'Scheduled', Interview.date.like(f'{today_prefix}%')
  ).count()

  completed_interview_count = Interview.query.filter_by(
      status='Completed'
  ).count()

  pending_feedback_count = Interview.query.filter_by(
      feedback_status='Pending'
  ).count()

  return render_template(
      'interviews.html',
      interviews=interviews_data,
      pagination=pagination,
      interviewers=get_interview_eligible_employees(),
      today_interview_count=today_interview_count,
      completed_interview_count=completed_interview_count,
      pending_feedback_count=pending_feedback_count,
  )


@app.route('/schedule-interview', methods=['POST'])
@role_required('admin')
def schedule_interview():
  candidate = request.form.get('candidate')
  role = request.form.get('role')
  interviewer = get_interview_assignee(request.form.get('interviewer_id'))
  raw_date = request.form.get('date')  # Format from datetime-local input

  # Format datetime for clear UI reading
  formatted_date = raw_date
  try:
    dt_obj = datetime.strptime(raw_date, '%Y-%m-%dT%H:%M')
    formatted_date = dt_obj.strftime('%d %b %Y %I:%M %p')
  except Exception:
    pass

  new_interview = Interview(
      candidate=candidate,
      role=role,
      interviewer=interviewer.name,
      date=formatted_date,
      interview_type=request.form.get('interview_type', 'Online'),
      status='Scheduled',
      feedback_status='Pending',
  )

  db.session.add(new_interview)
  db.session.commit()

  flash(f'Interview scheduled successfully for {candidate}!', 'success')
  return redirect(url_for('interviews'))


@app.route('/interview/update/<int:interview_id>', methods=['POST'])
@role_required('admin')
def update_interview(interview_id):
  interview = db.get_or_404(Interview, interview_id)

  interview.candidate = request.form.get('candidate', interview.candidate)
  interview.role = request.form.get('role', interview.role)
  interviewer = get_interview_assignee(request.form.get('interviewer_id'))
  interview.interviewer = interviewer.name
  interview.status = request.form.get('status', interview.status)

  db.session.commit()
  flash('Interview record updated successfully!', 'success')
  return redirect(url_for('interviews'))


@app.route('/interview/delete/<int:interview_id>', methods=['POST'])
@role_required('admin')
def delete_interview(interview_id):
  interview = db.get_or_404(Interview, interview_id)

  db.session.delete(interview)
  db.session.commit()

  flash('Interview session removed.', 'info')
  return redirect(url_for('interviews'))


# --- FEATURE ACTION ROUTES (FIXES BUILDERRORS) ---
@app.route("/add-employee", methods=["GET", "POST"])
@role_required("admin")
def add_employee():
    if request.method == "GET":
        return render_template("add_employee.html")

    name = request.form.get("name")
    role = request.form.get("role")
    dept = request.form.get("dept")
    email = (request.form.get("email") or "").strip().lower()
    phone = request.form.get("phone")
    joining = request.form.get("joining") or "Jan 2026"
    if not email:
        abort(400, description="An employee email address is required.")
    if Employee.query.filter(db.func.lower(Employee.email) == email).first():
        abort(409, description="An employee already uses that email address.")

    new_emp = Employee(
        id=next_employee_id(),
        name=(name or "New Employee").strip(),
        role=(role or "Staff Member").strip(),
        dept=(dept or "General").strip(),
        email=email,
        status=request.form.get("status") or "Active",
        avatar="".join(part[0] for part in (name or "NE").split()[:2]).upper(),
        phone=(phone or "+91 99999 99999").strip(),
        joining=joining
    )
    db.session.add(new_emp)
    db.session.commit()
    return redirect(url_for("employees"))


@app.route("/add-job", methods=["GET", "POST"])
@role_required("admin")
def add_job():
    if request.method == "GET":
        return render_template("add_job.html")

    title = request.form.get("title")
    dept = request.form.get("dept")
    location = request.form.get("location")
    job_type = request.form.get("type", "Full-Time")
    
    if title:
        new_job = Job(
            id=f"JOB-{Job.query.count() + 101:03d}",
            title=title,
            dept=dept or "General",
            location=location or "Remote",
            type=job_type,
            applicants=0,
            status="Active"
        )
        db.session.add(new_job)
        db.session.commit()
    return redirect(url_for("jobs"))


@app.route("/candidates/add", methods=["POST"])
@app.route("/add-candidate", methods=["POST"])
@role_required("admin")
def add_candidate():
    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    phone = (request.form.get("phone") or "").strip()
    role = (request.form.get("role") or "").strip()
    exp = request.form.get("exp", "2 Years")
    skills = request.form.get("skills", "Python, Flask")
    resume = request.files.get("resume")
    safe_filename = secure_filename(resume.filename or "") if resume else ""
    if not name or not email or not role or not resume or not safe_filename:
        abort(400, description="Provide candidate details and a PDF resume.")
    if not safe_filename.lower().endswith(".pdf"):
        abort(400, description="Candidate resumes must be PDF files.")

    stored_filename = f"{secrets.token_hex(8)}_{safe_filename}"
    resume.save(os.path.join(app.config["UPLOAD_FOLDER"], stored_filename))
    new_cand = Candidate(
        id=f"CAND-{Candidate.query.count() + 1:02d}",
        name=name,
        email=email,
        phone=phone or "+91 99999 99999",
        role=role,
        exp=exp,
        skills=skills,
        stage="Applied",
        match=calculate_ai_match(
            os.path.join(app.config["UPLOAD_FOLDER"], stored_filename), role
        ),
        summary="Newly added candidate profile.",
        resume_file=stored_filename,
    )
    db.session.add(new_cand)
    db.session.commit()
    return redirect(url_for("candidates"))


@app.route("/employee/dashboard")
@role_required("employee")
def emp_dashboard():
    user, employee = get_current_employee()
    if not employee:
        return redirect(url_for("login"))

    # 1. Dynamic Greeting & Current Date
    now = datetime.now()
    hour = now.hour
    if hour < 12:
        greeting = "Good Morning"
    elif 12 <= hour < 17:
        greeting = "Good Afternoon"
    else:
        greeting = "Good Evening"
        
    current_date = now.strftime("%a, %d %b %Y")  # e.g., "Mon, 12 Oct 2026"

    # 2. Leave and Attendance Queries
    my_leaves = LeaveRequest.query.filter_by(emp_id=employee.id).all()
    today_str = date.today().isoformat()
    today_attendance = Attendance.query.filter_by(emp_id=employee.id, date=today_str).first()

    approved_usage = get_leave_usage(my_leaves)
    total_approved = sum(approved_usage.values())
    available_usage = get_leave_usage(
        my_leaves,
        statuses=("Approved", "Pending", "Awaiting Manager Approval"),
    )
    leave_balances = get_leave_balances(available_usage)

    stats = {
        "today_checked_in": bool(today_attendance and today_attendance.check_in != "-"),
        "leave_balance": sum(leave_balances.values()),
        "total_approved_leaves": total_approved,
        "pending_requests": len([
            leave for leave in my_leaves
            if leave.status in {"Pending", "Awaiting Manager Approval"}
        ]),
    }

    # 3. Recent Activity Logs
    recent_activities = ActivityLog.query.filter_by(emp_id=employee.id)\
                                         .order_by(ActivityLog.id.desc())\
                                         .limit(5)\
                                         .all()

    return render_template(
        "emp_dashboard.html", 
        employee=employee, 
        stats=stats, 
        activities=recent_activities, 
        greeting=greeting,
        current_date=current_date,
        active_page="dashboard"
    )

@app.route("/employee/attendance", methods=["GET", "POST"])
@role_required("employee")
def emp_attendance():
    user, employee = get_current_employee()
    if not employee:
        return redirect(url_for("login"))

    today_str = date.today().isoformat()
    today_record = Attendance.query.filter_by(emp_id=employee.id, date=today_str).first()

    if request.method == "POST":
        action = request.form.get("action")
        if action not in {"check_in", "check_out"}:
            abort(400, description="Choose check-in or check-out.")
        now_time = datetime.now().strftime("%I:%M %p")

        if action == "check_in" and (
            today_record is None
            or today_record.check_in in (None, "", "-", "--")
        ):
            if today_record is None:
                today_record = Attendance(
                    id=f"ATT-{secrets.token_hex(3).upper()}",
                    emp_id=employee.id,
                    name=employee.name,
                    date=today_str,
                    check_out="-",
                )
                db.session.add(today_record)
            today_record.check_in = now_time
            today_record.status = "Present"
            log_activity(employee.id, f"Checked in at {now_time}")
            db.session.commit()

        elif (
            action == "check_out"
            and today_record
            and today_record.check_in not in (None, "", "-", "--")
            and today_record.check_out in (None, "", "-", "--")
        ):
            today_record.check_out = now_time
            log_activity(employee.id, f"Checked out at {now_time}")
            db.session.commit()

        return redirect(url_for("emp_attendance"))

    # --- Dynamic Calendar & Summary Logic ---
    now = date.today()
    year = request.args.get("year", now.year, type=int)
    month = request.args.get("month", now.month, type=int)
    if not 1 <= year <= 9999 or not 1 <= month <= 12:
        year, month = now.year, now.month

    cal_matrix = calendar.monthcalendar(year, month)
    month_name = calendar.month_name[month]
    previous_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    history = Attendance.query.filter_by(emp_id=employee.id).order_by(Attendance.date.desc()).all()

    attendance_map = {}
    present_count = 0
    absent_count = 0
    half_day_count = 0

    for att in history:
        try:
            att_date = datetime.strptime(att.date, "%Y-%m-%d")
        except ValueError:
            try:
                att_date = datetime.strptime(att.date, "%d %b %Y")
            except (TypeError, ValueError):
                continue

        if att_date.year != year or att_date.month != month:
            continue

        attendance_map[att_date.strftime("%Y-%m-%d")] = att.status
        if att.status in {"Present", "Late"}:
            present_count += 1
        elif att.status == "Absent":
            absent_count += 1
        elif att.status == "Half Day":
            half_day_count += 1

    summary = {
        "present": present_count,
        "absent": absent_count,
        "half_day": half_day_count,
        "working_days": present_count + absent_count + half_day_count
    }

    return render_template(
        "emp_attendance.html", 
        employee=employee, 
        today_record=today_record, 
        history=history,
        cal_matrix=cal_matrix,
        month_name=month_name,
        month_num=month,
        year=year,
        current_day=now.day if (year, month) == (now.year, now.month) else None,
        attendance_map=attendance_map,
        summary=summary,
        previous_month=previous_month,
        next_month=next_month,
        active_page="attendance"
    )
@app.route("/employee/leave-requests", methods=["GET", "POST"])
@role_required("employee")
def emp_leave_requests():
    user, employee = get_current_employee()
    if not employee:
        return redirect(url_for("login"))

    if request.method == "POST":
        validate_csrf_token()
        leave_type = (request.form.get("leave_type") or "").strip()
        start_date = (request.form.get("start_date") or "").strip()
        end_date = (request.form.get("end_date") or "").strip()
        reason = (request.form.get("reason") or "").strip()

        if leave_type not in LEAVE_ALLOWANCES:
            abort(400, description="Choose a valid leave type.")
        if not reason or len(reason) > 2000:
            abort(400, description="Enter a reason of no more than 2000 characters.")
        try:
            parsed_start = date.fromisoformat(start_date)
            parsed_end = date.fromisoformat(end_date)
        except ValueError:
            abort(400, description="Enter valid leave dates.")
        if parsed_end < parsed_start:
            abort(400, description="The end date must be on or after the start date.")

        new_leave = LeaveRequest(
            emp_id=employee.id,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            status="Pending",
            reason=reason
        )
        db.session.add(new_leave)
        log_activity(employee.id, f"Submitted {leave_type} request ({start_date} to {end_date})")
        db.session.commit()

        return redirect(url_for("emp_leave_requests"))

    # Fetch real leave requests from SQLite ordered by latest first
    leave_history = LeaveRequest.query.filter_by(emp_id=employee.id)\
                                      .order_by(LeaveRequest.id.desc())\
                                      .all()

    available_usage = get_leave_usage(
        leave_history,
        statuses=("Approved", "Pending", "Awaiting Manager Approval"),
    )
    leave_balances = get_leave_balances(available_usage)

    return render_template(
        "emp_leaves.html", 
        employee=employee, 
        leave_history=leave_history, 
        leave_balances=leave_balances,
        active_page="leave_requests",
        csrf_token=get_csrf_token(),
    )

# 1. View Assigned Interviews for Logged-in Employee (Interviewer View)
@app.route('/employee/interviews')
@app.route('/emp/interview')
@role_required("employee")
def employee_interviews():
    user, employee = get_current_employee()
    if not user:
        return redirect(url_for("login"))

    interviewer_names = {user.username.strip().lower()}
    if employee and employee.name:
        interviewer_names.add(employee.name.strip().lower())

    assigned_sessions = Interview.query.filter(
        db.func.lower(db.func.trim(Interview.interviewer)).in_(interviewer_names)
    ).all()
    
    return render_template(
        'emp_interview.html',
        interviews=assigned_sessions,
        user=user,
        employee=employee,
    )

# 2. Submit Scorecard & Feedback Route
@app.route('/emp/interview/<int:interview_id>/feedback', methods=['POST'])
@role_required("employee")
def submit_interview_feedback(interview_id):
    user, employee = get_current_employee()
    if not user:
        return redirect(url_for("login"))

    interview = Interview.query.get_or_404(interview_id)
    interviewer_names = {user.username.strip().lower()}
    if employee and employee.name:
        interviewer_names.add(employee.name.strip().lower())
    if (interview.interviewer or "").strip().lower() not in interviewer_names:
        abort(403)
    
    # Save scorecard ratings and feedback notes
    interview.skills_score = int(request.form.get('skills_score', 0))
    interview.problem_solving_score = int(request.form.get('problem_solving_score', 0))
    interview.communication_score = int(request.form.get('communication_score', 0))
    interview.role_fit_score = int(request.form.get('role_fit_score', 0))
    interview.feedback_notes = request.form.get('feedback_notes')
    
    interview.feedback_status = 'Submitted'
    interview.status = 'Completed'
    
    db.session.commit()
    flash('Interview scorecard and feedback submitted successfully!', 'success')
    return redirect(url_for('employee_interviews'))


@app.route("/employee/ai-assistant", methods=["GET", "POST"])
@role_required("employee")
def emp_ai_assistant():
    user, employee = get_current_employee()
    if not employee:
        return redirect(url_for("login"))

    attendance_records = Attendance.query.filter_by(emp_id=employee.id).all()
    attendance_totals = {
        "present": sum(1 for record in attendance_records if record.status in {"Present", "Late"}),
        "absent": sum(1 for record in attendance_records if record.status == "Absent"),
        "half_day": sum(1 for record in attendance_records if record.status == "Half Day"),
        "recorded": len(attendance_records),
    }

    hr_employee = Employee.query.filter(
        or_(
            Employee.role.ilike("%hr%"),
            Employee.dept.ilike("%human resources%"),
        )
    ).order_by(Employee.id.asc()).first()
    hr_phone = (
        os.environ.get("WORKFORCE_HR_PHONE", "").strip()
        or (hr_employee.phone.strip() if hr_employee and hr_employee.phone else "")
    )
    agent_phone = os.environ.get("WORKFORCE_HR_AGENT_PHONE", "").strip() or hr_phone

    def valid_call_number(phone_number):
        normalized_number = re.sub(r"[^\d+]", "", phone_number or "")
        return normalized_number if re.fullmatch(r"\+?\d{7,15}", normalized_number) else ""

    hr_tel = valid_call_number(hr_phone)
    agent_tel = valid_call_number(agent_phone)

    if request.method == "POST":
        raw_message = request.form.get("message", "").strip()
        if raw_message:
            # 1. Save user message to database chat history
            user_msg_db = ChatMessage(emp_id=employee.id, sender="user", message=raw_message)
            db.session.add(user_msg_db)
            db.session.commit()

            user_message = raw_message.lower()
            
            today_str = date.today().isoformat()
            today_attendance = Attendance.query.filter_by(emp_id=employee.id, date=today_str).first()
            my_leaves = LeaveRequest.query.filter_by(emp_id=employee.id).all()
            
            total_approved_casual = sum(
                (datetime.strptime(l.end_date, "%Y-%m-%d") - datetime.strptime(l.start_date, "%Y-%m-%d")).days + 1 
                for l in my_leaves if l.status == "Approved" and l.leave_type == "Casual Leave"
            )
            casual_balance = max(0, 12 - total_approved_casual)

            # Expanded Agent Intent Matching
            if "leave" in user_message or "balance" in user_message:
                bot_reply = f"Hello {employee.name}! You currently have {casual_balance} Casual Leaves remaining out of 12. You can apply for more anytime in the Leave Requests tab!"
            
            elif "attendance" in user_message or "check in" in user_message or "present" in user_message:
                if any(
                    phrase in user_message
                    for phrase in ("total", "how many", "overall", "history")
                ):
                    bot_reply = (
                        f"You have {attendance_totals['present']} attended days in total "
                        f"({attendance_totals['absent']} absent and "
                        f"{attendance_totals['half_day']} half days) across "
                        f"{attendance_totals['recorded']} recorded attendance days."
                    )
                elif today_attendance and today_attendance.check_in != "-":
                    checkout_status = f"and checked out at {today_attendance.check_out}" if today_attendance.check_out != "-" else "and you are currently still checked in."
                    bot_reply = f"Yes! You checked in today at {today_attendance.check_in} {checkout_status}."
                else:
                    bot_reply = f"You haven't checked in yet today, {employee.name}. Head over to the Attendance tab to clock in!"
            
            elif "hr" in user_message or "contact" in user_message or "phone" in user_message or "email" in user_message:
                if hr_tel:
                    bot_reply = f"You can call HR at {hr_phone}. Tap the number to call."
                else:
                    bot_reply = "An HR phone number has not been configured yet. Please ask your administrator to set WORKFORCE_HR_PHONE."
            
            elif "salary" in user_message or "payroll" in user_message or "pay" in user_message:
                bot_reply = "Payroll is processed on the last working day of every month. You can view your payment history and profile details in the My Profile tab."
            
            elif "location" in user_message or "office" in user_message or "address" in user_message:
                bot_reply = "Our corporate office is located at Tech Park, Block B, Jamshedpur."
            
            elif "policy" in user_message or "holiday" in user_message or "guideline" in user_message:
                bot_reply = "We offer 12 Casual Leaves, 8 Sick Leaves, 5 Privilege Leaves, and 2 Earned Leaves annually. Standard working hours are 9:00 AM to 6:00 PM."
            
            elif "hello" in user_message or "hi" in user_message or "hey" in user_message:
                bot_reply = f"Hello {employee.name}! 👋 How can I assist you with your HR needs today?"
            
            else:
                if agent_tel:
                    bot_reply = (
                        "I don't have a reliable answer for that yet. You can call an "
                        f"HR agent at {agent_phone}. Tap the number to call."
                    )
                else:
                    bot_reply = (
                        "I don't have a reliable answer for that yet, and no HR agent "
                        "phone number is configured. Please ask your administrator to "
                        "set WORKFORCE_HR_AGENT_PHONE."
                    )

            # 2. Save bot reply to database chat history
            bot_msg_db = ChatMessage(emp_id=employee.id, sender="bot", message=bot_reply)
            db.session.add(bot_msg_db)
            db.session.commit()

            log_activity(employee.id, f"Asked AI Assistant: '{raw_message[:30]}...'")
            
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return {"reply": bot_reply}

    # 3. Fetch chat history for this user to display on page load
    chat_history = ChatMessage.query.filter_by(emp_id=employee.id).order_by(ChatMessage.id.asc()).all()

    return render_template(
        "emp_ai_assistant.html",
        employee=employee,
        chat_history=chat_history,
        attendance_totals=attendance_totals,
        active_page="ai_assistant",
    )


@app.route("/employee/ai-assistant/clear", methods=["POST"])
@role_required("employee")
def clear_chat():
    _, employee = get_current_employee()
    if not employee:
        return redirect(url_for("login"))

    ChatMessage.query.filter_by(emp_id=employee.id).delete()
    db.session.commit()
    return redirect(url_for("emp_ai_assistant"))


def migrate_database_schema():
    inspector = inspect(db.engine)
    table_columns = {
        "user": {
            "name": "VARCHAR(150)",
        },
        "application": {
            "interview_date": "VARCHAR(50)",
            "interview_time": "VARCHAR(50)",
        },
        "interview": {
            "interview_type": "VARCHAR(50) DEFAULT 'Online'",
            "skills_score": "INTEGER DEFAULT 0",
            "problem_solving_score": "INTEGER DEFAULT 0",
            "communication_score": "INTEGER DEFAULT 0",
            "role_fit_score": "INTEGER DEFAULT 0",
            "feedback_notes": "TEXT",
            "feedback_status": "VARCHAR(50) DEFAULT 'Pending'",
        },
        "leave_request": {
            "manager_id": "VARCHAR(50)",
            "manager_name": "VARCHAR(150)",
            "manager_sent_at": "VARCHAR(100)",
        },
    }
    with db.engine.begin() as connection:
        for table_name, missing_columns in table_columns.items():
            if table_name not in inspector.get_table_names():
                continue
            existing_columns = {
                column["name"] for column in inspector.get_columns(table_name)
            }
            for column_name, column_type in missing_columns.items():
                if column_name in existing_columns:
                    continue
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} ADD COLUMN "
                        f"{column_name} {column_type}"
                    )
                )


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        migrate_database_schema()
        seed_database()
        print("Database initialized and seeded successfully!")
    app.run(debug=True)